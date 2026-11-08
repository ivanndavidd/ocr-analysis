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


@dataclass(frozen=True)
class InkFeatures:
    ink_ratio: float
    tallest: float  # tallest remaining component, in printed-word heights
    large_components: int
    stroke_ratio: float
    looks_like_text: bool


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
