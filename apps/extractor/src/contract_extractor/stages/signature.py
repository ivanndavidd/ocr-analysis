"""Tahap 4: deteksi tanda tangan karyawan, sepenuhnya lokal (Bagian 11 PRD).

ROI selalu diikat ke nama karyawan tercetak (atau sebutan karyawan bila nama tidak terbaca) di blok tanda tangan,
yaitu zona penutup tanpa baris paragrafnya.
Sistem tidak pernah memilih tinta terbesar di halaman: jangkar tidak ditemukan berarti `uncertain`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import cv2
import numpy as np
from rapidfuzz import fuzz

from contract_extractor.layout import Line, PageText
from contract_extractor.models import SignatureStatus, Word
from contract_extractor.pdf.objects import Box, PageObjects
from contract_extractor.stages.filename import normalize_name
from contract_extractor.stages.ink import TEXT_BOX_MIN_CONF, InkFeatures, ink_verdict, measure_ink
from contract_extractor.stages.page_selection import signature_block

EMPLOYEE_LABELS = ("pihak kedua", "tenaga kerja", "karyawan", "pekerja")
_COMPANY_LABEL = re.compile(r"^(?:pihak pertama|pengusaha|perusahaan|direktur|pt)$")

NAME_MATCH_MIN = 85
LABEL_MATCH_MIN = 85  # OCR can merge or garble a label under a signature stroke (`PIHAKKEDUA`)
SINGLE_TOKEN_MATCH_MIN = 90  # a one-word name (`Abdkarim`) needs a closer match
MAX_WORD_GAP = 2.0  # words further apart than this many word heights belong to different columns
ROI_WIDTH = 2.5  # times the name width, centred on the name
ROI_HEIGHT = 5.0  # times the name line height
MAX_ROI_WIDTH = 0.5  # of the page width
VECTOR_MIN_SEGMENTS = 8
VECTOR_MIN_LENGTH = 1.0  # total stroke length, in ROI widths; a tick or a short underline stays below it

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


def _windows(line: Line, max_len: int) -> Iterator[tuple[Word, ...]]:
    """Runs of up to max_len adjacent words; a wide gap ends the run, so two columns never merge."""
    words = line.words
    for i in range(len(words)):
        for k in range(1, max_len + 1):
            group = words[i : i + k]
            if len(group) < k:
                break
            if k > 1:
                a, b = group[-2], group[-1]
                if b.bbox[0] - a.bbox[2] > MAX_WORD_GAP * (a.bbox[3] - a.bbox[1]):
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
        for group in _windows(line, len(tokens) + 1):
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


def roi_above(name: Box, page: PageText, labels: list[Box], company: list[Box]) -> Box:
    """From the name's bottom edge upward, so a signature written over the printed name still counts."""
    x0, x1 = _column((name.x0 + name.x1) / 2, ROI_WIDTH * name.width / 2, page, company)
    top = name.bottom - ROI_HEIGHT * name.height
    for label in labels:  # stop early at an employee label above the name, in the same column
        if label.bottom <= name.top and label.bottom > top and label.x1 > x0 and label.x0 < x1:
            top = label.bottom
    return Box(x0, max(0.0, top), x1, name.bottom)


def roi_below(name: Box, page: PageText, company: list[Box]) -> Box:
    x0, x1 = _column((name.x0 + name.x1) / 2, ROI_WIDTH * name.width / 2, page, company)
    return Box(x0, name.top, x1, min(page.height, name.top + ROI_HEIGHT * name.height))


def label_roi(labels: list[Box], page: PageText, company: list[Box]) -> Box | None:
    """Secondary anchor: between two stacked employee labels, or below a single one."""
    for upper in labels:
        for lower in labels:
            stacked = lower.top > upper.bottom + upper.height and lower.x0 < upper.x1 and upper.x0 < lower.x1
            if stacked:
                width = max(upper.width, lower.width)
                x0, x1 = _column((upper.x0 + upper.x1) / 2, ROI_WIDTH * width / 2, page, company)
                return Box(x0, upper.bottom, x1, lower.top)
    if len(labels) == 1:
        label = labels[0]
        x0, x1 = _column((label.x0 + label.x1) / 2, ROI_WIDTH * label.width / 2, page, company)
        return Box(x0, label.bottom, x1, min(page.height, label.bottom + ROI_HEIGHT * label.height))
    return None


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


def _pixels(page: PageText, image: np.ndarray) -> _Pixels:
    h, w = image.shape
    sx, sy = w / page.width, h / page.height
    otsu, _ = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    heights = sorted(word.bbox[3] - word.bbox[1] for word in page.words)
    median_h = heights[len(heights) // 2] if heights else 10.0
    boxes = [
        (int(word.bbox[0] * sx) - 2, int(word.bbox[1] * sy) - 2, int(word.bbox[2] * sx) + 2, int(word.bbox[3] * sy) + 2)
        for word in page.words
        # OCR may read a signature as tall, low-confidence junk words; those boxes must not hide its ink.
        if word.conf >= TEXT_BOX_MIN_CONF and (word.bbox[3] - word.bbox[1]) <= 1.6 * median_h
    ]
    return _Pixels(image, float(min(max(otsu, 80.0), 180.0)), median_h * sy, boxes)


def _ink(roi: Box, page: PageText, pixels: _Pixels) -> InkFeatures:
    h, w = pixels.image.shape
    fx0, ft, fx1, fb = _fractions(roi, page.width, page.height)
    x0, y0, x1, y1 = int(fx0 * w), int(ft * h), int(fx1 * w), int(fb * h)
    crop = pixels.image[y0:y1, x0:x1]
    local = [(bx0 - x0, by0 - y0, bx1 - x0, by1 - y0) for bx0, by0, bx1, by1 in pixels.text_boxes if bx1 > x0 and bx0 < x1 and by1 > y0 and by0 < y1]
    return measure_ink(crop, pixels.threshold, pixels.word_height, local)


def _object_source(roi: Box, page: PageText, objects: PageObjects | None, native: bool) -> str | None:
    if objects is None or not objects.width:
        return None
    roi_frac = _frac_box(roi, page.width, page.height)

    def hit(box: Box) -> bool:
        return _frac_box(box, objects.width, objects.height).intersects(roi_frac)

    if any(hit(box) for box in objects.signed_fields):
        return SOURCE_DIGITAL
    if any(hit(box) for _, box in objects.annotations):
        return SOURCE_ANNOTATION
    if not native:
        return None  # a scan's images are the page itself; only annotations are checked there
    if any(hit(box) for box in objects.images):
        return SOURCE_IMAGE
    roi_width_pt = (roi.x1 - roi.x0) / page.width * objects.width
    inside = [v for v in objects.vectors if hit(v.box)]
    if sum(v.segments for v in inside) >= VECTOR_MIN_SEGMENTS and sum(v.length for v in inside) >= VECTOR_MIN_LENGTH * roi_width_pt:
        return SOURCE_VECTOR
    return None


@dataclass(frozen=True)
class _Verdict:
    status: SignatureStatus
    source: str | None
    clear: bool
    roi: Box
    features: InkFeatures
    object_found: bool


def _judge(roi: Box, page: PageText, pixels: _Pixels, objects: PageObjects | None, native: bool) -> _Verdict:
    """Objects and pixels must agree: an object without ink, or ink without an object on a native page, is uncertain."""
    features = _ink(roi, page, pixels)
    ink_status, ink_clear = ink_verdict(features)
    source = _object_source(roi, page, objects, native)
    signed, unsigned, uncertain = SignatureStatus.SIGNED, SignatureStatus.UNSIGNED, SignatureStatus.UNCERTAIN

    if source == SOURCE_DIGITAL:  # a signed signature field is evidence on its own, even when invisible
        return _Verdict(signed, source, ink_status is signed, roi, features, True)
    if source is not None:
        # A signing object and signature-like ink agree: two independent signals, so the margin is clear.
        status = {signed: signed, uncertain: signed, unsigned: uncertain}[ink_status]
        return _Verdict(status, source, ink_status is signed, roi, features, True)
    if native and ink_status is signed:
        return _Verdict(uncertain, None, False, roi, features, False)  # ink no signing object explains
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
) -> SignatureResult:
    """`image` is the P_ttd render whose pixels line up with `page` coordinates (straightened for scans)."""
    zone = signature_block(page)
    company = _phrase_boxes(zone, _COMPANY_LABEL)
    employee = _phrase_boxes(zone, labels)
    pixels = _pixels(page, image)

    verdicts: list[_Verdict] = []
    anchor: str | None = None
    names = name_anchors(zone, employee_name) if employee_name else []
    if names:
        anchor = ANCHOR_NAME
        for name in names:
            verdict = _judge(roi_above(name, page, employee, company), page, pixels, objects, native)
            if verdict.status is SignatureStatus.UNSIGNED and not verdict.object_found:
                below = _judge(roi_below(name, page, company), page, pixels, objects, native)
                if below.status is not SignatureStatus.UNSIGNED:
                    verdict = below
            verdicts.append(verdict)
    else:
        roi = label_roi(employee, page, company)
        if roi is not None:
            anchor = ANCHOR_LABEL
            verdicts.append(_judge(roi, page, pixels, objects, native))

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
    if any(v.status is not first.status for v in verdicts):  # two anchored ROIs disagree
        return SignatureResult(
            SignatureStatus.UNCERTAIN, None, anchor, False, 0.5, None, None, outside, len(verdicts)
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
    )
