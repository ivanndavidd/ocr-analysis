"""Tangga OCR lokal: skoring kasar untuk page selection dan baca penuh untuk halaman terpilih (Bagian 8–9 PRD)."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from contract_extractor.common.config import OcrEngine, Settings
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import Word
from contract_extractor.document.ocr.base import OcrEngineError, OCRProvider
from contract_extractor.document.ocr.preprocess import preprocess, straighten
from contract_extractor.document.ocr.tesseract import TesseractOCR, TesseractUnavailable
from contract_extractor.document.pdf.render import PageRenderer
from contract_extractor.extractor_method.pattern.filename import name_in_text


# Degrees. On 200 synthetic contracts, straightening every page found a missed page in 10 contracts and lost one in 3
# level ones; straightening only pages tilted at least this much found 9 and lost none.
ROUGH_TILT_DEG = 0.5

# Page turn: below this many confident words upright, the other three turns are read; one wins with at least half
# this many and TURN_MARGIN times the upright count. On 300 synthetic files this turned all 7 turned photos the right
# way and no upright page (the best wrong turn reached 3 words).
TURN_MIN_WORDS = 20
TURN_MARGIN = 3
_WORD = re.compile(r"[A-Za-z]{3,}")


class OcrUnavailable(Exception):
    """Local OCR cannot run with this configuration; the message never contains document content."""


@dataclass(frozen=True)
class OcrOutcome:
    page: PageText | None
    engine: str | None
    mean_conf: float
    passed: bool
    attempts: list[str] = field(default_factory=list)


def mean_confidence(words: list[Word] | tuple[Word, ...]) -> float:
    return float(np.mean([w.conf for w in words])) if words else 0.0


def confident_words(words: list[Word] | tuple[Word, ...]) -> int:
    """Words read with confidence that hold three letters in a row: noise from a turned page rarely does."""
    return sum(1 for w in words if w.conf >= 0.6 and _WORD.search(w.text))


@dataclass
class OcrRunner:
    providers: list[OCRProvider]  # ladder order; the first one also does the rough scoring pass
    scoring_dpi: int = 150
    read_dpi: int = 300
    min_mean_conf: float = 0.70

    @property
    def primary(self) -> OCRProvider:
        return self.providers[0]

    def rough_text(self, renderer: PageRenderer, number: int, dpi: int | None = None) -> PageText | None:
        """Text only for page selection, at `scoring_dpi` unless `dpi` is given; primary engine only. A tilted page
        is straightened like the full read (not thresholded): a phone photo a few degrees off read as noise, so its
        identity page went unseen. A level page is read as rendered, since shadow removal alone cost as many pages
        as it found."""
        rendered = renderer.render_gray(number, dpi or self.scoring_dpi)
        straight, angle = straighten(rendered)
        image = straight if abs(angle) >= ROUGH_TILT_DEG else rendered
        try:
            words = self.primary.read(image)
        except OcrEngineError:
            return None
        h, w = image.shape
        return PageText(number=number, width=w, height=h, words=tuple(words), source=f"ocr:{self.primary.name}")

    def best_turn(self, renderer: PageRenderer, number: int, upright: PageText | None) -> int:
        """The clockwise turn (0, 90, 180, 270) that reads best, for a photo taken sideways or upside down. Tesseract's
        own orientation check gave up on every synthetic phone photo ("too few characters"), so each turn is read and
        the confident words counted. A page that already reads upright is never turned."""
        base = confident_words(upright.words) if upright else 0
        if base >= TURN_MIN_WORDS:
            return 0
        counts: dict[int, int] = {}
        for turn in (90, 180, 270):
            image, _angle = straighten(renderer.render_gray(number, self.scoring_dpi, turn))
            try:
                counts[turn] = confident_words(self.primary.read(image))
            except OcrEngineError:
                counts[turn] = 0
        best = max(counts, key=lambda turn: counts[turn])
        return best if counts[best] >= max(TURN_MIN_WORDS // 2, TURN_MARGIN * base) else 0

    def read(self, renderer: PageRenderer, number: int, quality_name: str = "", erase_rules: bool = False) -> OcrOutcome:
        """Climb the ladder until a provider passes the quality check; otherwise return the best attempt.
        With `erase_rules`, a page no provider passes is read once more with table borders whited out (see
        `erase_rules` in preprocess), for an identity page. Only as a fallback: erasing every identity page fixed 8 of
        184 synthetic scans and photos but broke 4 photos that passed, whose page edges and slanted borders were left
        in pieces that OCR read as letters."""
        return self._climb(renderer, number, quality_name, (False, True) if erase_rules else (False,))

    def read_erased(self, renderer: PageRenderer, number: int, quality_name: str = "") -> OcrOutcome:
        """The ladder on the page with table borders whited out only: for an identity page that passed but lacks a
        value a ruled table can hide (see `_reveal_table_rows` in the pipeline)."""
        return self._climb(renderer, number, quality_name, (True,))

    def _climb(self, renderer: PageRenderer, number: int, quality_name: str, erases: tuple[bool, ...]) -> OcrOutcome:
        gray = renderer.render_gray(number, self.read_dpi)
        attempts: list[str] = []
        best: OcrOutcome | None = None
        for erase in erases:
            image, _angle = preprocess(gray, erase)
            h, w = image.shape
            for provider in self.providers:
                attempt = f"{provider.name}+rules_erased" if erase else provider.name
                try:
                    words = provider.read(image)
                except OcrEngineError:
                    attempts.append(f"{attempt}:error")
                    continue
                attempts.append(attempt)
                page = PageText(number=number, width=w, height=h, words=tuple(words), source=f"ocr:{provider.name}")
                conf = mean_confidence(words)
                name_ok = not quality_name or any(name_in_text(quality_name, line.text) for line in page.lines)
                outcome = OcrOutcome(page, provider.name, conf, conf >= self.min_mean_conf and name_ok, list(attempts))
                if outcome.passed:
                    return outcome
                if best is None or conf > best.mean_conf:
                    best = outcome
        if best is None:
            return OcrOutcome(None, None, 0.0, False, attempts)
        return OcrOutcome(best.page, best.engine, best.mean_conf, False, attempts)


def _resolve_tesseract(configured: Path | None) -> Path:
    candidates = [configured, os.environ.get("TESSERACT_CMD"), shutil.which("tesseract")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise OcrUnavailable("tesseract not found: set ocr.tesseract_cmd, TESSERACT_CMD, or add it to PATH")


def build_runner(settings: Settings) -> OcrRunner:
    ocr = settings.ocr
    if ocr.cloud_fallback:
        raise OcrUnavailable("ocr.cloud_fallback is not implemented; keep it false")
    if OcrEngine.PADDLE in (ocr.primary, ocr.fallback):
        raise OcrUnavailable("PaddleOCR is not available yet; use tesseract with no fallback")

    tesseract = TesseractOCR(_resolve_tesseract(ocr.tesseract_cmd), lang=ocr.lang, timeout_s=ocr.timeout_s)
    try:
        tesseract.check()
    except TesseractUnavailable as exc:
        raise OcrUnavailable(str(exc)) from exc
    return OcrRunner(
        providers=[tesseract],
        scoring_dpi=ocr.scoring_dpi,
        read_dpi=ocr.read_dpi,
        min_mean_conf=ocr.min_mean_conf,
    )
