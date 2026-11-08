"""Interface extractor LLM dan skema keluarannya (Bagian 10 PRD)."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from contract_extractor.common.models import TokenUsage

MAX_TEXT = 200  # longer than any name or job title; a longer value is not one
MAX_QUOTE = 400


class _Answer(BaseModel):
    """One field of the answer: a quote copied from the source text, the value read from it, and how sure the model
    is. Out-of-range values become null instead of failing the whole answer, so one bad field costs only itself."""

    kutipan: str | None = None
    confidence: float = 0.0

    @field_validator("kutipan", mode="before")
    @classmethod
    def _quote(cls, value: Any) -> Any:
        return value if not isinstance(value, str) or len(value) <= MAX_QUOTE else None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> Any:
        return min(max(value, 0.0), 1.0) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class TextAnswer(_Answer):
    nilai: str | None = None

    @field_validator("nilai", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = " ".join(value.split())
            return value if 0 < len(value) <= MAX_TEXT else None
        return value


class DateAnswer(_Answer):
    nilai: date | None = None


class MonthsAnswer(_Answer):
    nilai: int | None = None

    @field_validator("nilai", mode="before")
    @classmethod
    def _months(cls, value: Any) -> Any:
        return value if not isinstance(value, int) or 1 <= value <= 120 else None


class ExtractionResult(BaseModel):
    nama_karyawan: TextAnswer = Field(default_factory=TextAnswer)
    posisi: TextAnswer = Field(default_factory=TextAnswer)
    tanggal_mulai: DateAnswer = Field(default_factory=DateAnswer)
    tanggal_selesai: DateAnswer = Field(default_factory=DateAnswer)
    durasi_bulan: MonthsAnswer = Field(default_factory=MonthsAnswer)
    vendor: TextAnswer = Field(default_factory=TextAnswer)  # the employer company; checked against the vendor folder
    usage: TokenUsage | None = None  # set by the extractor from its provider's report; not part of the model's answer


class ImageTask(str, Enum):
    """A crop the laptop could not settle: the line holding the NIK, or the signature area above the employee's name."""

    NIK = "nik"
    SIGNATURE = "ttd"


class ImageAnswer(BaseModel):
    """What the extractor read from one crop. `nik` for the NIK line (16 digits, else null), `status` for the
    signature area; whatever the task did not ask for stays null."""

    nik: str | None = None
    status: Literal["signed", "unsigned", "unclear"] | None = None
    confidence: float = 0.0
    usage: TokenUsage | None = None  # set by the extractor from its provider's report

    @field_validator("nik", mode="before")
    @classmethod
    def _digits(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = "".join(value.split())
            return value if len(value) == 16 and value.isdigit() else None
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> Any:
        return min(max(value, 0.0), 1.0) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class NikAnswer(_Answer):
    nilai: str | None = None

    @field_validator("nilai", mode="before")
    @classmethod
    def _digits(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = "".join(value.split())
            return value if len(value) == 16 and value.isdigit() else None
        return value


class GenderAnswer(_Answer):
    nilai: Literal["laki-laki", "perempuan"] | None = None


class SignatureAnswer(BaseModel):
    status: Literal["signed", "unsigned", "unclear"] | None = None
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> Any:
        return min(max(value, 0.0), 1.0) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class FullPageAnswer(BaseModel):
    """What the extractor read from the first and last page sent whole (the no-OCR mode): every field of the row,
    plus the printed birth date and gender, which check its NIK locally. Quotes are not asked for, since there is no
    local text to find them in."""

    nama_karyawan: TextAnswer = Field(default_factory=TextAnswer)
    nik: NikAnswer = Field(default_factory=NikAnswer)
    tanggal_lahir: DateAnswer = Field(default_factory=DateAnswer)
    jenis_kelamin: GenderAnswer = Field(default_factory=GenderAnswer)
    posisi: TextAnswer = Field(default_factory=TextAnswer)
    tanggal_mulai: DateAnswer = Field(default_factory=DateAnswer)
    tanggal_selesai: DateAnswer = Field(default_factory=DateAnswer)
    durasi_bulan: MonthsAnswer = Field(default_factory=MonthsAnswer)
    vendor: TextAnswer = Field(default_factory=TextAnswer)
    tanda_tangan: SignatureAnswer = Field(default_factory=SignatureAnswer)
    usage: TokenUsage | None = None  # set by the extractor from its provider's report


class ExtractorError(Exception):
    """A failed extraction call. `kind` is safe to log; it never holds document text or API messages. `usage` is what
    the call still used when the provider answered, e.g. with a truncated or invalid reply."""

    def __init__(self, kind: str, usage: TokenUsage | None = None) -> None:
        super().__init__(kind)
        self.kind, self.usage = kind, usage


class ExtractorUnavailable(Exception):
    """The configured extractor cannot run (credentials, model, or settings); raised before any file is read."""


class Extractor(Protocol):
    name: str

    def extract(self, masked_text: str) -> ExtractionResult:
        """Receives only masked P_id text that already passed assert_no_leak."""
        ...

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        """Receives one small crop: the NIK line, or the signature area. Never a whole page."""
        ...

    def read_pages(self, first: bytes, last: bytes | None) -> FullPageAnswer:
        """The no-OCR mode only: the first page (identity) and the last page (signature) as JPEG images, whole and
        unmasked; `last` is None for a one-page contract. Never called in the OCR mode."""
        ...
