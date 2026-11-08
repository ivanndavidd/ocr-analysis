"""Tahap 4: deteksi tanda tangan karyawan, sepenuhnya lokal (Bagian 11 PRD).

ROI selalu diikat ke nama karyawan tercetak (atau sebutan karyawan bila nama tidak terbaca) di blok tanda tangan,
yaitu zona penutup tanpa baris paragrafnya.
Sistem tidak pernah memilih tinta terbesar di halaman: jangkar tidak ditemukan berarti `uncertain`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace

import cv2
import numpy as np
from rapidfuzz import fuzz

from contract_extractor.common.layout import Line, PageText
from contract_extractor.common.models import SignatureStatus, Word
from contract_extractor.document.pdf.objects import Box, PageObjects
from contract_extractor.extractor_method.pattern.filename import normalize_name
from contract_extractor.signature.ink import TEXT_BOX_MIN_CONF, InkFeatures, ink_verdict, measure_ink
from contract_extractor.document.pages.page_selection import signature_block

EMPLOYEE_LABELS = ("pihak kedua", "tenaga kerja", "karyawan", "pekerja")
# Headings only the employee's column carries, whatever the identity page calls the employee: an anchor when OCR
# cannot read the printed name under a signature.
EMPLOYEE_HEADINGS = ("yang menerima",)
_COMPANY_LABEL = re.compile(r"^(?:pihak pertama|pengusaha|perusahaan|direktur|pt)$")

NAME_MATCH_MIN = 85
LABEL_MATCH_MIN = 85  # OCR can merge or garble a label under a signature stroke (`PIHAKKEDUA`)
SINGLE_TOKEN_MATCH_MIN = 90  # a one-word name (`Abdkarim`) needs a closer match
MAX_WORD_GAP = 2.0  # words further apart than this many word heights belong to different columns
# A signature stroke through the printed name can blank a few letters, splitting it (`AN SIA PALASTRI`); the other
# column is still much further away than this.
NAME_WORD_GAP = 3.5
ROI_WIDTH = 2.5  # times the name width, centred on the name
# Of the page width: the second try for a short name (`Ikin`), whose ROI was narrower than the signature beside it.
# Only a second try, after the ROI reads unsigned: as the first, it took in short names' underlines (five unsigned
# synthetic scans and photos went uncertain) and spread a signature's ink over more paper.
MIN_ROI_WIDTH = 0.3
ROI_HEIGHT = 5.0  # times the name line height, when no employee label sits above the name
# Name heights. A line closer above the name than this leaves no room to sign under it, so it is part of the signing
# area (a script-font signature OCR read as words, an e-signature caption), not a heading that bounds it.
MIN_SIGN_SPACE = 2.5
TITLE_GAP = 1.5  # label heights; a label this close under a line is a title under the printed name
# Name heights; a line further above the name belongs to another part of the page. 14, not 10: a contract signed
# over a meterai left about twelve between `Tenaga Kerja,` and the name, and the default ROI missed the pen.
LABEL_REACH = 14.0
MAX_ROI_WIDTH = 0.5  # of the page width
PRINTED_MAX_HEIGHT = 1.6  # median word heights; OCR reads a signature as taller words than printed text
HEAD_MIN_LETTERS = 3  # a block line bounding the ROI top holds real words, not a stray `oe` read off a stroke
# A printed e-signature caption (`Ditandatangani secara elektronik 12/03/2045`, `Digitally signed`) sits right under
# the signature it describes, wherever that is, so it never bounds the ROI. Taken for a heading where it sat high above
# the name, it cut the ROI off under the signature: two signed synthetic photos read unsigned on the 800-contract
# website run (2026-11-04). Matched on the letters only, spaces dropped, so a split word still counts.
_ESIGN_CAPTION = re.compile(r"(?=.*(?:tandatangan|signed|signature))(?=.*(?:elektroni|electroni|digital))")
VECTOR_MIN_SEGMENTS = 8
VECTOR_MIN_LENGTH = 1.0  # total stroke length, in ROI widths; a tick or a short underline stays below it
# A native page has a clean white background, so any clearly non-white pixel can be ink: a light grey or lavender
# signature (grey 160-195 on the synthetic set) sat above the scan threshold (Otsu, ~145) and read as no ink.
NATIVE_INK_THRESHOLD = 215.0
# A scan's ROI that reads unsigned at the page's threshold is measured again with this one. A faint pen (light grey on
# the synthetic set) sits above Otsu and read as no ink: on 184 synthetic scans and photos four faint signed ones read
# unsigned; at 220 three of them show signature-like ink, one unsigned page does (a dotted signing line), and none of
# the unsigned is anything but unsigned or uncertain. So it only makes the status uncertain, never signed.
FAINT_INK_THRESHOLD = 220.0
# An image whose box and a meterai's box mostly coincide (intersection over union) is the stamp, not a signature. A
# signature image laid half over the meterai scores 0.30-0.46 on the synthetic set, the meterai's own image about 1.
METERAI_IMAGE_IOU = 0.6

SOURCE_DIGITAL, SOURCE_ANNOTATION, SOURCE_IMAGE, SOURCE_VECTOR, SOURCE_PIXELS = (
    "digital",
    "annotation",
    "image",
    "vector",
    "pixels",
)
ANCHOR_NAME, ANCHOR_LABEL = "nama", "sebutan"


@dataclass(frozen=True)
class SignatureResult:
    status: SignatureStatus
    source: str | None
    anchor: str | None  # nama | sebutan | None
    clear_margin: bool
    confidence: float
    roi: tuple[float, float, float, float] | None = None  # page fractions x0, top, x1, bottom
    features: InkFeatures | None = None
    annotations_outside_roi: int = 0
    candidates: int = 0
    meterai: int = 0  # meterai stamps inside the measured ROI

    @property
    def anchor_found(self) -> bool:
        return self.anchor is not None


def employee_labels(p_id_text: str) -> tuple[str, ...]:
    """The employee labels this vendor uses on the identity page; all of them when none is found."""
    text = normalize_name(p_id_text)
    found = tuple(label for label in EMPLOYEE_LABELS if re.search(rf"(?<![a-z]){label}(?![a-z])", text))
    return found or EMPLOYEE_LABELS


def _box(words: tuple[Word, ...] | list[Word]) -> Box:
    return Box(min(w.bbox[0] for w in words), min(w.bbox[1] for w in words), max(w.bbox[2] for w in words), max(w.bbox[3] for w in words))


def _windows(line: Line, max_len: int, max_gap: float = MAX_WORD_GAP) -> Iterator[tuple[Word, ...]]:
    """Runs of up to max_len adjacent words; a wide gap ends the run, so two columns never merge."""
    words = line.words
    for i in range(len(words)):
        for k in range(1, max_len + 1):
            group = words[i : i + k]
            if len(group) < k:
                break
            if k > 1:
                a, b = group[-2], group[-1]
                if b.bbox[0] - a.bbox[2] > max_gap * (a.bbox[3] - a.bbox[1]):
                    break
            yield group


def name_anchors(zone: list[Line], name: str) -> list[Box]:
    """The best match of the employee name on each closing-zone line."""
    name_norm = normalize_name(name)
    if not name_norm:
        return []
    tokens = name_norm.split()
    target = name_norm.replace(" ", "")
    threshold = SINGLE_TOKEN_MATCH_MIN if len(tokens) == 1 else NAME_MATCH_MIN
    found: list[Box] = []
    for line in zone:
        best: tuple[float, tuple[Word, ...]] | None = None
        for group in _windows(line, len(tokens) + 1, NAME_WORD_GAP):
            text = normalize_name(" ".join(w.text for w in group))
            if not text:
                continue
            score = max(fuzz.ratio(text.replace(" ", ""), target), fuzz.token_sort_ratio(text, name_norm))
            if score >= threshold and (best is None or score > best[0]):
                best = (score, group)
        if best:
            found.append(_box(best[1]))
    return found


def _label_score(text: str, accept: re.Pattern[str] | tuple[str, ...]) -> float:
    if isinstance(accept, re.Pattern):
        return 100.0 if accept.match(text) else 0.0
    compact = text.replace(" ", "")
    best = 0.0
    for label in accept:
        # A one-word label needs a closer match, or `bekerja` would pass for `pekerja`.
        threshold = LABEL_MATCH_MIN if " " in label else SINGLE_TOKEN_MATCH_MIN
        score = fuzz.ratio(compact, label.replace(" ", ""))
        if score >= threshold:
            best = max(best, score)
    return best


def _phrase_boxes(zone: list[Line], accept: re.Pattern[str] | tuple[str, ...]) -> list[Box]:
    """Label boxes, one per label occurrence: overlapping windows on a line keep only the best match."""
    boxes: list[Box] = []
    for line in zone:
        found: list[tuple[float, Box]] = []
        for group in _windows(line, 3):
            text = normalize_name(" ".join(w.text for w in group))
            score = _label_score(text, accept) if text else 0.0
            if score:
                found.append((score, _box(group)))
        kept: list[Box] = []
        for _, box in sorted(found, key=lambda item: -item[0]):
            if not any(box.intersects(other) for other in kept):
                kept.append(box)
        boxes.extend(sorted(kept, key=lambda b: b.x0))
    return boxes


def _median_word_height(page: PageText) -> float:
    heights = sorted(word.bbox[3] - word.bbox[1] for word in page.words)
    return heights[len(heights) // 2] if heights else 10.0


def _printed(word: Word, median_h: float) -> bool:
    """OCR may read a signature as tall or low-confidence junk words; those are not printed text."""
    return word.conf >= TEXT_BOX_MIN_CONF and (word.bbox[3] - word.bbox[1]) <= PRINTED_MAX_HEIGHT * median_h


def _columns(words: list[Word]) -> list[list[Word]]:
    """A line's words split where a wide gap separates two columns."""
    groups = [words[:1]] if words else []
    for a, b in zip(words, words[1:]):
        if b.bbox[0] - a.bbox[2] > MAX_WORD_GAP * (a.bbox[3] - a.bbox[1]):
            groups.append([])
        groups[-1].append(b)
    return groups


def _column_runs(zone: list[Line], median_h: float) -> tuple[list[Box], list[Box]]:
    """Each block line's printed words split into its columns: `Yang menerima,` and `PT Duta Sejahtera` are two boxes.
    Returns the heads that can bound a ROI, and the e-signature captions, which never do.

    Junk OCR read off the signature itself is left out, since a run there would cut the ROI off right under the
    signature (13 synthetic scans turned signed into unsigned or uncertain that way). A caption is recognised from all
    its words: its small print reads with low confidence (`Ditandatangani` at 0.68 on a synthetic photo), and what was
    left of it, `secara elektronik`, bounded the ROI under the signature.
    """
    heads: list[Box] = []
    captions: list[Box] = []
    for line in zone:
        here = [
            _box(group)
            for group in _columns(list(line.words))
            if _ESIGN_CAPTION.search(normalize_name(" ".join(w.text for w in group)).replace(" ", ""))
        ]
        captions += here
        for group in _columns([word for word in line.words if _printed(word, median_h)]):
            box = _box(group)
            if sum(ch.isalpha() for w in group for ch in w.text) >= HEAD_MIN_LETTERS and not any(
                box.intersects(caption) for caption in here
            ):
                heads.append(box)
    return heads, captions


def _column(cx: float, half_width: float, page: PageText, company: list[Box]) -> tuple[float, float]:
    """Horizontal ROI extent: centred, capped at half the page, and never past the midpoint to a company label."""
    half = min(half_width, MAX_ROI_WIDTH * page.width / 2)
    x0, x1 = cx - half, cx + half
    for label in company:
        lcx = (label.x0 + label.x1) / 2
        if abs(lcx - cx) < half / 4:
            continue  # same column: a company word above the employee name, not a neighbour
        boundary = (lcx + cx) / 2
        if lcx < cx:
            x0 = max(x0, boundary)
        else:
            x1 = min(x1, boundary)
    return max(0.0, x0), min(page.width, x1)


def _own_column(roi: Box, anchor: Box, runs: list[Box]) -> Box:
    """Never past the midpoint to a block line of the other column at the ROI's height. Company labels alone did not
    always bound it: with none read in the company's column, a ROI above a long name took in the company signer's pen
    and an unsigned synthetic photo read signed."""
    x0, x1 = roi.x0, roi.x1
    for run in runs:
        if run.bottom < roi.top or run.top > roi.bottom + anchor.height:
            continue
        if run.x1 <= anchor.x0:
            x0 = max(x0, (run.x1 + anchor.x0) / 2)
        elif run.x0 >= anchor.x1:
            x1 = min(x1, (anchor.x1 + run.x0) / 2)
    return Box(x0, roi.top, x1, roi.bottom)


def _half_width(anchor: Box, page: PageText, wide: bool) -> float:
    return max(ROI_WIDTH * anchor.width, MIN_ROI_WIDTH * page.width if wide else 0.0) / 2


def roi_above(name: Box, page: PageText, labels: list[Box], company: list[Box], wide: bool = False) -> Box:
    """From the name's bottom edge upward, so a signature written over the printed name still counts.

    The space between the line above and the printed name is where the signature goes, so when a block line (an
    employee label, or a heading such as `Yang menerima,`) sits above the name in the same column, the ROI spans
    exactly that space, taller or shorter than the default. A line closer than MIN_SIGN_SPACE leaves no such space
    and is passed over (see `signing_band`). `wide` makes it at least MIN_ROI_WIDTH of the page wide.
    """
    x0, x1 = _column((name.x0 + name.x1) / 2, _half_width(name, page, wide), page, company)
    top = name.bottom - ROI_HEIGHT * name.height
    above = [
        label
        for label in labels
        if label.bottom <= name.top - MIN_SIGN_SPACE * name.height
        and label.x1 > x0
        and label.x0 < x1
        and name.bottom - label.bottom <= LABEL_REACH * name.height
    ]
    if above:
        top = max(label.bottom for label in above)  # the nearest one
    return _own_column(Box(x0, max(0.0, top), x1, name.bottom), name, labels)


def signing_band(name: Box, roi: Box, captions: list[Box] | None = None) -> Box:
    """The ROI's strip right above the printed name, MIN_SIGN_SPACE name heights tall. Printed-looking words there are
    a signature OCR read as text (a script font, neat handwriting) or an e-signature caption, so their boxes do not
    hide ink. Printed letters left as ink are too small to count, or look like text and leave the ROI uncertain.

    With an e-signature caption in the ROI, the signature it describes sits above the caption, often higher than this
    strip, so the band is the whole ROI above the name: half the synthetic e-signatures are set in a script font,
    which OCR can read as words."""
    if any(c.intersects(roi) and c.bottom <= name.top for c in captions or []):
        return Box(roi.x0, roi.top, roi.x1, name.top)
    return Box(roi.x0, max(roi.top, name.top - MIN_SIGN_SPACE * name.height), roi.x1, name.top)


def roi_below(name: Box, page: PageText, company: list[Box], runs: list[Box]) -> Box:
    x0, x1 = _column((name.x0 + name.x1) / 2, ROI_WIDTH * name.width / 2, page, company)
    return _own_column(Box(x0, name.top, x1, min(page.height, name.top + ROI_HEIGHT * name.height)), name, runs)


def label_roi(
    labels: list[Box], page: PageText, heads: list[Box], company: list[Box]
) -> tuple[Box, Box | None] | None:
    """Secondary anchor, when no printed name matched: between two stacked employee labels, or by a single one.

    A single label is a heading over the signing space when a line follows it in its column (the printed name OCR
    could not match): the ROI runs down to that line. With nothing below, a line right above it makes it a title under
    the printed name, and the signature goes above that line as above a name; on 300 synthetic contracts four signed
    ones read unsigned when the ROI went below a title. Returns the ROI and the line taken for the printed name."""
    for upper in labels:
        for lower in labels:
            stacked = lower.top > upper.bottom + upper.height and lower.x0 < upper.x1 and upper.x0 < lower.x1
            if stacked:
                width = max(upper.width, lower.width)
                x0, x1 = _column((upper.x0 + upper.x1) / 2, ROI_WIDTH * width / 2, page, company)
                return _own_column(Box(x0, upper.bottom, x1, lower.top), upper, heads), None
    if len(labels) != 1:
        return None
    label = labels[0]
    x0, x1 = _column((label.x0 + label.x1) / 2, ROI_WIDTH * label.width / 2, page, company)
    column = [h for h in heads if h.x1 > x0 and h.x0 < x1]
    below = [
        h
        for h in column
        if label.bottom + MIN_SIGN_SPACE * label.height <= h.top <= label.bottom + LABEL_REACH * label.height
    ]
    if below:
        name = min(below, key=lambda h: h.top)
        return _own_column(Box(x0, label.bottom, x1, name.bottom), label, heads), name
    over = [h for h in column if h.bottom <= label.top and label.top - h.bottom <= TITLE_GAP * label.height]
    if over:
        name = max(over, key=lambda h: h.bottom)
        return roi_above(name, page, heads, company), name
    bottom = min(page.height, label.bottom + ROI_HEIGHT * label.height)
    return _own_column(Box(x0, label.bottom, x1, bottom), label, heads), None


def _fractions(box: Box, width: float, height: float) -> tuple[float, float, float, float]:
    return (box.x0 / width, box.top / height, box.x1 / width, box.bottom / height)


def _frac_box(box: Box, width: float, height: float) -> Box:
    return Box(*_fractions(box, width, height))


@dataclass(frozen=True)
class _Pixels:
    image: np.ndarray
    threshold: float
    word_height: float  # median printed-word height, in image pixels
    text_boxes: list[tuple[int, int, int, int]]  # image pixels


def _pixels(page: PageText, image: np.ndarray, native: bool) -> _Pixels:
    h, w = image.shape
    sx, sy = w / page.width, h / page.height
    otsu, _ = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = NATIVE_INK_THRESHOLD if native else float(min(max(otsu, 80.0), 180.0))
    median_h = _median_word_height(page)
    boxes = [
        (int(word.bbox[0] * sx) - 2, int(word.bbox[1] * sy) - 2, int(word.bbox[2] * sx) + 2, int(word.bbox[3] * sy) + 2)
        for word in page.words
        if _printed(word, median_h)  # a junk word's box must not hide the signature's ink
    ]
    return _Pixels(image, threshold, median_h * sy, boxes)


def _ink(
    roi: Box, page: PageText, pixels: _Pixels, band: Box | None = None, rule_width: float | None = None
) -> InkFeatures:
    """`band`: the signing band (see `signing_band`), whose printed-looking words are measured as ink. `rule_width`:
    the ROI width, in page units, that line removal is scaled to, when the ROI was widened past its usual width."""
    h, w = pixels.image.shape
    fx0, ft, fx1, fb = _fractions(roi, page.width, page.height)
    x0, y0, x1, y1 = int(fx0 * w), int(ft * h), int(fx1 * w), int(fb * h)
    crop = pixels.image[y0:y1, x0:x1]
    band_top, band_bottom = (band.top / page.height * h, band.bottom / page.height * h) if band else (0.0, -1.0)
    local = [
        (bx0 - x0, by0 - y0, bx1 - x0, by1 - y0)
        for bx0, by0, bx1, by1 in pixels.text_boxes
        if bx1 > x0 and bx0 < x1 and by1 > y0 and by0 < y1 and not band_top <= (by0 + by1) / 2 <= band_bottom
    ]
    # The ink thresholds hold for a ROI of the default height. One stretched up to a heading has more blank paper, and
    # a thin signature in it fell under SIGNED_INK, so its ink share counts against the default height at most.
    area = crop.shape[1] * min(crop.shape[0], ROI_HEIGHT * pixels.word_height)
    rule_px = int(rule_width / page.width * w / 3) if rule_width else None
    return measure_ink(crop, pixels.threshold, pixels.word_height, local, area, rule_px)


def _overlap(a: Box, b: Box) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))


def _object_source(
    roi: Box, page: PageText, objects: PageObjects | None, native: bool, meterai: list[Box]
) -> tuple[str | None, bool]:
    """The signing object in the ROI, and whether a meterai image lies in it (not counted as one)."""
    if objects is None or not objects.width:
        return None, False
    roi_frac = _frac_box(roi, page.width, page.height)

    def hit(box: Box) -> bool:
        return _frac_box(box, objects.width, objects.height).intersects(roi_frac)

    def stamp(box: Box) -> bool:
        frac = _frac_box(box, objects.width, objects.height)
        area = frac.width * frac.height
        for m in meterai:
            shared = _overlap(frac, m)
            if shared and shared / (area + m.width * m.height - shared) >= METERAI_IMAGE_IOU:
                return True
        return False

    if any(hit(box) for box in objects.signed_fields):
        return SOURCE_DIGITAL, False
    if any(hit(box) for _, box in objects.annotations):
        return SOURCE_ANNOTATION, False
    if not native:
        return None, False  # a scan's images are the page itself; only annotations are checked there
    images = [box for box in objects.images if hit(box)]
    on_stamp = any(stamp(box) for box in images)
    if any(not stamp(box) for box in images):  # a meterai image is not a signature
        return SOURCE_IMAGE, on_stamp
    roi_width_pt = (roi.x1 - roi.x0) / page.width * objects.width
    inside = [v for v in objects.vectors if hit(v.box)]
    if sum(v.segments for v in inside) >= VECTOR_MIN_SEGMENTS and sum(v.length for v in inside) >= VECTOR_MIN_LENGTH * roi_width_pt:
        return SOURCE_VECTOR, on_stamp
    return None, on_stamp


@dataclass(frozen=True)
class _Verdict:
    status: SignatureStatus
    source: str | None
    clear: bool
    roi: Box
    features: InkFeatures
    object_found: bool


def _judge(
    roi: Box,
    page: PageText,
    pixels: _Pixels,
    objects: PageObjects | None,
    native: bool,
    meterai: list[Box],
    band: Box | None = None,
    rule_width: float | None = None,
) -> _Verdict:
    """Objects and pixels must agree: an object without ink, or ink without an object on a native page, is uncertain.
    The meterai's own colours are already gone from the pixels, and a meterai image is no signing object."""
    features = _ink(roi, page, pixels, band, rule_width)
    ink_status, ink_clear = ink_verdict(features)
    if not native and ink_status is SignatureStatus.UNSIGNED and pixels.threshold < FAINT_INK_THRESHOLD:
        faint = _ink(roi, page, replace(pixels, threshold=FAINT_INK_THRESHOLD), band, rule_width)
        if ink_verdict(faint)[0] is not SignatureStatus.UNSIGNED:
            ink_status, ink_clear = SignatureStatus.UNCERTAIN, False
    source, on_stamp = _object_source(roi, page, objects, native, meterai)
    signed, unsigned, uncertain = SignatureStatus.SIGNED, SignatureStatus.UNSIGNED, SignatureStatus.UNCERTAIN
    if source is None and on_stamp and ink_status is signed:
        source = SOURCE_IMAGE  # pen strokes left once the meterai's colours are gone: signed on the stamp's image

    if source == SOURCE_DIGITAL:  # a signed signature field is evidence on its own, even when invisible
        return _Verdict(signed, source, ink_status is signed, roi, features, True)
    if source is not None:
        # A signing object and signature-like ink agree: two independent signals, so the margin is clear.
        status = {signed: signed, uncertain: signed, unsigned: uncertain}[ink_status]
        return _Verdict(status, source, ink_status is signed, roi, features, True)
    if native and ink_status is signed:
        return _Verdict(uncertain, None, False, roi, features, False)  # ink no signing object explains
    roi_frac = _frac_box(roi, page.width, page.height)
    if not native and ink_status is unsigned and any(m.intersects(roi_frac) for m in meterai):
        # On a scan a faint pen over the meterai blends into its colours and is whitened with it; a meterai stuck
        # where the employee signs means the contract went out for signing.
        return _Verdict(uncertain, None, False, roi, features, False)
    return _Verdict(ink_status, SOURCE_PIXELS if ink_status is signed else None, ink_clear, roi, features, False)


def _confidence(status: SignatureStatus, clear: bool, native: bool) -> float:
    if status is SignatureStatus.SIGNED:
        return 0.95 if clear else 0.8
    if status is SignatureStatus.UNSIGNED:
        # New ways to sign a native PDF keep appearing, so a native "unsigned" never gets high confidence.
        return (0.8 if native else 0.9) if clear else 0.7
    return 0.5


def detect_signature(
    page: PageText,
    native: bool,
    image: np.ndarray,
    objects: PageObjects | None,
    employee_name: str | None,
    labels: tuple[str, ...] = EMPLOYEE_LABELS,
    meterai: list[Box] | None = None,
) -> SignatureResult:
    """`image` is the P_ttd render whose pixels line up with `page` coordinates (straightened for scans), with any
    meterai's own colours already turned white; `meterai` holds the stamps' boxes as page fractions."""
    stamps = meterai or []
    zone = signature_block(page)
    company = _phrase_boxes(zone, _COMPANY_LABEL)
    employee = _phrase_boxes(zone, labels)
    pixels = _pixels(page, image, native)
    heads, captions = _column_runs(zone, _median_word_height(page))
    heads += employee  # a matched label counts even when OCR garbled it

    verdicts: list[_Verdict] = []
    anchor: str | None = None
    names = name_anchors(zone, employee_name) if employee_name else []
    # A name match right above another in its column is the signature itself (a script font, the name written out),
    # not a second printed name.
    names = [
        upper
        for upper in names
        if not any(
            0 <= lower.top - upper.bottom < MIN_SIGN_SPACE * lower.height
            and upper.x1 > lower.x0
            and upper.x0 < lower.x1
            for lower in names
        )
    ]
    if names:
        anchor = ANCHOR_NAME
        for name in names:
            roi = roi_above(name, page, heads, company)
            verdict = _judge(roi, page, pixels, objects, native, stamps, signing_band(name, roi, captions))
            if verdict.status is SignatureStatus.UNSIGNED and not verdict.object_found:
                # Nothing above the name: a short name's signature may lie beside the ROI, or one is written below.
                wide = roi_above(name, page, heads, company, wide=True)
                tries = [(wide, signing_band(name, wide, captions), roi.width)] if wide.width > roi.width else []
                for other, band, rule_width in tries + [(roi_below(name, page, company, heads), None, None)]:
                    second = _judge(other, page, pixels, objects, native, stamps, band, rule_width)
                    if second.status is not SignatureStatus.UNSIGNED:
                        verdict = second
                        break
            verdicts.append(verdict)
    else:
        found = label_roi(employee or _phrase_boxes(zone, EMPLOYEE_HEADINGS), page, heads, company)
        if found is not None:
            anchor = ANCHOR_LABEL
            roi, printed = found
            band = signing_band(printed, roi, captions) if printed else None
            verdicts.append(_judge(roi, page, pixels, objects, native, stamps, band))

    outside = 0
    if objects is not None and objects.width:
        rois = [_frac_box(v.roi, page.width, page.height) for v in verdicts]
        outside = sum(
            1
            for _, box in objects.annotations
            if not any(_frac_box(box, objects.width, objects.height).intersects(r) for r in rois)
        )

    if not verdicts:
        return SignatureResult(SignatureStatus.UNCERTAIN, None, None, False, 0.0, annotations_outside_roi=outside)

    first = verdicts[0]
    first_roi = _frac_box(first.roi, page.width, page.height)
    in_roi = sum(1 for m in stamps if m.intersects(first_roi))
    if any(v.status is not first.status for v in verdicts):  # two anchored ROIs disagree
        return SignatureResult(
            SignatureStatus.UNCERTAIN, None, anchor, False, 0.5, None, None, outside, len(verdicts), in_roi
        )
    # A label anchor is weaker than the printed name, so it never reaches a clear margin.
    clear = first.clear and anchor == ANCHOR_NAME
    return SignatureResult(
        first.status,
        first.source,
        anchor,
        clear,
        _confidence(first.status, clear, native),
        _fractions(first.roi, page.width, page.height),
        first.features,
        outside,
        len(verdicts),
        in_roi,
    )
