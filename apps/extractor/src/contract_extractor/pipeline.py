"""Orkestrasi per file. Satu file rusak tidak pernah menghentikan batch (FR-1)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from contract_extractor.config import Settings
from contract_extractor.models import FileResult
from contract_extractor.pdf.reader import InputRejected, read_page_stats, validate_pdf_file
from contract_extractor.stages.filename import parse_filename
from contract_extractor.stages.page_classify import classify_page
from contract_extractor.stages.scoring import decide

log = logging.getLogger(__name__)

# pdfminer logs raw content-stream tokens at DEBUG, which would copy contract text (names, NIK) into logs.
for _pdf_logger in ("pdfminer", "pdfplumber"):
    logging.getLogger(_pdf_logger).setLevel(logging.WARNING)

FLAG_INPUT_REJECTED = "input_rejected"
FLAG_PROCESSING_ERROR = "processing_error"
FLAG_PIPELINE_INCOMPLETE = "pipeline_incomplete"

# Stages still to be built (Fase 1-3). Shrink this as each one lands.
PENDING_STAGES = ("page_selection", "ocr", "masking_extraction", "signature")


def file_id(path: Path) -> str:
    """Filenames carry employee names, so logs refer to files by a short hash only."""
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


def process_file(path: Path, settings: Settings) -> FileResult:
    result = FileResult(file=path.name)
    fid = file_id(path)
    try:
        validate_pdf_file(path, settings.input.max_file_mb * 1024 * 1024)
        stats = read_page_stats(path, settings.input.max_pages)

        result.log.n_halaman = len(stats)
        result.log.jenis_halaman = {s.number: classify_page(s) for s in stats}

        parsed = parse_filename(path.name)
        result.flags.extend(parsed.flags)

        result.log.alasan.extend(f"{stage}_not_implemented" for stage in PENDING_STAGES)
        result.flags.append(FLAG_PIPELINE_INCOMPLETE)
        result.keputusan, extra_flags = decide({})
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


def process_path(input_path: Path, settings: Settings, recursive: bool = False) -> list[FileResult]:
    files = collect_inputs(input_path, recursive)
    log.info("processing %d file(s)", len(files))
    return [process_file(p, settings) for p in files]
