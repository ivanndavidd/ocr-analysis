"""Preprocessing citra scan/foto sebelum OCR (Bagian 9 PRD, langkah 1)."""

from __future__ import annotations

import cv2
import numpy as np

# Larger rotations are more likely a misdetection than a real skew; leave those pages as they are.
MAX_DESKEW_DEG = 15.0


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


def straighten(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Shadow removal and deskew. Deterministic, so signature analysis can rebuild the exact image OCR saw
    and reuse its word coordinates. Returns the grayscale image and the applied angle."""
    flat = remove_shadows(gray)
    angle = estimate_skew(flat)
    straight = deskew(flat, angle)
    return straight, (angle if straight is not flat else 0.0)


def preprocess(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Shadow removal, deskew, adaptive threshold. Returns the image for OCR and the applied angle."""
    straight, applied = straighten(gray)
    return binarize(straight), applied
