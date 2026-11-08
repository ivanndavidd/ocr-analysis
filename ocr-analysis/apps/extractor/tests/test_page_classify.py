import numpy as np
import pytest
from helpers import PAGE_H, PAGE_W, WORD_FOOTER, WORD_MARGIN, PageExtras, make_pdf

from contract_extractor.models import PageKind
from contract_extractor.pdf.reader import read_document
from contract_extractor.stages.page_classify import (
    ISSUE_IMAGE_WITHOUT_TEXT,
    ISSUE_PHOTO_IN_DOCUMENT,
    ISSUE_SCAN_WITH_WORD_TEXT,
    ISSUE_SPLIT_IMAGES,
    ISSUE_VECTOR_TEXT,
    VENDOR_REQUEST,
    PageClass,
    PageStats,
    classify_page,
    vendor_note,
)

NATIVE, SCAN, HYBRID = PageClass(PageKind.NATIVE), PageClass(PageKind.SCAN), PageClass(PageKind.HYBRID, ISSUE_SCAN_WITH_WORD_TEXT)
BLANK = PageClass(PageKind.BLANK)


def unclassified(issue: str) -> PageClass:
    return PageClass(PageKind.UNCLASSIFIED, issue)


def image(coverage: float, total: float | None = None, n: int = 1) -> dict:
    return {"max_image_coverage": coverage, "total_image_coverage": coverage if total is None else total, "n_images": n}


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        ({"n_chars": 3000, "body_chars": 2900}, NATIVE),
        ({"n_chars": 50, "body_chars": 50, **image(0.01)}, NATIVE),  # sparse signature page with a small signature image
        ({"n_chars": 38, "body_chars": 38}, NATIVE),  # signature page with only labels and names
        ({"n_chars": 38, "body_chars": 38, "vector_segments": 79}, NATIVE),  # ... and the samples' vector company signature
        ({**image(0.99)}, SCAN),
        ({"n_chars": 49, **image(0.71)}, SCAN),
        ({"n_chars": 2500, "body_chars": 2400, **image(0.99)}, SCAN),  # searchable scan: an OCR text layer over the body
        ({"n_chars": 120, "body_chars": 10, **image(0.95)}, HYBRID),  # a page-sized photo under a Word header and footer
        ({**image(0.63)}, unclassified(ISSUE_PHOTO_IN_DOCUMENT)),  # photo pasted inside Word's default margins
        ({"n_chars": 59, **image(0.63)}, unclassified(ISSUE_PHOTO_IN_DOCUMENT)),  # ... with a Word footer; was native
        ({"n_chars": 3000, "body_chars": 2900, **image(0.50)}, NATIVE),  # a watermark under real body text
        ({"n_chars": 3000, "body_chars": 2900, **image(0.70)}, NATIVE),  # 70% is not dominant; must exceed it
        ({**image(0.15, total=0.90, n=6)}, unclassified(ISSUE_SPLIT_IMAGES)),
        ({"n_chars": 12, "vector_segments": 5000}, unclassified(ISSUE_VECTOR_TEXT)),
        ({**image(0.05)}, unclassified(ISSUE_IMAGE_WITHOUT_TEXT)),
        ({}, BLANK),
        ({"vector_segments": 4}, BLANK),  # an empty page with a border
    ],
)
def test_classify_page(stats, expected):
    assert classify_page(PageStats(number=1, **{"n_chars": 0, "max_image_coverage": 0.0, **stats})) == expected


def test_vendor_note_groups_pages_by_issue_in_page_order():
    note = vendor_note({3: ISSUE_VECTOR_TEXT, 1: ISSUE_PHOTO_IN_DOCUMENT, 2: ISSUE_PHOTO_IN_DOCUMENT})
    assert note == (
        "Halaman 1, 2: foto atau scan ditempel ke dokumen Word, tidak satu halaman penuh. "
        "Halaman 3: teks berupa garis vektor sehingga tidak bisa dipilih atau disalin. " + VENDOR_REQUEST
    )


def test_standard_files_get_no_vendor_note():
    assert vendor_note({}) is None


def test_read_document_reports_every_non_standard_page(tmp_path):
    photo = np.full((640, 460), 200, np.uint8)
    footer = ("layout", [(WORD_MARGIN, 760.0, WORD_FOOTER)])
    in_margins = (WORD_MARGIN, WORD_MARGIN, PAGE_W - 2 * WORD_MARGIN, PAGE_H - 2 * WORD_MARGIN)
    narrow = (36.0, 36.0, PAGE_W - 72, PAGE_H - 72)
    strips = [(photo, (0.0, i * PAGE_H / 6, PAGE_W, PAGE_H / 6)) for i in range(6)]
    outlines = "\n".join(f"{50 + (i % 100) * 5} {700 - (i // 100) * 12} m {53 + (i % 100) * 5} {708 - (i // 100) * 12} l S" for i in range(1200))
    signature_only = [(80, 300, "PIHAK PERTAMA"), (380, 300, "PIHAK KEDUA"), (80, 400, "Andi W."), (380, 400, "Budi S.")]
    pdf = make_pdf(
        tmp_path / "mixed.pdf",
        [
            ("blank", ""),  # 1 photo pasted inside Word's margins
            footer,  # 2 the same with a footer
            ("image", photo),  # 3 full-page scan
            ("layout", signature_only),  # 4 short signature page
            footer,  # 5 page-sized photo under a footer
            ("blank", ""),  # 6 empty page
            ("blank", ""),  # 7 text converted to outlines
            ("blank", ""),  # 8 scan stored as six strips
        ],
        {
            0: PageExtras(images=[(photo, in_margins)]),
            1: PageExtras(images=[(photo, in_margins)]),
            4: PageExtras(images=[(photo, narrow)]),
            6: PageExtras(ops=outlines),
            7: PageExtras(images=strips),
        },
    )
    doc = read_document(pdf, max_pages=50)

    assert doc.kinds == {
        1: PageKind.UNCLASSIFIED,
        2: PageKind.UNCLASSIFIED,
        3: PageKind.SCAN,
        4: PageKind.NATIVE,
        5: PageKind.HYBRID,
        6: PageKind.BLANK,
        7: PageKind.UNCLASSIFIED,
        8: PageKind.UNCLASSIFIED,
    }
    assert doc.issues == {
        1: ISSUE_PHOTO_IN_DOCUMENT,
        2: ISSUE_PHOTO_IN_DOCUMENT,
        5: ISSUE_SCAN_WITH_WORD_TEXT,
        7: ISSUE_VECTOR_TEXT,
        8: ISSUE_SPLIT_IMAGES,
    }
    assert [page.number for page in doc.texts] == [4]  # only native pages are read from the text layer
