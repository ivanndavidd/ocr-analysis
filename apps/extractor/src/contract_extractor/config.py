"""Konfigurasi pipeline dari file YAML. Kredensial tidak pernah dibaca dari sini."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

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
    fallback: OcrEngine | None = OcrEngine.PADDLE
    # Sends the raw P_id image before masking; off unless the client approved it in writing.
    cloud_fallback: bool = False


class ExtractorSettings(_Strict):
    provider: ExtractorProvider = ExtractorProvider.CLAUDE
    max_chars_per_page: int = Field(4000, ge=500, le=20000)


class OutputSettings(_Strict):
    dir: Path = Path("output")
    formats: list[OutputFormat] = Field(default_factory=lambda: list(OutputFormat))


class Settings(_Strict):
    input: InputSettings = Field(default_factory=InputSettings)
    page_selection: PageSelectionSettings = Field(default_factory=PageSelectionSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    extractor: ExtractorSettings = Field(default_factory=ExtractorSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings.model_validate(raw)
