"""Konfigurasi pipeline dari file YAML. Kredensial tidak pernah dibaca dari sini."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OcrEngine(str, Enum):
    TESSERACT = "tesseract"
    PADDLE = "paddle"


class ExtractorProvider(str, Enum):
    CLAUDE = "claude"
    GEMINI_VERTEX = "gemini-vertex"


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


class InputSettings(_Strict):
    max_file_mb: int = Field(50, ge=1, le=500)
    max_pages: int = Field(200, ge=1, le=2000)


class PageSelectionSettings(_Strict):
    # Read only the first 3 and last 5 pages; middle pages are never opened.
    economy_mode: bool = False


class OcrSettings(_Strict):
    primary: OcrEngine = OcrEngine.TESSERACT
    fallback: OcrEngine | None = None  # PaddleOCR is planned but not available yet
    tesseract_cmd: Path | None = None  # falls back to TESSERACT_CMD, then PATH
    lang: str = Field("ind+eng", pattern=r"^[a-z_]+(\+[a-z_]+)*$")
    scoring_dpi: int = Field(150, ge=72, le=300)
    read_dpi: int = Field(300, ge=150, le=600)
    timeout_s: int = Field(60, ge=5, le=600)
    min_mean_conf: float = Field(0.70, ge=0.0, le=1.0)
    # Sends the raw P_id image before masking; off unless the client approved it in writing.
    cloud_fallback: bool = False


class ExtractorSettings(_Strict):
    # Sends masked P_id text to a cloud LLM; off until the client's cloud policy approves it.
    enabled: bool = False
    provider: ExtractorProvider = ExtractorProvider.CLAUDE
    model: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._\-]{0,99}$")  # null: provider default
    max_chars_per_page: int = Field(4000, ge=500, le=20000)
    timeout_s: int = Field(60, ge=5, le=600)
    max_retries: int = Field(2, ge=0, le=10)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"  # claude only; tune on the Fase 0 test set
    refusal_fallback: bool = True  # claude only: a declined request is re-run on a fallback Claude model
    vertex_project: str | None = Field(None, pattern=r"^[a-z][a-z0-9\-]{4,61}[a-z0-9]$")  # else GOOGLE_CLOUD_PROJECT
    vertex_location: str | None = Field(None, pattern=r"^[a-z0-9\-]{2,40}$")  # else GOOGLE_CLOUD_LOCATION


class DecisionSettings(_Strict):
    # Auto-pass stays off until the thresholds are calibrated on the Fase 0 test set; until then an
    # all-green file is downgraded to review and flagged `auto_disabled`.
    allow_auto: bool = False


class OutputSettings(_Strict):
    dir: Path = Path("output")
    formats: list[OutputFormat] = Field(default_factory=lambda: list(OutputFormat))


class Settings(_Strict):
    input: InputSettings = Field(default_factory=InputSettings)
    page_selection: PageSelectionSettings = Field(default_factory=PageSelectionSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    extractor: ExtractorSettings = Field(default_factory=ExtractorSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings.model_validate(raw)
