"""Orkestrasi per file. Satu file rusak tidak pernah menghentikan batch (FR-1)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from contract_extractor.config import Settings
from contract_extractor.extractors.base import Extractor, ExtractorError
from contract_extractor.layout import PageText
from contract_extractor.models import FileResult, Level, PageKind
from contract_extractor.ocr.engine import OcrRunner
from contract_extractor.pdf.reader import DocumentRead, InputRejected, read_document, validate_pdf_file
from contract_extractor.pdf.render import PageRenderer
from contract_extractor.stages.extraction import verify_extraction
from contract_extractor.stages.filename import (
    FLAG_NONSTANDARD,
    ParsedFilename,
    compare_name,
    compare_period,
    normalize_name,
    parse_filename,
)
from contract_extractor.stages.masking import PAGE_SEPARATOR, MaskLeakError, mask_pages
from contract_extractor.stages.page_selection import candidate_pages, select_pages
from contract_extractor.stages.period_regex import find_period
from contract_extractor.stages.scoring import COMPONENT_FILENAME, decide, filename_level, ocr_level

log = logging.getLogger(__name__)

# pdfminer logs raw content-stream tokens at DEBUG, which would copy contract text (names, NIK) into logs.
for _pdf_logger in ("pdfminer", "pdfplumber"):
    logging.getLogger(_pdf_logger).setLevel(logging.WARNING)

FLAG_INPUT_REJECTED = "input_rejected"
FLAG_PROCESSING_ERROR = "processing_error"
FLAG_PIPELINE_INCOMPLETE = "pipeline_incomplete"
REASON_PAGE_SELECTION_AMBIGUOUS = "page_selection_ambiguous"
REASON_OCR_UNAVAILABLE = "ocr_unavailable"
REASON_OCR_LOW_CONFIDENCE = "ocr_low_confidence"
REASON_MASK_LEAK = "mask_leak"
REASON_EXTRACTOR_DISABLED = "extractor_disabled"
REASON_EXTRACTOR_ERROR = "extractor_error"

# Stages still to be built (Fase 3). Each counts as red until it lands, so nothing auto-passes.
PENDING_STAGES = ("signature",)

_NEEDS_OCR = (PageKind.SCAN, PageKind.HYBRID)


@dataclass
class _PagesRead:
    levels: dict[str, Level]
    p_id_pages: list[PageText] = field(default_factory=list)  # full-resolution text, in page order
    p_id_trusted: bool = False  # every P_id page came from a text layer or from OCR that passed quality


def file_id(path: Path) -> str:
    """Filenames carry employee names, so logs refer to files by a short hash only."""
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


def _add_reason(result: FileResult, reason: str) -> None:
    if reason not in result.log.alasan:
        result.log.alasan.append(reason)


def _select_and_read(
    path: Path, doc: DocumentRead, result: FileResult, employee_name: str, quality_name: str, ocr: OcrRunner | None
) -> _PagesRead:
    """Tahap 1 (page selection with rough OCR for scan/hybrid) and Tahap 2 (full OCR of P_id and P_ttd)."""
    components = result.log.komponen_halaman
    for page in doc.texts:
        components.setdefault(page.number, []).append("text_layer")

    to_ocr = [n for n in candidate_pages(doc.n_pages) if doc.kinds.get(n) in _NEEDS_OCR]
    if to_ocr and ocr is None:
        _add_reason(result, REASON_OCR_UNAVAILABLE)
    renderer = PageRenderer(path) if to_ocr and ocr is not None else None
    try:
        texts: list[PageText] = list(doc.texts)
        if renderer is not None and ocr is not None:
            for number in to_ocr:
                components.setdefault(number, []).append(f"{ocr.primary.name}@{ocr.scoring_dpi}dpi")
                rough = ocr.rough_text(renderer, number)
                if rough is not None:
                    texts.append(rough)

        selection = select_pages(texts, doc.n_pages, employee_name)
        result.log.p_id = selection.p_id
        result.log.p_ttd = selection.p_ttd
        result.log.halaman_sensitif = selection.sensitive_pages
        result.log.skor_halaman = {n: s.as_dict() for n, s in sorted(selection.scores.items())}
        if selection.ambiguous:
            # No cloud stage may run for this file; it goes straight to review.
            _add_reason(result, REASON_PAGE_SELECTION_AMBIGUOUS)
            return _PagesRead({"page_selection": selection.level, "ocr_p_id": Level.RED})

        native = {page.number: page for page in doc.texts}
        read_pages: dict[int, PageText] = {}
        passed: set[int] = set()
        engines: dict[int, str] = {}
        confidences: dict[int, float] = {}
        for number in sorted({*selection.p_id, selection.p_ttd}):
            if doc.kinds[number] is PageKind.NATIVE:
                engines[number], confidences[number] = "text_layer", 1.0
                read_pages[number] = native[number]
                passed.add(number)
            elif renderer is None or ocr is None:
                engines[number], confidences[number] = "none", 0.0
            else:
                outcome = ocr.read(renderer, number, quality_name if number in selection.p_id else "")
                engines[number] = outcome.engine or "none"
                confidences[number] = outcome.mean_conf
                components.setdefault(number, []).append(f"{engines[number]}@{ocr.read_dpi}dpi")
                result.log.ocr_conf[number] = round(outcome.mean_conf, 3)
                if outcome.page is not None:
                    read_pages[number] = outcome.page
                if outcome.passed:
                    passed.add(number)
                else:
                    _add_reason(result, REASON_OCR_LOW_CONFIDENCE)

        result.log.ocr_p_id = engines[selection.p_id[0]]
        result.log.ocr_p_ttd = engines[selection.p_ttd]  # type: ignore[index]
        p_id_conf = min(confidences[n] for n in selection.p_id)
        return _PagesRead(
            {"page_selection": selection.level, "ocr_p_id": ocr_level(p_id_conf)},
            [read_pages[n] for n in selection.p_id if n in read_pages],
            all(n in passed for n in selection.p_id),
        )
    finally:
        if renderer is not None:
            renderer.close()


def _prepare_extraction(result: FileResult, p_id_pages: list[PageText], max_chars_per_page: int) -> str | None:
    """Tahap 3, local part: regex period from the raw P_id text, then the masked payload for the extractor.

    Returns the only text allowed to leave the machine, or None when masking fails closed. Nothing is sent here.
    """
    if not p_id_pages:
        return None
    result.log.periode_regex = find_period(PAGE_SEPARATOR.join(page.text for page in p_id_pages))
    try:
        masked = mask_pages([page.text for page in p_id_pages], max_chars_per_page)
    except MaskLeakError:
        _add_reason(result, REASON_MASK_LEAK)
        return None
    result.log.pola_dimask = masked.total
    return masked.text


def _filename_level(result: FileResult, parsed: ParsedFilename) -> Level:
    """Tahap 5a: compare the extracted name and period with the filename `Nama Karyawan - Masa Kontrak`."""
    if FLAG_NONSTANDARD in parsed.flags:
        return Level.YELLOW  # the whole stem is treated as the name, so a comparison would mean nothing
    name_match = compare_name(parsed.name, result.nama_karyawan or "")
    period_match = compare_period(parsed, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan)
    result.log.validasi_nama_file = {"nama": name_match.value}
    if period_match is not None:
        result.log.validasi_nama_file["periode"] = period_match.value
    return filename_level(name_match, period_match, parsed.flags)


def _extract(
    result: FileResult, payload: str | None, trusted: bool, parsed: ParsedFilename, extractor: Extractor | None
) -> dict[str, Level]:
    """Tahap 3, cloud part: send the masked payload, verify the answer locally, then validate the filename."""
    if payload is None:  # selection ambiguous, mask_leak, or no P_id text: nothing to send
        return {"extraction": Level.RED}
    if extractor is None:
        _add_reason(result, REASON_EXTRACTOR_DISABLED)
        return {"extraction": Level.RED}
    if not trusted:
        # Low-quality OCR can split a NIK into short digit runs that masking misses, so the text stays local.
        return {"extraction": Level.RED}

    result.log.extractor = extractor.name
    result.log.teks_keluar_karakter = len(payload)  # counted on send: a failed call may still have delivered it
    for number in result.log.p_id:
        result.log.komponen_halaman.setdefault(number, []).append(f"{extractor.name}:masked")
    try:
        extraction = extractor.extract(payload)
    except ExtractorError as exc:
        _add_reason(result, f"{REASON_EXTRACTOR_ERROR}:{exc.kind}")
        return {"extraction": Level.RED}

    verified = verify_extraction(extraction, payload, result.log.periode_regex)
    result.nama_karyawan = verified.name
    result.tanggal_mulai, result.tanggal_selesai = verified.start, verified.end
    result.durasi_bulan = verified.duration_months
    result.confidence.update(verified.confidence)
    for reason in verified.reasons:
        _add_reason(result, reason)

    levels = {"extraction": verified.level}
    if verified.name:  # without an extracted name there is nothing to compare, and extraction is red already
        levels[COMPONENT_FILENAME] = _filename_level(result, parsed)
    return levels


def process_file(
    path: Path, settings: Settings, ocr: OcrRunner | None = None, extractor: Extractor | None = None
) -> FileResult:
    """`ocr=None` means local OCR is not available; scan and hybrid pages are then flagged, not guessed.
    `extractor=None` means no text leaves the machine; extraction then counts as red."""
    result = FileResult(file=path.name)
    fid = file_id(path)
    try:
        validate_pdf_file(path, settings.input.max_file_mb * 1024 * 1024)
        doc = read_document(path, settings.input.max_pages, settings.page_selection.economy_mode)
        result.log.n_halaman = doc.n_pages
        result.log.jenis_halaman = doc.kinds

        parsed = parse_filename(path.name)
        result.flags.extend(parsed.flags)
        # A nonstandard filename's "name" may include period text, so it cannot vouch for OCR quality.
        quality_name = "" if FLAG_NONSTANDARD in parsed.flags else normalize_name(parsed.name)

        pages = _select_and_read(path, doc, result, parsed.name, quality_name, ocr)
        levels = dict(pages.levels)
        payload = _prepare_extraction(result, pages.p_id_pages, settings.extractor.max_chars_per_page)
        levels.update(_extract(result, payload, pages.p_id_trusted, parsed, extractor))

        levels.update({stage: Level.RED for stage in PENDING_STAGES})
        result.log.alasan.extend(f"{stage}_not_implemented" for stage in PENDING_STAGES)
        result.flags.append(FLAG_PIPELINE_INCOMPLETE)
        result.keputusan, extra_flags = decide(levels)
        result.flags.extend(extra_flags)
    except InputRejected as exc:
        result.flags.append(FLAG_INPUT_REJECTED)
        result.log.alasan.append(exc.reason)
        log.warning("file %s rejected: %s", fid, exc.reason)
    except Exception as exc:  # FR-1: isolate every per-file failure; record the type only, never the message
        result.flags.append(FLAG_PROCESSING_ERROR)
        result.log.alasan.append(f"error:{type(exc).__name__}")
        log.error("file %s failed: %s", fid, type(exc).__name__)
    return result


def collect_inputs(input_path: Path, recursive: bool = False) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in input_path.glob(pattern) if p.suffix.lower() == ".pdf" and p.is_file() and not p.is_symlink()
    )


def process_path(
    input_path: Path,
    settings: Settings,
    recursive: bool = False,
    ocr: OcrRunner | None = None,
    extractor: Extractor | None = None,
) -> list[FileResult]:
    files = collect_inputs(input_path, recursive)
    log.info("processing %d file(s)", len(files))
    return [process_file(p, settings, ocr, extractor) for p in files]
