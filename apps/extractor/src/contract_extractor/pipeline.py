"""Orkestrasi per file. Satu file rusak tidak pernah menghentikan batch (FR-1)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from contract_extractor.config import Settings
from contract_extractor.layout import PageText
from contract_extractor.models import FileResult, Level, PageKind
from contract_extractor.ocr.engine import OcrRunner
from contract_extractor.pdf.reader import DocumentRead, InputRejected, read_document, validate_pdf_file
from contract_extractor.pdf.render import PageRenderer
from contract_extractor.stages.filename import FLAG_NONSTANDARD, normalize_name, parse_filename
from contract_extractor.stages.page_selection import candidate_pages, select_pages
from contract_extractor.stages.scoring import decide, ocr_level

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

# Stages still to be built (Fase 2-3). Each counts as red until it lands, so nothing auto-passes.
PENDING_STAGES = ("masking_extraction", "signature")

_NEEDS_OCR = (PageKind.SCAN, PageKind.HYBRID)


def file_id(path: Path) -> str:
    """Filenames carry employee names, so logs refer to files by a short hash only."""
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


def _add_reason(result: FileResult, reason: str) -> None:
    if reason not in result.log.alasan:
        result.log.alasan.append(reason)


def _select_and_read(
    path: Path, doc: DocumentRead, result: FileResult, employee_name: str, quality_name: str, ocr: OcrRunner | None
) -> dict[str, Level]:
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
            return {"page_selection": selection.level, "ocr_p_id": Level.RED}

        engines: dict[int, str] = {}
        confidences: dict[int, float] = {}
        for number in sorted({*selection.p_id, selection.p_ttd}):
            if doc.kinds[number] is PageKind.NATIVE:
                engines[number], confidences[number] = "text_layer", 1.0
            elif renderer is None or ocr is None:
                engines[number], confidences[number] = "none", 0.0
            else:
                outcome = ocr.read(renderer, number, quality_name if number in selection.p_id else "")
                engines[number] = outcome.engine or "none"
                confidences[number] = outcome.mean_conf
                components.setdefault(number, []).append(f"{engines[number]}@{ocr.read_dpi}dpi")
                result.log.ocr_conf[number] = round(outcome.mean_conf, 3)
                if not outcome.passed:
                    _add_reason(result, REASON_OCR_LOW_CONFIDENCE)

        result.log.ocr_p_id = engines[selection.p_id[0]]
        result.log.ocr_p_ttd = engines[selection.p_ttd]  # type: ignore[index]
        p_id_conf = min(confidences[n] for n in selection.p_id)
        return {"page_selection": selection.level, "ocr_p_id": ocr_level(p_id_conf)}
    finally:
        if renderer is not None:
            renderer.close()


def process_file(path: Path, settings: Settings, ocr: OcrRunner | None = None) -> FileResult:
    """`ocr=None` means local OCR is not available; scan and hybrid pages are then flagged, not guessed."""
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

        levels = _select_and_read(path, doc, result, parsed.name, quality_name, ocr)
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
    input_path: Path, settings: Settings, recursive: bool = False, ocr: OcrRunner | None = None
) -> list[FileResult]:
    files = collect_inputs(input_path, recursive)
    log.info("processing %d file(s)", len(files))
    return [process_file(p, settings, ocr) for p in files]
