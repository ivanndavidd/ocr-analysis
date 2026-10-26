"""Orkestrasi per file. Satu file rusak tidak pernah menghentikan batch (FR-1)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from contract_extractor.config import Settings
from contract_extractor.extractors.base import Extractor, ExtractorError
from contract_extractor.layout import PageText
from contract_extractor.models import Decision, FileResult, Level, PageKind, SignatureLog, SignatureStatus
from contract_extractor.ocr.engine import OcrRunner
from contract_extractor.ocr.preprocess import straighten
from contract_extractor.pdf.objects import read_page_objects
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
from contract_extractor.stages.scoring import COMPONENT_FILENAME, decide, filename_level, ocr_level, signature_level
from contract_extractor.stages.signature import detect_signature, employee_labels

log = logging.getLogger(__name__)

# pdfminer logs raw content-stream tokens at DEBUG, which would copy contract text (names, NIK) into logs.
for _pdf_logger in ("pdfminer", "pdfplumber"):
    logging.getLogger(_pdf_logger).setLevel(logging.WARNING)

FLAG_INPUT_REJECTED = "input_rejected"
FLAG_PROCESSING_ERROR = "processing_error"
FLAG_AUTO_DISABLED = "auto_disabled"
REASON_PAGE_SELECTION_AMBIGUOUS = "page_selection_ambiguous"
REASON_OCR_UNAVAILABLE = "ocr_unavailable"
REASON_OCR_LOW_CONFIDENCE = "ocr_low_confidence"
REASON_MASK_LEAK = "mask_leak"
REASON_EXTRACTOR_DISABLED = "extractor_disabled"
REASON_EXTRACTOR_ERROR = "extractor_error"
REASON_SIGNATURE_PAGE_UNREADABLE = "signature_page_unreadable"
REASON_SIGNATURE_ANCHOR_NOT_FOUND = "signature_anchor_not_found"
REASON_SIGNATURE_UNCERTAIN = "signature_uncertain"

SIGNATURE_DPI = 300  # native pages; scans reuse the OCR read resolution so word coordinates line up

_NEEDS_OCR = (PageKind.SCAN, PageKind.HYBRID)


@dataclass
class _PagesRead:
    levels: dict[str, Level]
    p_id_pages: list[PageText] = field(default_factory=list)  # full-resolution text, in page order
    p_id_trusted: bool = False  # every P_id page came from a text layer or from OCR that passed quality
    p_ttd_page: PageText | None = None  # full-resolution text of the signature page


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
            read_pages.get(selection.p_ttd),  # type: ignore[arg-type]
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


def _signature(
    path: Path, doc: DocumentRead, result: FileResult, pages: _PagesRead, parsed: ParsedFilename, ocr: OcrRunner | None
) -> Level:
    """Tahap 4, fully local: the signature page and its crops never leave this machine."""
    page = pages.p_ttd_page
    number = result.log.p_ttd
    if page is None or number is None:
        result.status_ttd = SignatureStatus.UNCERTAIN
        if REASON_PAGE_SELECTION_AMBIGUOUS not in result.log.alasan:  # that reason already explains it
            _add_reason(result, REASON_SIGNATURE_PAGE_UNREADABLE)
        return Level.RED

    native = doc.kinds[number] is PageKind.NATIVE
    dpi = SIGNATURE_DPI if native or ocr is None else ocr.read_dpi
    with PageRenderer(path) as renderer:
        image = renderer.render_gray(number, dpi)
    if not native:
        image, _ = straighten(image)  # the same transform OCR saw, so its word boxes line up with these pixels
    result.log.komponen_halaman.setdefault(number, []).append(f"signature@{dpi}dpi")

    # The verified extracted name anchors the ROI; a standard filename's name is the fallback.
    name = result.nama_karyawan or (None if FLAG_NONSTANDARD in parsed.flags else parsed.name) or None
    labels = employee_labels(PAGE_SEPARATOR.join(p.text for p in pages.p_id_pages))
    found = detect_signature(page, native, image, read_page_objects(path, number), name, labels)

    result.status_ttd = found.status
    result.confidence["ttd"] = found.confidence
    result.log.tanda_tangan = SignatureLog(
        sumber=found.source,
        jangkar=found.anchor,
        roi=tuple(round(v, 4) for v in found.roi) if found.roi else None,  # type: ignore[arg-type]
        rasio_tinta=round(found.features.ink_ratio, 4) if found.features else None,
        komponen_tertinggi=round(found.features.tallest, 2) if found.features else None,
        anotasi_di_luar_roi=found.annotations_outside_roi,
        kandidat_roi=found.candidates,
    )
    if not found.anchor_found:
        _add_reason(result, REASON_SIGNATURE_ANCHOR_NOT_FOUND)
    elif found.status is SignatureStatus.UNCERTAIN:
        _add_reason(result, REASON_SIGNATURE_UNCERTAIN)
    return signature_level(found.status, found.anchor_found, found.clear_margin)


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
        levels["signature"] = _signature(path, doc, result, pages, parsed, ocr)

        result.keputusan, extra_flags = decide(levels)
        result.flags.extend(extra_flags)
        if result.keputusan is Decision.AUTO and not settings.decision.allow_auto:
            result.keputusan = Decision.REVIEW
            result.flags.append(FLAG_AUTO_DISABLED)
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
