"""Tesseract 5 lokal lewat pytesseract (argumen dikirim sebagai list, tanpa shell)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytesseract

from contract_extractor.models import Word
from contract_extractor.ocr.base import OcrEngineError


class TesseractUnavailable(Exception):
    pass


class TesseractOCR:
    name = "tesseract"
    is_local = True

    def __init__(self, cmd: Path, lang: str, psm: int = 6, timeout_s: int = 60) -> None:
        # pytesseract keeps the binary path in module state; this process only ever uses one binary.
        pytesseract.pytesseract.tesseract_cmd = str(cmd)
        self.lang = lang
        self.psm = psm
        self.timeout_s = timeout_s

    def check(self) -> None:
        try:
            pytesseract.get_tesseract_version()
            available = set(pytesseract.get_languages(config=""))
        except (pytesseract.TesseractNotFoundError, OSError) as exc:
            raise TesseractUnavailable("tesseract binary cannot be started") from exc
        missing = set(self.lang.split("+")) - available
        if missing:
            raise TesseractUnavailable(f"tesseract language data missing: {', '.join(sorted(missing))}")

    def read(self, image: np.ndarray) -> list[Word]:
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self.lang,
                config=f"--psm {self.psm}",
                output_type=pytesseract.Output.DICT,
                timeout=self.timeout_s,
            )
        except (RuntimeError, pytesseract.TesseractError) as exc:
            raise OcrEngineError(type(exc).__name__) from exc

        words = []
        for text, conf, left, top, width, height in zip(
            data["text"], data["conf"], data["left"], data["top"], data["width"], data["height"]
        ):
            conf = float(conf)
            if conf < 0 or not text.strip():
                continue
            words.append(Word(text=text.strip(), bbox=(left, top, left + width, top + height), conf=conf / 100))
        return words
