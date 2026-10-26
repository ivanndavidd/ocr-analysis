"""Struktur data bersama antar tahap pipeline dan skema keluaran per file (Bagian 12 PRD)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class Word:
    """Satu kata berkoordinat, dari text layer native maupun OCR."""

    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1; y dari atas
    conf: float  # 0..1; text layer native selalu 1.0


class PageKind(str, Enum):
    NATIVE = "native"
    SCAN = "scan"
    HYBRID = "hybrid"
    UNCLASSIFIED = "unclassified"


class SignatureStatus(str, Enum):
    SIGNED = "signed"
    UNSIGNED = "unsigned"
    UNCERTAIN = "uncertain"


class Decision(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    REVIEW_PRIORITY = "review_priority"


class Level(str, Enum):
    GREEN = "hijau"
    YELLOW = "kuning"
    RED = "merah"


class RegexPeriod(BaseModel):
    """Periode dari jalur regex lokal (Bagian 10 PRD): pembanding hasil extractor, bukan pengganti."""

    tanggal_mulai: date | None = None
    tanggal_selesai: date | None = None
    durasi_bulan: int | None = None
    ambigu: bool = False  # conflicting candidates were found; the conflicting fields stay null


class FileLog(BaseModel):
    """Bukti data minimization per file. Tidak boleh memuat isi teks atau PII."""

    n_halaman: int | None = None
    jenis_halaman: dict[int, PageKind] = Field(default_factory=dict)
    p_id: list[int] = Field(default_factory=list)
    p_ttd: int | None = None
    halaman_sensitif: list[int] = Field(default_factory=list)
    skor_halaman: dict[int, dict[str, float]] = Field(default_factory=dict)
    # Every component that received each page (e.g. "text_layer", "tesseract@150dpi"); the data-minimization proof.
    komponen_halaman: dict[int, list[str]] = Field(default_factory=dict)
    ocr_p_id: str | None = None
    ocr_p_ttd: str | None = None
    ocr_conf: dict[int, float] = Field(default_factory=dict)
    ocr_cloud_dipakai: bool = False
    periode_regex: RegexPeriod | None = None
    extractor: str | None = None
    # Counted only when an extractor actually sends text; preparing the masked payload alone sends nothing.
    teks_keluar_karakter: int = 0
    pola_dimask: int = 0
    alasan: list[str] = Field(default_factory=list)


class FileResult(BaseModel):
    file: str
    nama_karyawan: str | None = None
    tanggal_mulai: date | None = None
    tanggal_selesai: date | None = None
    durasi_bulan: int | None = None
    status_ttd: SignatureStatus | None = None
    keputusan: Decision = Decision.REVIEW_PRIORITY
    flags: list[str] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    log: FileLog = Field(default_factory=FileLog)
