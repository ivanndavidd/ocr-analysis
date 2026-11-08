"""Interface OCR yang sama untuk Tesseract, PaddleOCR, dan OCR cloud (Bagian 9 PRD)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from contract_extractor.common.models import Word

if TYPE_CHECKING:
    import numpy as np


class OcrEngineError(Exception):
    """One OCR call failed (timeout, crash); the ladder moves on to the next provider."""


class OCRProvider(Protocol):
    name: str
    is_local: bool  # False means page pixels leave the machine; must be logged per file

    def read(self, image: np.ndarray) -> list[Word]: ...
