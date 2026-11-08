"""Tahap 4 on synthetic pages. Every signature here is a drawn scribble; no real signature is used."""

import cv2
import numpy as np
import pytest
from helpers import (
    BODY_FILLER,
    PageExtras,
    flow,
    make_pdf,
    page_text,
    pdf_rect,
    scribble_ops,
    signature_rows,
    signed_contract_pdf,
)

from contract_extractor.models import SignatureStatus
from contract_extractor.pdf.objects import read_page_objects
from contract_extractor.pdf.reader import read_document
from contract_extractor.pdf.render import PageRenderer
from contract_extractor.stages.ink import ink_verdict, measure_ink
from contract_extractor.stages.signature import (
    ANCHOR_LABEL,
    ANCHOR_NAME,
    EMPLOYEE_LABELS,
    detect_signature,
    employee_labels,
    name_anchors,
)

SIGNED, UNSIGNED, UNCERTAIN = SignatureStatus.SIGNED, SignatureStatus.UNSIGNED, SignatureStatus.UNCERTAIN


def _detect(pdf, name="Budi Santoso", page_number=5, with_objects=True):
    page = next(p for p in read_document(pdf, 200).texts if p.number == page_number)
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(page_number, 300)
    objects = read_page_objects(pdf, page_number) if with_objects else None
    return detect_signature(page, True, image, objects, name)


@pytest.mark.parametrize(
    ("marks", "status", "source", "clear"),
    [
        ((), UNSIGNED, None, True),
        (("vector",), SIGNED, "vector", True),
        (("annotation",), SIGNED, "annotation", True),
        (("image",), SIGNED, "image", True),
        (("digital",), SIGNED, "digital", False),  # an invisible signed field: signed, but not a clear margin
        (("company_vector",), UNSIGNED, None, True),  # the company's signature is never the employee's
        (("company_vector", "vector"), SIGNED, "vector", True),
    ],
)
def test_native_signing_methods(tmp_path, marks, status, source, clear):
    result = _detect(signed_contract_pdf(tmp_path / "c.pdf", *marks))
    assert (result.status, result.source, result.clear_margin, result.anchor) == (status, source, clear, ANCHOR_NAME)


def test_paraf_outside_the_roi_is_ignored_but_counted(tmp_path):
    result = _detect(signed_contract_pdf(tmp_path / "c.pdf", "corner_paraf"))
    assert result.status is UNSIGNED
    assert result.annotations_outside_roi == 1


def test_native_ink_without_a_signing_object_is_uncertain(tmp_path):
    result = _detect(signed_contract_pdf(tmp_path / "c.pdf", "vector"), with_objects=False)
    assert result.status is UNCERTAIN


def test_unreadable_name_falls_back_to_stacked_employee_labels(tmp_path):
    result = _detect(signed_contract_pdf(tmp_path / "c.pdf", "vector"), name="Nama Lain Sekali")
    assert (result.status, result.anchor) == (SIGNED, ANCHOR_LABEL)
    assert result.clear_margin is False  # a label anchor never reaches a clear margin


def test_no_anchor_at_all_is_uncertain_not_unsigned(tmp_path):
    rows = flow([BODY_FILLER] * 6) + [(500.0, 380.0, "Tanda tangan")]
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])])
    result = _detect(pdf, page_number=1)
    assert (result.status, result.anchor, result.confidence) == (UNCERTAIN, None, 0.0)


def test_two_anchored_rois_that_disagree_are_uncertain(tmp_path):
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0) + [(600.0, 380.0, "Budi Santoso")]
    pdf = make_pdf(
        tmp_path / "c.pdf",
        [("layout", [(x, y, t) for y, x, t in rows])],
        {0: PageExtras(ops=scribble_ops((385.0, 408.0, 70.0, 38.0)))},
    )
    result = _detect(pdf, page_number=1)
    assert (result.status, result.candidates) == (UNCERTAIN, 2)


def _vendor_c_page(employee_label: str = "PIHAK KEDUA") -> list[tuple[float, float, str]]:
    """Closing page laid out like samples C and D: the closing paragraph wraps a short line ending in the employee
    label, a running paragraph continues under it, then the two-column block with a company line under the labels."""
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6)
    rows += flow(
        [
            "Demikian perjanjian ini dibuat dan ditandatangani oleh Pihak Pertama dan Pihak Kedua",
            "dalam rangkap dua bermeterai cukup yang masing-masing mempunyai kekuatan hukum sama",
            "bagi PIHAK KEDUA.",
            "Perjanjian ini ditandatangani dalam keadaan sadar dan tanpa paksaan dari pihak manapun.",
        ],
        start_y=200.0,
    )
    return rows + [
        (300.0, 80.0, "PIHAK PERTAMA"),
        (300.0, 380.0, employee_label),
        (316.0, 80.0, "PT Contoh Sejahtera"),
        (380.0, 80.0, "Andi Wijaya"),
        (380.0, 380.0, "Budi Santoso"),
        (394.0, 80.0, "Direktur"),
    ]


VENDOR_C_SIGNATURE_BOX = (385.0, 336.0, 70.0, 40.0)  # right above the printed name


@pytest.mark.parametrize("name", ["Budi Santoso", None])
def test_paragraph_line_ending_in_the_employee_label_is_not_an_anchor(tmp_path, name):
    box = VENDOR_C_SIGNATURE_BOX
    extras = PageExtras(annots=[(f"/Subtype /Ink /Rect [{pdf_rect(box)}]", scribble_ops(box))])
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in _vendor_c_page()])], {0: extras})
    result = _detect(pdf, name=name, page_number=1)
    assert (result.status, result.source) == (SIGNED, "annotation")
    assert result.anchor == (ANCHOR_NAME if name else ANCHOR_LABEL)


def test_name_roi_spans_the_whole_gap_up_to_the_employee_label(tmp_path):
    # Sample C: about seven name heights between the label and the printed name, the signature written high in
    # that gap. A fixed five-name-height ROI missed it: the real scan came out uncertain, this page unsigned.
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + [
        (300.0, 80.0, "PIHAK PERTAMA"),
        (300.0, 380.0, "PIHAK KEDUA"),
        (380.0, 80.0, "Andi Wijaya"),
        (380.0, 380.0, "Budi Santoso"),
        (394.0, 80.0, "Direktur"),
    ]
    extras = PageExtras(ops=scribble_ops((385.0, 313.0, 70.0, 40.0)))
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: extras})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)

    result = detect_signature(page, False, image, None, "Budi Santoso")  # pixels only, as on a scan
    assert (result.status, result.source, result.anchor) == (SIGNED, "pixels", ANCHOR_NAME)
    assert result.roi is not None and result.roi[1] < 313.0 / 792.0  # starts at the label, above the signature


def test_label_far_above_the_name_does_not_stretch_the_roi():
    page = page_text(1, [(100.0, 380.0, "PIHAK KEDUA"), (448.0, 380.0, "Budi Santoso")])
    result = detect_signature(page, False, np.full((1100, 850), 255, np.uint8), None, "Budi Santoso")
    name_height = 11.0  # helpers' word height
    assert result.roi is not None
    assert (result.roi[3] - result.roi[1]) * 792.0 <= 5.0 * name_height + 1.0  # the default height, not 350 pt


def test_ocr_garbled_employee_label_still_anchors():
    page = page_text(1, _vendor_c_page(employee_label="PIHAKKEDUA"))
    result = detect_signature(page, False, np.full((1100, 850), 255, np.uint8), None, None)
    assert (result.anchor, result.status) == (ANCHOR_LABEL, UNSIGNED)
    assert result.roi is not None and result.roi[0] > 0.5  # the employee's right-hand column


def test_signed_widget_and_annotations_are_read(tmp_path):
    objects = read_page_objects(signed_contract_pdf(tmp_path / "c.pdf", "digital", "annotation", "image"), 5)
    assert len(objects.signed_fields) == 1
    assert [kind for kind, _ in objects.annotations] == ["Ink"]
    assert len(objects.images) == 1


def test_name_anchor_picks_the_employee_column_on_a_shared_line():
    page = page_text(5, [(448.0, 80.0, "Andi Wijaya"), (448.0, 380.0, "Budi Santoso")])
    [box] = name_anchors(page.lines, "Budi Santoso")
    assert box.x0 == 380.0


def test_first_name_alone_is_not_an_anchor():
    page = page_text(5, [(448.0, 380.0, "Budi Hartono")])
    assert name_anchors(page.lines, "Budi Santoso") == []


def test_single_word_name_matches_with_or_without_the_space():
    page = page_text(5, [(448.0, 380.0, "ABD KARIM")])
    assert len(name_anchors(page.lines, "Abd.karim")) == 1
    assert len(name_anchors(page.lines, "Abdkarim")) == 1


def test_employee_labels_follow_the_identity_page():
    assert employee_labels("Nama : X\nselanjutnya disebut Pekerja") == ("pekerja",)
    assert employee_labels("tanpa sebutan") == EMPLOYEE_LABELS


# Ink analysis on synthetic ROI crops: 300 dpi, printed word height 40 px.
WORD_H = 40.0
OTSU = 128.0


def _blank(h=240, w=640):
    return np.full((h, w), 255, np.uint8)


def _scribble(image, x0=60, x1=520, thickness=6):
    xs = np.linspace(x0, x1, 200)
    ys = image.shape[0] / 2 + 70 * np.sin(xs / 15) * np.cos(xs / 41)
    cv2.polylines(image, [np.stack([xs, ys], axis=1).astype(np.int32)], False, 0, thickness=thickness)
    return image


def test_blank_roi_is_unsigned_with_a_clear_margin():
    assert ink_verdict(measure_ink(_blank(), OTSU, WORD_H, [])) == (UNSIGNED, True)


def test_signature_line_and_table_border_are_not_ink():
    image = _blank()
    cv2.line(image, (20, 200), (620, 200), 0, 3)
    cv2.line(image, (630, 0), (630, 239), 0, 3)
    assert ink_verdict(measure_ink(image, OTSU, WORD_H, []))[0] is UNSIGNED


def test_printed_name_inside_its_word_box_is_not_ink():
    image = _blank()
    cv2.putText(image, "BUDI SANTOSO", (150, 215), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    features = measure_ink(image, OTSU, WORD_H, [(140, 180, 470, 225)])
    assert ink_verdict(features)[0] is UNSIGNED


def test_scribble_is_signed():
    status, clear = ink_verdict(measure_ink(_scribble(_blank()), OTSU, WORD_H, []))
    assert (status, clear) == (SIGNED, True)


def test_signature_written_over_the_printed_name_still_counts():
    image = _scribble(_blank())
    cv2.putText(image, "BUDI SANTOSO", (150, 215), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    features = measure_ink(image, OTSU, WORD_H, [(140, 180, 470, 225)])
    assert ink_verdict(features)[0] is SIGNED


def test_handwritten_block_letters_are_uncertain():
    image = _blank()
    for i in range(6):
        cv2.ellipse(image, (80 + i * 80, 120), (18, 24), 0, 0, 360, 0, -1)
    features = measure_ink(image, OTSU, WORD_H, [])
    assert features.looks_like_text
    assert ink_verdict(features)[0] is UNCERTAIN


def test_round_stamp_is_dropped():
    image = _blank(h=300, w=400)
    cv2.circle(image, (200, 150), 110, 0, 8)
    assert ink_verdict(measure_ink(image, OTSU, WORD_H, []))[0] is UNSIGNED
