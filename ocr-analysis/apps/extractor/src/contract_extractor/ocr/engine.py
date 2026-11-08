"""Tangga OCR lokal: skoring kasar untuk page selection dan baca penuh untuk halaman terpilih (Bagian 8–9 PRD)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from contract_extractor.config import OcrEngine, Settings
from contract_extractor.layout import PageText
from contract_extractor.models import Word
from contract_extractor.ocr.base import OcrEngineError, OCRProvider
from contract_extractor.ocr.preprocess import preprocess
from contract_extractor.ocr.tesseract import TesseractOCR, TesseractUnavailable
from contract_extractor.pdf.render import PageRenderer
from contract_extractor.stages.filename import name_in_text


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


@dataclass
class OcrRunner:
    providers: list[OCRProvider]  # ladder order; the first one also does the rough scoring pass
    scoring_dpi: int = 150
    read_dpi: int = 300
    min_mean_conf: float = 0.70

    @property
    def primary(self) -> OCRProvider:
        return self.providers[0]

    def rough_text(self, renderer: PageRenderer, number: int) -> PageText | None:
        """Low-resolution text only for page selection; no preprocessing, primary engine only."""
        image = renderer.render_gray(number, self.scoring_dpi)
        try:
            words = self.primary.read(image)
        except OcrEngineError:
            return None
        h, w = image.shape
        return PageText(number=number, width=w, height=h, words=tuple(words), source=f"ocr:{self.primary.name}")

    def read(self, renderer: PageRenderer, number: int, quality_name: str = "") -> OcrOutcome:
        """Climb the ladder until a provider passes the quality check; otherwise return the best attempt."""
        image, _angle = preprocess(renderer.render_gray(number, self.read_dpi))
        h, w = image.shape
        attempts: list[str] = []
        best: OcrOutcome | None = None
        for provider in self.providers:
            try:
                words = provider.read(image)
            except OcrEngineError:
                attempts.append(f"{provider.name}:error")
                continue
            attempts.append(provider.name)
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
