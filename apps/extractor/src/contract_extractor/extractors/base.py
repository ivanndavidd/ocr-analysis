"""Interface extractor LLM dan skema keluarannya (Bagian 10 PRD)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    nama: str | None = Field(None, max_length=200)
    periode: str | None = Field(None, max_length=200)


class ExtractionResult(BaseModel):
    nama_karyawan: str | None = Field(None, max_length=200)
    tanggal_mulai: date | None = None
    tanggal_selesai: date | None = None
    durasi_bulan: int | None = Field(None, ge=1, le=120)
    bukti: Evidence = Field(default_factory=Evidence)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class Extractor(Protocol):
    name: str

    def extract(self, masked_text: str) -> ExtractionResult:
        """Receives only masked P_id text that already passed assert_no_leak."""
        ...
