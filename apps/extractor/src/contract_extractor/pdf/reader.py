"""Validasi file input dan statistik halaman lokal. PDF selalu dianggap input tidak tepercaya."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from contract_extractor.stages.page_classify import PageStats

_PDF_MAGIC = b"%PDF-"
_HEADER_SCAN_BYTES = 1024


class InputRejected(Exception):
    """A file that fails validation. `reason` is a fixed code, never file content or path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_pdf_file(path: Path, max_bytes: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise InputRejected("not_a_regular_file")
    if path.suffix.lower() != ".pdf":
        raise InputRejected("not_pdf_extension")
    size = path.stat().st_size
    if size == 0 or size > max_bytes:
        raise InputRejected("size_out_of_range")
    with path.open("rb") as fh:
        if _PDF_MAGIC not in fh.read(_HEADER_SCAN_BYTES):
            raise InputRejected("not_pdf_magic")


def read_page_stats(path: Path, max_pages: int) -> list[PageStats]:
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > max_pages:
            raise InputRejected("too_many_pages")
        stats = []
        for number, page in enumerate(pdf.pages, start=1):
            area = float(page.width * page.height) or 1.0
            coverage = max(
                ((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) / area for im in page.images),
                default=0.0,
            )
            stats.append(PageStats(number=number, n_chars=len(page.chars), max_image_coverage=min(coverage, 1.0)))
            page.close()
        return stats
