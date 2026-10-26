"""Tahap 2a: klasifikasi halaman native, scan, hybrid, unclassified, atau kosong (Bagian 9 PRD).

Format standar adalah format sampel: PDF langsung dari Word, atau scan satu gambar penuh per halaman. Halaman lain
tetap dibaca lewat OCR, tetapi diberi kode temuan format yang menjadi catatan untuk vendor dan mengirim file ke review.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from contract_extractor.models import PageKind

MIN_TEXT_CHARS = 50
DOMINANT_IMAGE_COVERAGE = 0.70
# Total image area. A photo of an A4 page pasted inside Word's default margins covers 57-63%; signature images
# and logos on the native sample pages stay under 2%.
LARGE_IMAGE_COVERAGE = 0.40
# Characters outside the header and footer bands. Above this, the text layer is the page's own body text, so a
# large image under it is a watermark or letterhead, and a page-sized image under it is a searchable scan.
BODY_TEXT_CHARS = 200
HEADER_FOOTER_BAND = 0.10  # Word headers and footers sit in the top and bottom tenth of the page
VECTOR_TEXT_MIN_SEGMENTS = 1000  # text converted to outlines; the vector company signature in the samples has 79

ISSUE_SCAN_WITH_WORD_TEXT = "scan_with_word_text"
ISSUE_PHOTO_IN_DOCUMENT = "photo_in_document"
ISSUE_SPLIT_IMAGES = "split_images"
ISSUE_VECTOR_TEXT = "vector_text"
ISSUE_IMAGE_WITHOUT_TEXT = "image_without_text"

_VENDOR_NOTES = {
    ISSUE_SCAN_WITH_WORD_TEXT: "foto atau scan ditempel ke dokumen Word bersama header/footer",
    ISSUE_PHOTO_IN_DOCUMENT: "foto atau scan ditempel ke dokumen Word, tidak satu halaman penuh",
    ISSUE_SPLIT_IMAGES: "halaman tersusun dari beberapa potongan gambar",
    ISSUE_VECTOR_TEXT: "teks berupa garis vektor sehingga tidak bisa dipilih atau disalin",
    ISSUE_IMAGE_WITHOUT_TEXT: "halaman hanya berisi gambar kecil tanpa teks",
}
VENDOR_REQUEST = "Mohon kirim ulang sebagai PDF langsung dari Word, atau scan satu gambar penuh per halaman."


@dataclass(frozen=True)
class PageStats:
    number: int  # 1-based
    n_chars: int
    max_image_coverage: float  # largest single image area / page area
    total_image_coverage: float = 0.0  # summed image area / page area, capped at 1
    n_images: int = 0
    body_chars: int = 0  # characters outside the header and footer bands
    vector_segments: int = 0  # line and curve segments of all vector paths


@dataclass(frozen=True)
class PageClass:
    kind: PageKind
    issue: str | None = None  # None: the page has a standard format


def classify_page(stats: PageStats) -> PageClass:
    """Text-bbox coverage is deliberately unused: native sample pages cover only 8-37%."""
    has_body_text = stats.body_chars >= BODY_TEXT_CHARS
    if stats.max_image_coverage > DOMINANT_IMAGE_COVERAGE:
        if stats.n_chars < MIN_TEXT_CHARS or has_body_text:
            return PageClass(PageKind.SCAN)
        return PageClass(PageKind.HYBRID, ISSUE_SCAN_WITH_WORD_TEXT)
    if stats.total_image_coverage >= LARGE_IMAGE_COVERAGE and not has_body_text:
        if stats.max_image_coverage >= LARGE_IMAGE_COVERAGE:
            return PageClass(PageKind.UNCLASSIFIED, ISSUE_PHOTO_IN_DOCUMENT)
        return PageClass(PageKind.UNCLASSIFIED, ISSUE_SPLIT_IMAGES)
    if stats.n_chars >= MIN_TEXT_CHARS:
        return PageClass(PageKind.NATIVE)
    if stats.vector_segments >= VECTOR_TEXT_MIN_SEGMENTS:
        return PageClass(PageKind.UNCLASSIFIED, ISSUE_VECTOR_TEXT)
    if stats.n_chars:
        return PageClass(PageKind.NATIVE)  # short real text, e.g. a signature page with only labels and names
    if stats.n_images:
        return PageClass(PageKind.UNCLASSIFIED, ISSUE_IMAGE_WITHOUT_TEXT)
    return PageClass(PageKind.BLANK)


def vendor_note(issues: Mapping[int, str]) -> str | None:
    """One note per file, pages grouped by issue in page order, e.g. `Halaman 1, 2: ... Mohon kirim ulang ...`."""
    if not issues:
        return None
    pages_by_issue: dict[str, list[int]] = {}
    for number, issue in sorted(issues.items()):
        pages_by_issue.setdefault(issue, []).append(number)
    parts = [f"Halaman {', '.join(map(str, pages))}: {_VENDOR_NOTES[issue]}." for issue, pages in pages_by_issue.items()]
    return " ".join([*parts, VENDOR_REQUEST])
