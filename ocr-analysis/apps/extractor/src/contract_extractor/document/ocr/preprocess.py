"""Preprocessing citra scan/foto sebelum OCR (Bagian 9 PRD, langkah 1)."""

from __future__ import annotations

import cv2
import numpy as np

# Larger rotations are more likely a misdetection than a real skew; leave those pages as they are.
MAX_DESKEW_DEG = 15.0
# A table border is at least 1/8 of the page wide or 1/20 of it tall: about 310 and 175 px at 300 dpi. An underlined
# date or name is shorter and stays, a letter (30 to 45 px tall at 11 pt) far shorter.
RULE_MIN_W = 8
RULE_MIN_H = 20


def remove_shadows(gray: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting from phone photos by dividing out a blurred background estimate."""
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 21)
    flattened = 255 - cv2.absdiff(gray, background)
    return cv2.normalize(flattened, None, 0, 255, cv2.NORM_MINMAX)


def estimate_skew(gray: np.ndarray) -> float:
    """Dominant text-line angle in degrees (positive = counter-clockwise), from merged line blobs."""
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel_w = max(15, gray.shape[1] // 60)
    blobs = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 3)))
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    angles = []
    for contour in contours:
        rect = cv2.minAreaRect(contour)
        long_side, short_side = max(rect[1]), min(rect[1])
        if long_side < gray.shape[1] * 0.15 or short_side == 0 or long_side / short_side < 5:
            continue
        # Measure the long edge directly; minAreaRect's own angle convention differs across OpenCV versions.
        box = cv2.boxPoints(rect)
        p, q = max(((box[i], box[(i + 1) % 4]) for i in range(4)), key=lambda e: float(np.hypot(*(e[1] - e[0]))))
        dx, dy = q - p
        angle = -float(np.degrees(np.arctan2(dy, dx)))  # image y grows downward
        if angle > 90:
            angle -= 180
        elif angle <= -90:
            angle += 180
        if abs(angle) <= 45:
            angles.append(angle)
    return float(np.median(angles)) if angles else 0.0


def deskew(gray: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.1 or abs(angle) > MAX_DESKEW_DEG:
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)


def binarize(gray: np.ndarray) -> np.ndarray:
    block = max(15, (gray.shape[1] // 80) | 1)  # odd, scales with resolution
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 15)


def erase_rules(binary: np.ndarray) -> np.ndarray:
    """White out table borders in a binarized page: straight strokes much longer than any letter or underline.

    Tesseract read the borders of a ruled identity table as text glued to the values (`(:13226...` for a NIK), and
    with a border round every row it dropped whole rows: a synthetic scan's name and NIK rows went unread, so the name
    could not be confirmed, no NIK was found, and the text stayed local. With the lines erased every row read."""
    ink = 255 - binary
    h, w = binary.shape
    rows = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // RULE_MIN_W, 1)))
    columns = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // RULE_MIN_H)))
    # Grown across the line only, to take its ragged edge without eating into the letters along it.
    rules = cv2.bitwise_or(cv2.dilate(rows, np.ones((3, 1), np.uint8)), cv2.dilate(columns, np.ones((1, 3), np.uint8)))
    return np.where(rules > 0, 255, binary).astype(np.uint8)


def straighten(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Shadow removal and deskew. Deterministic, so signature analysis can rebuild the exact image OCR saw
    and reuse its word coordinates. Returns the grayscale image and the applied angle."""
    flat = remove_shadows(gray)
    angle = estimate_skew(flat)
    straight = deskew(flat, angle)
    return straight, (angle if straight is not flat else 0.0)


def preprocess(gray: np.ndarray, erase: bool = False) -> tuple[np.ndarray, float]:
    """Shadow removal, deskew, adaptive threshold, and with `erase` the table borders whited out. Returns the image
    for OCR and the applied angle; erasing moves nothing, so the word coordinates still fit the straightened page."""
    straight, applied = straighten(gray)
    binary = binarize(straight)
    return (erase_rules(binary) if erase else binary), applied
