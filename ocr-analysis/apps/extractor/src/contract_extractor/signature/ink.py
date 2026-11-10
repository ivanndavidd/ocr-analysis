"""Analisis tinta di ROI tanda tangan, sepenuhnya lokal (Bagian 11 PRD, jalur scan langkah 4-8).

Garis lurus panjang dan teks cetak dibuang dulu, karena garis tempat tanda tangan dan nama tercetak tidak boleh
dihitung sebagai tinta. Ambang di bawah adalah nilai awal untuk dikalibrasi di test set Fase 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from contract_extractor.common.models import SignatureStatus

SIGNED_INK = 0.015  # signed needs more than this share of dark pixels in the ROI
UNSIGNED_INK = 0.003  # unsigned needs less than this share
TALL_COMPONENT = 1.5  # signed needs one component taller than this many printed-word heights
STROKE_MIN = 0.15  # contour length per dark pixel; thin pen strokes score high, filled blobs low
MIN_LINE_PX = 40  # straight runs at least this long (and a third of the ROI) are rules, not ink
STAMP_CIRCULARITY = 0.75
TEXT_BOX_MIN_CONF = 0.70  # OCR boxes below this may be junk read off the signature itself

# The Rp10.000 meterai is a pink-magenta stamp, usually stuck where the employee signs. Its wavy pattern passes for
# ink, so an unsigned contract with a meterai used to read as signed. Measured on synthetic stamps only.
METERAI_HUE = (140, 179)  # OpenCV hue (0-179); blue pen ink sits near 100-125
METERAI_MIN_SATURATION = 50
PEN_MAX_VALUE = 90  # pixels this dark inside a meterai are pen ink drawn over it, whatever their hue
METERAI_MIN_FILL = 0.25  # share of the stamp's box in its colours; a purple pen signature covers far less
METERAI_SIDE = (0.025, 0.2)  # allowed side length, as a fraction of the page height
# A grayscale scan has no colour to go by. There a meterai is a solid area tinted darker than the paper (pattern and
# background cover about 90% of its box), while pen and text are thin strokes with paper between them.
GRAY_PAGE_MAX_SATURATION = 25  # 99.9th percentile of HSV saturation on a page without colour
METERAI_TINT = 20  # grey levels darker than the paper
METERAI_WINDOW = 0.015  # side of the density window, as a fraction of the page height
METERAI_DENSITY = 0.7  # tinted share of a window inside a meterai
METERAI_GRAY_FILL = 0.6  # tinted share of the whole box
# A black-and-white copy breaks the stamp's tint into wave lines with paper between them, so only its densest part (the
# printed `METERAI TEMPEL` and value) can pass as a core. On the 800-contract website run (2026-11-04) one core covered
# a fifth of its stamp and the rest of the waves read as a signature on an unsigned contract. A core smaller than a
# stamp grows to the pattern joined to it, when that is no larger than one. Of the page height; on that run the full
# stamps' cores measured 0.076-0.097 wide and 0.048-0.063 tall, and no other core on 800 signature pages grew.
METERAI_PART = (0.06, 0.04)  # a core narrower and lower than this is part of a stamp
METERAI_WHOLE = (0.11, 0.075)  # the largest a grown core may become
METERAI_JOIN = 0.0015  # closes breaks in the wave lines, not the gap to the printed name under the stamp
METERAI_RULE = 0.1  # straight runs this long (the signing line touching the stamp) are not part of it

# Stroke check, for scans and photos the ink amount leaves uncertain (see `measure_strokes`). On the 473 synthetic
# scans and photos (2026-11-08) the ink share left 152 uncertain: blank boxes held 0.3-1.5% "ink" from a bold printed
# name spilling past its OCR box, dotted signing lines and shaded paper, and thin, faint or script signatures as little.
# All lengths and sizes are in printed-word heights.
STROKE_CONTRAST = 30  # grey levels darker than the paper around it
FAINT_STROKE_CONTRAST = 20
STROKE_PAPER = 1.5  # the closing that estimates the paper is wider than any pen stroke
TEXT_SPILL = 0.3  # how far a bold printed letter reaches past its OCR box
TEXT_SPILL_HEIGHT = 1.3  # a taller mark touching a word box is a pen stroke over the print
LINE_PIECE = 0.2  # a mark this thin one way is a straight piece: a rule's end, a page edge
MAX_STROKE_WIDTH = 0.35  # a pen stroke is thinner; a page edge or shadow against the paper is not
DOT_GAP = 0.5  # the gap a dotted signing line's dots are joined across
DOT_HEIGHT = 0.35  # a dotted line is no taller than this; handwriting is
# Pen path above the name that reads signed. Every unsigned ROI above the name on the 800 synthetic contracts that
# reached the stroke check measured 0; the signatures it decided measured 5 to 60.
SIGNED_STROKES = 5.0
UNSIGNED_STROKES = 0.5  # at the faint contrast, no more than this reads unsigned


@dataclass(frozen=True)
class InkFeatures:
    ink_ratio: float
    tallest: float  # tallest remaining component, in printed-word heights
    large_components: int
    stroke_ratio: float
    looks_like_text: bool


@dataclass(frozen=True)
class StrokeFeatures:
    length: float  # pen-like path outside any meterai, in printed-word heights
    meterai_length: float  # marks lying mostly inside a meterai box, in printed-word heights


def _remove_rules(ink: np.ndarray, rule_px: int | None = None) -> np.ndarray:
    """Morphological opening finds long horizontal and vertical runs (signature lines, table borders): a third of the
    ROI, or `rule_px` long."""
    h, w = ink.shape
    length = max(MIN_LINE_PX, rule_px or w // 3)
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(MIN_LINE_PX, h // 3))))
    return cv2.subtract(ink, cv2.bitwise_or(horizontal, vertical))


def _inside_any(x: int, y: int, w: int, h: int, boxes: list[tuple[int, int, int, int]]) -> bool:
    return any(bx0 <= x and by0 <= y and x + w <= bx1 and y + h <= by1 for bx0, by0, bx1, by1 in boxes)


def _circularity(mask: np.ndarray) -> float:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return 0.0
    outer = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(outer, True)
    return 4 * math.pi * cv2.contourArea(outer) / (perimeter * perimeter) if perimeter else 0.0


def measure_ink(
    roi: np.ndarray,
    threshold: float,
    word_height_px: float,
    text_boxes: list[tuple[int, int, int, int]],
    area_px: float | None = None,
    rule_px: int | None = None,
) -> InkFeatures:
    """`roi` is grayscale; `text_boxes` are printed-word boxes (x0, y0, x1, y1) in ROI pixels; `area_px` is what the
    ink ratio is measured against, the whole ROI by default; `rule_px`: the shortest horizontal run taken for a rule.

    A component that lies wholly inside a printed-word box is text. One that crosses a box edge is kept,
    because a signature written over the printed name merges with its letters.
    """
    area = float(area_px or roi.size) or 1.0
    word_h = max(word_height_px, 1.0)
    ink = np.where(roi < threshold, 255, 0).astype(np.uint8)
    ink = _remove_rules(ink, rule_px)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros_like(ink)
    heights: list[int] = []
    centres: list[float] = []
    for i in range(1, count):
        x, y, w, h, pixels = (int(v) for v in stats[i])
        if pixels < max(4, int((0.05 * word_h) ** 2)):
            continue  # specks and scan noise
        if _inside_any(x, y, w, h, text_boxes):
            continue
        if h <= word_h and w <= word_h:
            continue  # a stray printed glyph or punctuation mark
        component = np.where(labels[y : y + h, x : x + w] == i, 255, 0).astype(np.uint8)
        if h > 2 * word_h and _circularity(component) > STAMP_CIRCULARITY:
            continue  # round company stamp
        keep[y : y + h, x : x + w] |= component
        heights.append(h)
        centres.append(y + h / 2)

    dark = int(np.count_nonzero(keep))
    contours, _ = cv2.findContours(keep, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)

    looks_like_text = False
    if len(heights) >= 4:
        median_h = float(np.median(heights))
        uniform = all(0.6 * median_h <= h <= 1.6 * median_h for h in heights)
        one_row = float(np.std(centres)) < 0.3 * median_h
        looks_like_text = uniform and one_row and max(heights) <= TALL_COMPONENT * word_h

    return InkFeatures(
        ink_ratio=dark / area,
        tallest=(max(heights) / word_h) if heights else 0.0,
        large_components=sum(1 for h in heights if h > word_h),
        stroke_ratio=(perimeter / dark) if dark else 0.0,
        looks_like_text=looks_like_text,
    )


def _odd(value: float) -> int:
    return max(3, int(value)) | 1


def _remove_dotted(ink: np.ndarray, word_h: float, min_length: int) -> np.ndarray:
    """A dotted or dashed signing line: small marks in a row, joined across their gaps into a thin rule."""
    def rect(w: int, h: int) -> np.ndarray:
        return cv2.getStructuringElement(cv2.MORPH_RECT, (w, h))

    joined = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, rect(_odd(DOT_GAP * word_h), 1))
    line = cv2.morphologyEx(joined, cv2.MORPH_OPEN, rect(min_length, 1))
    tall = cv2.morphologyEx(line, cv2.MORPH_OPEN, rect(1, _odd(DOT_HEIGHT * word_h)))
    line = cv2.dilate(cv2.subtract(line, tall), np.ones((3, 3), np.uint8))  # a band of handwriting is too tall
    return cv2.subtract(ink, line)


def _touches(x: int, y: int, w: int, h: int, boxes: list[tuple[float, float, float, float]]) -> bool:
    return any(x < bx1 and x + w > bx0 and y < by1 and y + h > by0 for bx0, by0, bx1, by1 in boxes)


def measure_strokes(
    roi: np.ndarray,
    word_height_px: float,
    text_boxes: list[tuple[int, int, int, int]],
    name_row: tuple[float, float],
    meterai_boxes: list[tuple[int, int, int, int]],
    contrast: float,
    rule_px: int | None = None,
) -> StrokeFeatures:
    """How much pen-like path the ROI holds: the shape of its marks, not the amount of ink.

    Ink is what lies `contrast` grey levels under the paper around it, so shading and tinted paper go while a faint
    pen stays. Dropped: long rules, dotted lines, specks, single glyphs, round stamps, marks inside a word box,
    letter-high marks touching one (bold print spilling past its OCR box), marks inside the printed name's own row
    (`name_row`, ROI pixel rows, that OCR may have read with too little confidence to box), straight pieces, marks
    thicker than a pen, and marks lying mostly in a meterai (counted apart). What is left is summed as path length,
    half each mark's contour. `text_boxes` and `meterai_boxes` are (x0, y0, x1, y1) in ROI pixels.
    """
    word_h = max(word_height_px, 1.0)
    k = _odd(STROKE_PAPER * word_h)
    paper = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ink = np.where(cv2.subtract(paper, roi) > contrast, 255, 0).astype(np.uint8)
    ink = _remove_rules(ink, rule_px)
    ink = _remove_dotted(ink, word_h, max(MIN_LINE_PX, (rule_px or roi.shape[1] // 3) // 2))

    pad = TEXT_SPILL * word_h
    spill = [(x0 - pad, y0 - pad, x1 + pad, y1 + pad) for x0, y0, x1, y1 in text_boxes]
    row_top, row_bottom = name_row[0] - pad, name_row[1] + pad
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    length = meterai_length = 0.0
    for i in range(1, count):
        x, y, w, h, pixels = (int(v) for v in stats[i])
        if pixels < max(4, int((0.05 * word_h) ** 2)) or _inside_any(x, y, w, h, text_boxes):
            continue
        mask = np.where(labels[y : y + h, x : x + w] == i, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        perimeter = sum(cv2.arcLength(c, True) for c in contours)
        on_stamp = sum(
            int(np.count_nonzero(mask[max(0, my0 - y) : max(0, my1 - y), max(0, mx0 - x) : max(0, mx1 - x)]))
            for mx0, my0, mx1, my1 in meterai_boxes
        )
        if on_stamp >= 0.5 * pixels:
            meterai_length += perimeter / 2
            continue
        glyph = h <= word_h and w <= word_h
        stamp = h > 2 * word_h and _circularity(mask) > STAMP_CIRCULARITY
        spilled = h <= TEXT_SPILL_HEIGHT * word_h and _touches(x, y, w, h, spill)
        printed = spilled or (y >= row_top and y + h <= row_bottom)
        straight = min(w, h) <= LINE_PIECE * word_h
        thick = perimeter == 0 or 2 * pixels / perimeter > MAX_STROKE_WIDTH * word_h
        if not (glyph or stamp or printed or straight or thick):
            length += perimeter / 2
    return StrokeFeatures(length=length / word_h, meterai_length=meterai_length / word_h)


def _meterai_colour(hsv: np.ndarray) -> np.ndarray:
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return (
        (hue >= METERAI_HUE[0]) & (hue <= METERAI_HUE[1]) & (saturation >= METERAI_MIN_SATURATION) & (value > PEN_MAX_VALUE)
    )


def meterai_boxes(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Pixel boxes (x0, y0, x1, y1) of meterai-like stamps: dense pink-magenta rectangles of stamp size."""
    height = rgb.shape[0]
    colour = _meterai_colour(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV))
    size = max(3, int(0.01 * height)) | 1  # joins the stamp's pattern into one blob; odd, so the closing does not shift it
    joined = cv2.morphologyEx(colour.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    low, high = METERAI_SIDE[0] * height, METERAI_SIDE[1] * height
    boxes = []
    for i in range(1, count):
        x, y, w, h, _ = (int(v) for v in stats[i])
        if low <= w <= high and low <= h <= high and colour[y : y + h, x : x + w].mean() >= METERAI_MIN_FILL:
            boxes.append((x, y, x + w, y + h))
    return boxes


def meterai_pixels(rgb: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    """True where a meterai shows its own colours, to be turned white before measuring ink. Pen strokes over the
    stamp stay: dark ones, and saturated ones of another hue (blue ink). A faint grey stroke over it is lost."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = np.zeros(rgb.shape[:2], dtype=bool)
    for x0, y0, x1, y1 in boxes:
        patch = hsv[y0:y1, x0:x1]
        hue, saturation, value = patch[..., 0], patch[..., 1], patch[..., 2]
        other_hue = (saturation >= METERAI_MIN_SATURATION) & ((hue < METERAI_HUE[0]) | (hue > METERAI_HUE[1]))
        mask[y0:y1, x0:x1] = ~((value <= PEN_MAX_VALUE) | other_hue)
    return mask


def is_grayscale(rgb: np.ndarray) -> bool:
    saturation = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[..., 1]
    return float(np.percentile(saturation, 99.9)) < GRAY_PAGE_MAX_SATURATION


def meterai_boxes_gray(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Pixel boxes of meterai-like stamps on a page without colour: solid tinted areas of stamp size."""
    height = gray.shape[0]
    tinted = (gray < float(np.median(gray)) - METERAI_TINT).astype(np.float32)  # the median pixel is paper
    window = max(3, int(METERAI_WINDOW * height)) | 1
    dense = (cv2.boxFilter(tinted, -1, (window, window)) >= METERAI_DENSITY).astype(np.uint8) * 255
    dense = cv2.dilate(dense, np.ones((window, window), np.uint8))  # back out to the stamp's edges
    count, _, stats, _ = cv2.connectedComponentsWithStats(dense, connectivity=8)
    low, high = METERAI_SIDE[0] * height, METERAI_SIDE[1] * height
    boxes = []
    for i in range(1, count):
        x, y, w, h, _ = (int(v) for v in stats[i])
        if low <= w <= high and low <= h <= high and tinted[y : y + h, x : x + w].mean() >= METERAI_GRAY_FILL:
            boxes.append((x, y, x + w, y + h))
    return _whole_stamps(tinted, boxes)


def _whole_stamps(tinted: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Each core smaller than a stamp, grown to the pattern joined to it (see METERAI_PART); a full-size core stays as
    it is, so a signature that touches its stamp is never taken in."""
    height = tinted.shape[0]
    small = [b for b in boxes if b[2] - b[0] < METERAI_PART[0] * height and b[3] - b[1] < METERAI_PART[1] * height]
    if not small:
        return boxes
    dark = _remove_rules(tinted.astype(np.uint8) * 255, int(METERAI_RULE * height))
    size = max(3, int(METERAI_JOIN * height)) | 1
    joined = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    grown: list[tuple[int, int, int, int]] = []
    for box in boxes:
        x0, y0, x1, y1 = box
        if box in small:
            for i in {int(v) for v in np.unique(labels[y0:y1, x0:x1])} - {0}:
                x, y, w, h, _ = (int(v) for v in stats[i])
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h)
            if x1 - x0 > METERAI_WHOLE[0] * height or y1 - y0 > METERAI_WHOLE[1] * height:
                x0, y0, x1, y1 = box  # joined to more than a stamp: keep the core alone
        if (x0, y0, x1, y1) not in grown:
            grown.append((x0, y0, x1, y1))
    return grown


def find_meterai(rgb: np.ndarray, gray: np.ndarray) -> tuple[list[tuple[int, int, int, int]], np.ndarray]:
    """Meterai boxes, and the pixels to turn white before measuring ink. On a colour page only the stamp's own
    colours go. On a grayscale page pen and stamp look alike, so the whole box goes: a signature over it is lost
    (at worst uncertain on a scan) rather than the stamp's pattern read as one (unsigned scans read as signed)."""
    if not is_grayscale(rgb):
        boxes = meterai_boxes(rgb)
        return boxes, meterai_pixels(rgb, boxes)
    boxes = meterai_boxes_gray(gray)
    mask = np.zeros(gray.shape, dtype=bool)
    for x0, y0, x1, y1 in boxes:
        mask[y0:y1, x0:x1] = True
    return boxes, mask


def ink_verdict(features: InkFeatures) -> tuple[SignatureStatus, bool]:
    """Status from pixels alone, and whether it clears its threshold by a wide margin."""
    if features.ink_ratio < UNSIGNED_INK:
        return SignatureStatus.UNSIGNED, features.ink_ratio < UNSIGNED_INK / 2
    signature_like = (
        features.ink_ratio > SIGNED_INK
        and features.tallest >= TALL_COMPONENT
        and features.stroke_ratio >= STROKE_MIN
        and not features.looks_like_text
    )
    if signature_like:
        return SignatureStatus.SIGNED, features.ink_ratio >= 2 * SIGNED_INK
    return SignatureStatus.UNCERTAIN, False
