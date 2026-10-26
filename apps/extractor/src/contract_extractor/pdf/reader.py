"""Validasi file input dan pembacaan halaman lokal. PDF selalu dianggap input tidak tepercaya."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from contract_extractor.layout import PageText
from contract_extractor.models import PageKind, Word
from contract_extractor.stages.page_classify import PageStats, classify_page
from contract_extractor.stages.page_selection import candidate_pages

_PDF_MAGIC = b"%PDF-"
_HEADER_SCAN_BYTES = 1024


class InputRejected(Exception):
    """A file that fails validation. `reason` is a fixed code, never file content or path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DocumentRead:
    n_pages: int
    kinds: dict[int, PageKind] = field(default_factory=dict)
    texts: list[PageText] = field(default_factory=list)  # native pages only; scan/hybrid need OCR


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


def _page_stats(page: pdfplumber.page.Page, number: int) -> PageStats:
    area = float(page.width * page.height) or 1.0
    coverage = max(
        ((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) / area for im in page.images),
        default=0.0,
    )
    return PageStats(number=number, n_chars=len(page.chars), max_image_coverage=min(coverage, 1.0))


def _native_text(page: pdfplumber.page.Page, number: int) -> PageText:
    words = tuple(
        Word(text=w["text"], bbox=(w["x0"], w["top"], w["x1"], w["bottom"]), conf=1.0)
        for w in page.extract_words()
    )
    return PageText(number=number, width=float(page.width), height=float(page.height), words=words, source="text_layer")


def read_document(path: Path, max_pages: int, economy_mode: bool = False) -> DocumentRead:
    """Classify pages and read native text layers. Economy mode never opens middle pages."""
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        if n_pages > max_pages:
            raise InputRejected("too_many_pages")
        numbers = candidate_pages(n_pages) if economy_mode else list(range(1, n_pages + 1))

        doc = DocumentRead(n_pages=n_pages)
        for number in numbers:
            page = pdf.pages[number - 1]
            kind = classify_page(_page_stats(page, number))
            doc.kinds[number] = kind
            if kind is PageKind.NATIVE:
                doc.texts.append(_native_text(page, number))
            page.close()
        return doc
