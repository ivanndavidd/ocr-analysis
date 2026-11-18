"""The 'claude_page' mode's image (owner decision 2026-11-16, approved by the mentor): the identity page as Claude sees
it, with the NIK blacked out.

Local OCR has already read the page, so it knows where the NIK is printed (`nik_spot`). That box, and every word or
run of digit words holding a long run of digits (another NIK, a KK or NPWP number), is painted black on the page
image before it is encoded. Only the identity page goes, never the signature page: the NIK and the signature stay
with the local reading. A page set whose NIK cannot be located is never sent; the pipeline then reads the masked text
as in the OCR mode. Nothing here sends anything: it only builds the images.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import cv2
import numpy as np

from contract_extractor.common.layout import PageText
from contract_extractor.extractor_method.pattern.nik import nik_spot

# Big enough to read small print and a hand-filled date, small enough for one request: an image costs about
# width x height / 750 tokens, so a 1414 x 2000 A4 page is about 3,800. A model that reads fewer pixels scales it down.
PAGE_MAX_SIDE = 2000
PAGE_JPEG_QUALITY = 85
# A NIK is 16 digits; 10 also covers one OCR cut short, while a date (8 digits, split by separators) or an amount
# (runs of 3) stays readable.
LONG_DIGITS = 10
PAD = 0.3  # of a word's height, around every painted word box
_DIGITS = re.compile(r"\d+")

Box = tuple[float, float, float, float]  # x0, top, x1, bottom as fractions of the page


def masked_jpegs(pages: list[PageText], image_of: Callable[[PageText], np.ndarray | None]) -> list[bytes] | None:
    """The identity pages as JPEG with the NIK blacked out, in order; None when the NIK cannot be located (no label,
    or several different numbers) or a page image cannot be rebuilt, since then nothing proves the NIK is covered.

    `image_of` gives a page's pixels in the frame its word boxes use (the pipeline's `_page_image`)."""
    spot = nik_spot(pages)
    if spot is None:
        return None
    jpegs = []
    for index, page in enumerate(pages):
        image = image_of(page)
        if image is None:
            return None
        boxes = list(long_digit_boxes(page))
        if index == spot.page:
            boxes.append(spot.box)
        jpegs.append(_jpeg(paint(image, boxes)))
    return jpegs


def long_digit_boxes(page: PageText) -> Iterable[Box]:
    """A box, in page fractions, around each word holding LONG_DIGITS digits in a row, and around each run of
    neighbouring all-digit words on one line that together hold that many (OCR often reads `3201 0101 9001 0001`)."""
    for line in page.lines:
        run: list = []
        for word in [*line.words, None]:
            if word is not None and _DIGITS.fullmatch(word.text):
                run.append(word)
                continue
            if sum(len(w.text) for w in run) >= LONG_DIGITS:
                yield _fraction(page, run)
            run = []
            if word is not None and max((len(m) for m in _DIGITS.findall(word.text)), default=0) >= LONG_DIGITS:
                yield _fraction(page, [word])


def _fraction(page: PageText, words: list) -> Box:
    x0 = min(w.bbox[0] for w in words)
    top = min(w.bbox[1] for w in words)
    x1 = max(w.bbox[2] for w in words)
    bottom = max(w.bbox[3] for w in words)
    pad = PAD * (bottom - top)
    return (
        max(0.0, (x0 - pad) / page.width),
        max(0.0, (top - pad) / page.height),
        min(1.0, (x1 + pad) / page.width),
        min(1.0, (bottom + pad) / page.height),
    )


def paint(image: np.ndarray, boxes: list[Box]) -> np.ndarray:
    """A copy of the page with each box filled black."""
    out = image.copy()
    height, width = out.shape[:2]
    for x0, top, x1, bottom in boxes:
        out[int(top * height) : max(int(top * height) + 1, round(bottom * height)),
            int(x0 * width) : max(int(x0 * width) + 1, round(x1 * width))] = 0  # fmt: skip
    return out


def _jpeg(image: np.ndarray) -> bytes:
    height, width = image.shape[:2]
    scale = min(1.0, PAGE_MAX_SIDE / max(height, width))
    if scale < 1.0:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, PAGE_JPEG_QUALITY])
    if not ok:
        raise ValueError("page could not be encoded")
    return encoded.tobytes()
