"""Analisis tinta di ROI tanda tangan, sepenuhnya lokal (Bagian 11 PRD, jalur scan langkah 4-7).

Garis lurus panjang dan teks cetak dibuang dulu, karena garis tempat tanda tangan dan nama tercetak tidak boleh
dihitung sebagai tinta. Ambang di bawah adalah nilai awal untuk dikalibrasi di test set Fase 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from contract_extractor.models import SignatureStatus

SIGNED_INK = 0.015  # signed needs more than this share of dark pixels in the ROI
UNSIGNED_INK = 0.003  # unsigned needs less than this share
TALL_COMPONENT = 1.5  # signed needs one component taller than this many printed-word heights
STROKE_MIN = 0.15  # contour length per dark pixel; thin pen strokes score high, filled blobs low
MIN_LINE_PX = 40  # straight runs at least this long (and a third of the ROI) are rules, not ink
STAMP_CIRCULARITY = 0.75
TEXT_BOX_MIN_CONF = 0.70  # OCR boxes below this may be junk read off the signature itself


@dataclass(frozen=True)
class InkFeatures:
    ink_ratio: float
    tallest: float  # tallest remaining component, in printed-word heights
    large_components: int
    stroke_ratio: float
    looks_like_text: bool


def _remove_rules(ink: np.ndarray) -> np.ndarray:
    """Morphological opening finds long horizontal and vertical runs (signature lines, table borders)."""
    h, w = ink.shape
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(MIN_LINE_PX, w // 3), 1)))
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
    roi: np.ndarray, threshold: float, word_height_px: float, text_boxes: list[tuple[int, int, int, int]]
) -> InkFeatures:
    """`roi` is grayscale; `text_boxes` are printed-word boxes (x0, y0, x1, y1) in ROI pixels.

    A component that lies wholly inside a printed-word box is text. One that crosses a box edge is kept,
    because a signature written over the printed name merges with its letters.
    """
    area = float(roi.size) or 1.0
    word_h = max(word_height_px, 1.0)
    ink = np.where(roi < threshold, 255, 0).astype(np.uint8)
    ink = _remove_rules(ink)

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
