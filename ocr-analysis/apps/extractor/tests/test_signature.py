"""Tahap 4 on synthetic pages. Every signature here is a drawn scribble; no real signature is used."""

import cv2
import numpy as np
import pytest
from helpers import (
    BODY_FILLER,
    CLOSING_LINE,
    PageExtras,
    flow,
    make_pdf,
    page_text,
    pdf_rect,
    scribble_image,
    scribble_ops,
    signature_rows,
    signed_contract_pdf,
)

from contract_extractor.common.layout import PageText
from contract_extractor.common.models import SignatureStatus, Word
from contract_extractor.document.pdf.objects import Box, read_page_objects
from contract_extractor.document.pdf.reader import read_document
from contract_extractor.document.pdf.render import PageRenderer
from contract_extractor.signature.ink import (
    find_meterai,
    ink_verdict,
    measure_ink,
    meterai_boxes,
    meterai_boxes_gray,
    meterai_pixels,
)
from contract_extractor.signature.signature import (
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


# Meterai: a pink-magenta stamp drawn here, never a real one. RGB page of 1000 px height.
METERAI_PINK, METERAI_LIGHT = (220, 60, 160), (240, 150, 200)
BLUE_PEN, BLACK_PEN, PURPLE_PEN = (30, 60, 200), (0, 0, 0), (150, 50, 170)


def _page_rgb(h=1000, w=800):
    return np.full((h, w, 3), 255, np.uint8)


def _meterai(rgb, x0=300, y0=400, side=90):
    cv2.rectangle(rgb, (x0, y0), (x0 + side, y0 + side), METERAI_LIGHT, -1)
    for y in range(y0 + 6, y0 + side, 8):  # the wavy security pattern
        xs = np.arange(x0, x0 + side)
        ys = y + 3 * np.sin(xs / 4)
        cv2.polylines(rgb, [np.stack([xs, ys], axis=1).astype(np.int32)], False, METERAI_PINK, 2)
    return rgb


def _pen(rgb, colour, x0, x1, y_mid, thickness=4):
    xs = np.linspace(x0, x1, 200)
    ys = y_mid + 40 * np.sin(xs / 15) * np.cos(xs / 41)
    cv2.polylines(rgb, [np.stack([xs, ys], axis=1).astype(np.int32)], False, colour, thickness)
    return rgb


def test_meterai_colours_are_whitened_but_pen_strokes_over_it_stay():
    rgb = _meterai(_page_rgb())
    _pen(rgb, BLUE_PEN, 280, 410, 430)
    _pen(rgb, BLACK_PEN, 280, 410, 460)
    boxes = meterai_boxes(rgb)
    assert len(boxes) == 1
    x0, y0, x1, y1 = boxes[0]
    assert max(abs(x0 - 300), abs(y0 - 400), abs(x1 - 391), abs(y1 - 491)) <= 2  # the drawn stamp, give or take a pixel

    whiten = meterai_pixels(rgb, boxes)
    pink = np.all(rgb == METERAI_PINK, axis=2)
    assert whiten[pink].all()
    for pen in (BLUE_PEN, BLACK_PEN):
        assert not whiten[np.all(rgb == pen, axis=2)].any()


def test_purple_pen_signature_is_not_taken_for_a_meterai():
    rgb = _pen(_page_rgb(), PURPLE_PEN, 200, 420, 500)
    assert meterai_boxes(rgb) == []


def test_grayscale_meterai_is_found_and_its_whole_box_whitened():
    # A grayscale scan: a tinted stamp with a darker pattern, a black pen signature beside it, printed text above.
    rgb = _meterai(_page_rgb())
    cv2.putText(rgb, "PIHAK KEDUA", (250, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.2, BLACK_PEN, 3)
    _pen(rgb, BLACK_PEN, 450, 650, 450)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    boxes, whiten = find_meterai(np.stack([gray] * 3, axis=2), gray)
    assert len(boxes) == 1
    x0, y0, x1, y1 = boxes[0]
    assert max(abs(x0 - 300), abs(y0 - 400), abs(x1 - 391), abs(y1 - 491)) <= 12  # the drawn stamp, near enough
    assert whiten[y0:y1, x0:x1].all() and whiten.sum() == (x1 - x0) * (y1 - y0)


def _black_and_white_stamp() -> np.ndarray:
    """A 1-bit copy of a meterai at (600, 800)-(780, 920): its frame, wave lines with paper between, a dense block for
    its printed text in one corner; the signing line touching its bottom and the printed name under it."""
    page = np.full((2000, 1400), 255, np.uint8)
    cv2.rectangle(page, (600, 800), (780, 920), 0, 3)
    for y in range(808, 920, 12):
        xs = np.arange(600, 780)
        cv2.polylines(page, [np.stack([xs, y + 5 * np.sin(xs / 7)], axis=1).astype(np.int32)], False, 0, 3)
    cv2.rectangle(page, (608, 808), (668, 848), 0, -1)
    cv2.line(page, (640, 920), (1260, 920), 0, 3)
    cv2.putText(page, "Budi Santoso", (660, 980), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 4)
    return page


def test_a_black_and_white_meterai_is_whitened_whole_not_just_its_dense_corner():
    # Only the printed corner is dense enough to be found; the waves left over read as a signature on an unsigned
    # contract (800-contract website run). The core grows to the stamp, and stops at the signing line and the name.
    page = _black_and_white_stamp()
    [(x0, y0, x1, y1)] = meterai_boxes_gray(page)
    assert max(abs(x0 - 600), abs(y0 - 800), abs(x1 - 780), abs(y1 - 920)) <= 12


def test_a_full_size_meterai_core_does_not_grow_into_a_signature_touching_it():
    rgb = _pen(_meterai(_page_rgb()), BLACK_PEN, 380, 600, 470)  # starts on the stamp's right edge
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    [(x0, y0, x1, y1)] = meterai_boxes_gray(gray)
    assert max(abs(x0 - 300), abs(y0 - 400), abs(x1 - 391), abs(y1 - 491)) <= 12


def test_colour_page_keeps_the_colour_rule():
    rgb = _pen(_meterai(_page_rgb()), BLUE_PEN, 280, 410, 430)
    boxes, whiten = find_meterai(rgb, cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    assert boxes == meterai_boxes(rgb)
    assert not whiten[np.all(rgb == BLUE_PEN, axis=2)].any()


def _closing_page_with_heading() -> list[tuple[float, float, str]]:
    """A block whose employee column starts with `Yang menerima,` instead of an employee label, the printed name
    about eight name heights below it."""
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6 + [CLOSING_LINE])
    return rows + [
        (300.0, 80.0, "PERUSAHAAN,"),
        (300.0, 380.0, "Yang menerima,"),
        (390.0, 80.0, "Andi Wijaya"),
        (390.0, 380.0, "Budi Santoso"),
    ]


def test_heading_above_the_name_sets_the_roi_top(tmp_path):
    # Written high, right under `Yang menerima,`: the default five-name-height ROI only reached y 346.
    rows = _closing_page_with_heading()
    extras = PageExtras(ops=scribble_ops((385.0, 312.0, 70.0, 40.0)))
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: extras})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)

    result = detect_signature(page, False, image, None, "Budi Santoso")  # pixels only, as on a scan
    assert (result.status, result.anchor) == (SIGNED, ANCHOR_NAME)
    assert result.roi is not None and result.roi[1] < 312.0 / 792.0


def test_ocr_junk_read_off_the_signature_does_not_cut_the_roi():
    # A scan's OCR reads the signature as words: too tall, unsure, or a stray two-letter `oe`. Taken for a block line,
    # the nearest of them cut the ROI off right under the signature.
    page = page_text(1, _closing_page_with_heading())
    junk = (
        Word(text="Hasna", bbox=(392.0, 322.0, 440.0, 346.0), conf=0.93),
        Word(text="Gilda", bbox=(400.0, 350.0, 430.0, 361.0), conf=0.62),
        Word(text="oe", bbox=(395.0, 368.0, 405.0, 376.0), conf=0.87),
    )
    page = PageText(number=1, width=page.width, height=page.height, words=page.words + junk, source="ocr")
    result = detect_signature(page, False, np.full((1100, 850), 255, np.uint8), None, "Budi Santoso")
    assert result.roi is not None and result.roi[1] < 312.0 / 792.0  # up to `Yang menerima,`


def _heading_at(y: float) -> list[tuple[float, float, str]]:
    return [(y, x, t) if t == "Yang menerima," else (r, x, t) for r, x, t in _closing_page_with_heading()]


@pytest.mark.parametrize(("heading_y", "reaches"), [(255.0, True), (200.0, False)])
def test_a_heading_far_above_the_name_still_sets_the_roi_top(heading_y, reaches):
    # A photographed contract signed over a meterai left about twelve name heights between `Tenaga Kerja,` and the
    # name; a reach of ten fell back to the default ROI, which only held the meterai's lower half. Further than
    # LABEL_REACH, a line belongs to another part of the page.
    page = page_text(1, _heading_at(heading_y))
    result = detect_signature(page, False, np.full((1100, 850), 255, np.uint8), None, "Budi Santoso")
    assert result.roi is not None
    assert (result.roi[1] < (heading_y + 14.0) / 792.0) is reaches


def test_thin_signature_in_a_roi_stretched_to_the_heading_is_signed(tmp_path):
    # A thin pen in the tall space under `Yang menerima,` is about 1.35% of that ROI, under SIGNED_INK; the ink share
    # counts against the default ROI height.
    rows = _closing_page_with_heading()
    extras = PageExtras(ops=scribble_ops((385.0, 320.0, 60.0, 60.0), line_width=0.7))
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: extras})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)
    result = detect_signature(page, False, image, None, "Budi Santoso")
    assert (result.status, result.source) == (SIGNED, "pixels")


@pytest.mark.parametrize(("native", "status"), [(False, UNCERTAIN), (True, UNSIGNED)])
def test_meterai_where_the_employee_signs_without_ink(native, status):
    # On a scan a faint pen over the meterai is whitened with the stamp's colours, so no ink there proves nothing.
    page = page_text(5, flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0))
    meterai = [Box(400.0 / 612.0, 385.0 / 792.0, 450.0 / 612.0, 425.0 / 792.0)]
    result = detect_signature(page, native, np.full((1100, 850), 255, np.uint8), None, "Budi Santoso", meterai=meterai)
    assert (result.status, result.meterai) == (status, 1)


def test_light_grey_native_signature_counts_as_ink(tmp_path):
    # Grey 178, lighter than the Otsu threshold of a page of black text: it used to be no ink, so uncertain.
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0)
    extras = PageExtras(ops="0.7 G\n" + scribble_ops((385.0, 400.0, 70.0, 38.0)))
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: extras})
    result = _detect(pdf, page_number=1)
    assert (result.status, result.source) == (SIGNED, "vector")


METERAI_IMAGE_BOX = (385.0, 380.0, 80.0, 64.0)  # (x, y_top, w, h) points, between the label and the name


def _meterai_image(with_pen: bool) -> np.ndarray:
    """Grayscale stand-in for a meterai image: grey pattern, and a pen stroke when the employee signed on it."""
    image = np.full((200, 250), 255, np.uint8)
    for y in range(8, 200, 10):
        xs = np.arange(250)
        cv2.polylines(image, [np.stack([xs, y + 3 * np.sin(xs / 4)], axis=1).astype(np.int32)], False, 190, 3)
    if with_pen:
        xs = np.linspace(10, 240, 200)
        ys = 100 + 66 * np.sin(xs / 18) * np.cos(xs / 47)
        cv2.polylines(image, [np.stack([xs, ys], axis=1).astype(np.int32)], False, 0, 8)
    return image


def _detect_with_meterai(tmp_path, images):
    """A native closing page with a meterai image at METERAI_IMAGE_BOX, whitened the way the pipeline does it."""
    rows = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 6) + signature_rows(350.0)
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: PageExtras(images=images)})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)
    # The pipeline turns the meterai's own colours white and keeps dark pen pixels; grey stands in for pink here.
    x, y_top, w, h = METERAI_IMAGE_BOX
    sx, sy = image.shape[1] / 612.0, image.shape[0] / 792.0
    patch = image[int(y_top * sy) : int((y_top + h) * sy), int(x * sx) : int((x + w) * sx)]
    patch[patch > 100] = 255
    stamp = Box(x / 612.0, y_top / 792.0, (x + w) / 612.0, (y_top + h) / 792.0)
    return detect_signature(page, True, image, read_page_objects(pdf, 1), "Budi Santoso", meterai=[stamp])


@pytest.mark.parametrize(("with_pen", "status", "source"), [(False, UNSIGNED, None), (True, SIGNED, "image")])
def test_meterai_image_is_no_signing_object(tmp_path, with_pen, status, source):
    result = _detect_with_meterai(tmp_path, [(_meterai_image(with_pen), METERAI_IMAGE_BOX)])
    assert (result.status, result.source, result.meterai) == (status, source, 1)


def test_signature_image_laid_over_the_meterai_still_counts(tmp_path):
    # A small signature image wholly on the meterai, its ink too little to count alone: only an image matching the
    # meterai's box is the stamp, so this one stays a signing object.
    signature_box = (405.0, 395.0, 44.0, 22.0)
    images = [(_meterai_image(False), METERAI_IMAGE_BOX), (scribble_image(), signature_box)]
    result = _detect_with_meterai(tmp_path, images)
    assert ink_verdict(result.features)[0] is UNCERTAIN
    assert (result.status, result.source) == (SIGNED, "image")


def _native_closing_page(tmp_path, rows, box, name="Budi Santoso"):
    """A native closing page from (y, x, text) rows with a vector scribble in `box` (x, y_top, w, h)."""
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: PageExtras(ops=scribble_ops(box))})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)
    return detect_signature(page, True, image, read_page_objects(pdf, 1), name)


def test_a_short_name_still_gets_a_roi_as_wide_as_a_signature(tmp_path):
    # `Ikin` is 22 pt wide, so 2.5 name widths ended before a signature written beside the name.
    rows = _closing_page_with_heading()[:-1] + [(390.0, 380.0, "Ikin")]
    result = _native_closing_page(tmp_path, rows, (425.0, 340.0, 65.0, 40.0), name="Ikin")
    assert (result.status, result.source, result.anchor) == (SIGNED, "vector", ANCHOR_NAME)


@pytest.mark.parametrize("script_text", ["Budi Santoso", "Bwdi Smtsa"])
def test_a_signature_read_as_words_right_above_the_name_is_ink(script_text):
    # A script-font signature that OCR reads as a confident line of normal height, matching the name or not. Taken for
    # a heading it cut the ROI off under itself and its word boxes hid its strokes: a signed scan read unsigned.
    page = page_text(1, _closing_page_with_heading())
    script = page_text(1, [(362.0, 385.0, script_text)]).words
    page = PageText(number=1, width=page.width, height=page.height, words=page.words + script, source="ocr")
    image = np.full((1100, 850), 255, np.uint8)
    sx, sy = 850 / 612.0, 1100 / 792.0
    xs = np.linspace(385.0, 470.0, 200)
    ys = 367.0 + 9.0 * np.sin(xs / 3.0)
    cv2.polylines(image, [np.stack([xs * sx, ys * sy], axis=1).astype(np.int32)], False, 0, 3)
    result = detect_signature(page, False, image, None, "Budi Santoso")
    assert result.anchor == ANCHOR_NAME and result.candidates == 1  # the script line is no second printed name
    assert result.status is not UNSIGNED
    assert result.roi is not None and result.roi[1] < 312.0 / 792.0  # up to `Yang menerima,`


def test_an_e_signature_caption_between_signature_and_name_does_not_cut_the_roi(tmp_path):
    rows = _closing_page_with_heading() + [(378.0, 395.0, "Ditandatangani secara elektronik 01/01/2026")]
    result = _native_closing_page(tmp_path, rows, (385.0, 325.0, 70.0, 40.0))
    assert (result.status, result.source, result.anchor) == (SIGNED, "vector", ANCHOR_NAME)


@pytest.mark.parametrize("caption", ["Ditandatangani secara elektronik 01/01/2026", "Digitally signed 01/01/2026"])
def test_an_e_signature_caption_high_above_the_name_is_not_a_heading(tmp_path, caption):
    # The signature sat high, its caption right under it and far above the name. Taken for a heading, the caption cut
    # the ROI off under the signature: two signed synthetic photos read unsigned on the 800-contract website run.
    rows = _closing_page_with_heading() + [(342.0, 382.0, caption)]
    extras = PageExtras(ops=scribble_ops((385.0, 304.0, 70.0, 34.0)))
    pdf = make_pdf(tmp_path / "c.pdf", [("layout", [(x, y, t) for y, x, t in rows])], {0: extras})
    page = read_document(pdf, 200).texts[0]
    with PageRenderer(pdf) as renderer:
        image = renderer.render_gray(1, 300)

    result = detect_signature(page, False, image, None, "Budi Santoso")  # pixels only, as on a scan
    assert (result.status, result.anchor) == (SIGNED, ANCHOR_NAME)
    assert result.roi is not None and result.roi[1] < 312.0 / 792.0  # up to `Yang menerima,`, past the caption


def test_an_unread_name_over_an_employee_title_puts_the_roi_above_the_name(tmp_path):
    # OCR could not match the name under a signature stroke; `Tenaga Kerja` printed under it is a title, so the
    # signature is above the name line, not below the title.
    rows = _closing_page_with_heading() + [(404.0, 380.0, "Tenaga Kerja")]
    result = _native_closing_page(tmp_path, rows, (385.0, 330.0, 70.0, 40.0), name="Nama Lain Sekali")
    assert (result.status, result.source, result.anchor) == (SIGNED, "vector", ANCHOR_LABEL)
    assert result.roi is not None and result.roi[3] <= 402.0 / 792.0


def test_an_employee_heading_anchors_when_no_name_or_label_is_read(tmp_path):
    result = _native_closing_page(tmp_path, _closing_page_with_heading(), (385.0, 330.0, 70.0, 40.0), name="Nama Lain")
    assert (result.status, result.source, result.anchor) == (SIGNED, "vector", ANCHOR_LABEL)
    assert result.roi is not None and result.roi[3] >= 400.0 / 792.0  # down to the unread name line


def test_a_name_split_by_a_signature_stroke_still_anchors():
    # The stroke blanked `ASTA`: OCR read `AN` and `SIA` three word heights apart.
    words = (
        Word(text="AN", bbox=(380.0, 448.0, 391.0, 459.0), conf=0.96),
        Word(text="SIA", bbox=(424.0, 448.0, 440.0, 459.0), conf=0.93),
        Word(text="PALASTRI", bbox=(443.0, 448.0, 487.0, 459.0), conf=0.92),
    )
    page = PageText(number=5, width=612.0, height=792.0, words=words, source="ocr")
    assert len(name_anchors(page.lines, "ANASTASIA PALASTRI")) == 1


def test_the_roi_stops_halfway_to_the_other_columns_text():
    # No company label read in the company's column, only its signer's name level with the employee's long name: the
    # half-page ROI reached over to where the company signer's pen ran (an unsigned synthetic photo read signed).
    rows = [row for row in _closing_page_with_heading()[:-2] if row[2] != "PERUSAHAAN,"]
    page = page_text(1, rows + [(448.0, 60.0, "Andi Wijaya Kusumanegara"), (448.0, 300.0, "Budi Santoso Hutagalung Hutapea")])
    result = detect_signature(page, False, np.full((1100, 850), 255, np.uint8), None, "Budi Santoso Hutagalung Hutapea")
    company_end = 60.0 + 5.5 * len("AndiWijayaKusumanegara") + 2 * 3.0
    assert result.roi is not None and result.roi[0] * 612.0 >= (company_end + 300.0) / 2 - 0.5


def test_a_short_names_underline_is_no_signature_in_the_wide_second_try():
    # 300 dpi. Widened for a short name, the ROI's line removal scaled to the wide box and kept the name's underline,
    # so five unsigned synthetic files went uncertain; it now scales to the usual ROI.
    page = page_text(1, _closing_page_with_heading()[:-1] + [(390.0, 380.0, "Ellis")])
    scale = 2550 / 612.0
    image = np.full((3300, 2550), 255, np.uint8)
    cv2.line(image, (int(380 * scale), int(399.5 * scale)), (int(407.5 * scale), int(399.5 * scale)), 0, 3)
    result = detect_signature(page, False, image, None, "Ellis")
    assert result.status is UNSIGNED


@pytest.mark.parametrize(("grey", "status"), [(200, UNCERTAIN), (255, UNSIGNED)])
def test_a_faint_pen_on_a_scan_is_uncertain_not_unsigned(grey, status):
    # Light grey strokes sit above the page's Otsu threshold and read as no ink; seen at a lighter one, they leave
    # the ROI uncertain (for a person or the extractor's crop), never signed.
    page = page_text(1, _closing_page_with_heading())
    image = np.full((1100, 850), 255, np.uint8)
    for y, x, text in _closing_page_with_heading():
        cv2.putText(image, text, (int(x * 850 / 612), int((y + 10) * 1100 / 792)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1)
    xs = np.linspace(390.0, 460.0, 200)
    ys = 350.0 + 18.0 * np.sin(xs / 4.0)
    cv2.polylines(image, [np.stack([xs * 850 / 612, ys * 1100 / 792], axis=1).astype(np.int32)], False, grey, 2)
    result = detect_signature(page, False, image, None, "Budi Santoso")
    assert (result.status, result.anchor) == (status, ANCHOR_NAME)
