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
    RAPIDOCR = "rapidocr"  # Baidu PP-OCRv6 small through RapidOCR (CPU); its model files come from `second_reader`
    PADDLE = "paddle"


class ExtractorProvider(str, Enum):
    # Claude API when ANTHROPIC_API_KEY is set (production); otherwise the user's own Claude Code login (testing).
    CLAUDE = "claude"
    CLAUDE_CODE = "claude-code"  # always the user's own `claude auth login`: local testing only
    GEMINI_VERTEX = "gemini-vertex"


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


class InputSettings(_Strict):
    max_file_mb: int = Field(50, ge=1, le=500)
    max_pages: int = Field(200, ge=1, le=2000)


class PageSelectionSettings(_Strict):
    # Only the first and last pages are ever read; economy mode also skips classifying the middle pages.
    economy_mode: bool = False


class SecondReaderSettings(_Strict):
    # The other local engine reads a scanned identity page again when the NIK or position the first one (`primary`)
    # read is unsure; the same value from both makes it green, anything else stays as it was. Local only. RapidOCR's
    # model folder and threads are set here whichever role it has.
    enabled: bool = False
    model_dir: Path | None = None  # falls back to RAPIDOCR_MODEL_DIR, then apps/extractor/models/rapidocr
    threads: int = Field(2, ge=1, le=16)


class OcrSettings(_Strict):
    # The engine that reads every page (owner 2026-11-16: RapidOCR first on the website, `--primary`); the other one
    # is the second reader.
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
    second_reader: SecondReaderSettings = Field(default_factory=SecondReaderSettings)


class ExtractorSettings(_Strict):
    # Sends masked P_id text to a cloud LLM; off until the client's cloud policy approves it.
    enabled: bool = False
    # The website's flow: the patterns read the name, period and vendor, and the extractor's reading only fills what
    # they could not confirm; the extractor reads the position, and a crop of the NIK line or the signature area when
    # the local reading is unsure. False is the CLI's extractor mode, where the extractor reads every field.
    assist: bool = False
    provider: ExtractorProvider = ExtractorProvider.CLAUDE
    model: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._\-]{0,99}$")  # null: provider default
    max_chars_per_page: int = Field(4000, ge=500, le=20000)
    timeout_s: int = Field(120, ge=5, le=600)  # one call; a long think at max effort takes a while
    max_retries: int = Field(2, ge=0, le=10)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "max"  # claude and claude-code
    refusal_fallback: bool = True  # claude only: a declined request is re-run on a fallback Claude model
    vertex_project: str | None = Field(None, pattern=r"^[a-z][a-z0-9\-]{4,61}[a-z0-9]$")  # else GOOGLE_CLOUD_PROJECT
    vertex_location: str | None = Field(None, pattern=r"^[a-z0-9\-]{2,40}$")  # else GOOGLE_CLOUD_LOCATION
    claude_code_cmd: Path | None = None  # claude-code only; falls back to CLAUDE_CODE_CMD, then PATH


class DecisionSettings(_Strict):
    # Auto-pass stays off until the thresholds are calibrated on the Fase 0 test set; until then an
    # all-green file is downgraded to review and flagged `auto_disabled`.
    allow_auto: bool = False


class OutputSettings(_Strict):
    dir: Path = Path("output")
    formats: list[OutputFormat] = Field(default_factory=lambda: list(OutputFormat))
    # NIK in results.json/csv/xlsx: the last four digits only, unless `full` (needed by `evaluate` on labelled data).
    nik: Literal["masked", "full"] = "masked"


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
