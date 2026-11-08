"""Struktur data bersama antar tahap pipeline dan skema keluaran per file (Bagian 12 PRD)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Literal

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
    BLANK = "blank"


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


class NikStatus(str, Enum):
    FOUND = "ditemukan"
    CONFLICT = "bertentangan"  # two different valid NIKs; neither is reported
    INVALID = "tidak_valid"  # a number after an NIK label that is not a valid NIK
    MISSING = "tidak_ditemukan"


class NikCheck(str, Enum):
    """Whether the birth date or gender printed in the identity block agrees with the one coded in the NIK."""

    MATCH = "cocok"
    MISMATCH = "tidak_cocok"
    ABSENT = "tidak_ada"  # nothing printed or nothing readable to compare with


class NikLog(BaseModel):
    """Hasil pembacaan NIK lokal untuk audit: status dan pemeriksaan saja, tidak pernah nilai NIK-nya."""

    status: NikStatus = NikStatus.MISSING
    tanggal_lahir: NikCheck = NikCheck.ABSENT
    jenis_kelamin: NikCheck = NikCheck.ABSENT
    # The digits after the label ran past 16 (an extra digit read by OCR) and one 16-digit window was picked. The
    # birth date and gender checks cover digits 7 to 12 only, so a wrong pick can still pass both.
    deret_panjang: bool = False
    # What the extractor read from a crop of the NIK line, when one was sent: the same NIK as the local reading
    # ("sama"), used in its place ("dipakai"), a different one that was not used ("beda"), or none ("tidak_terbaca").
    llm: Literal["sama", "dipakai", "beda", "tidak_terbaca"] | None = None


class PositionStatus(str, Enum):
    FOUND = "ditemukan"
    CONFLICT = "bertentangan"  # two different positions; neither is reported
    MISSING = "tidak_ditemukan"


class PositionSource(str, Enum):
    LABEL = "label"  # a `Jabatan :` row in the employee's identity block
    SENTENCE = "kalimat"  # `mempekerjakan PIHAK KEDUA sebagai ...`
    LLM = "llm"  # read by the extractor and verified in the source; the local reading found none


class PositionLog(BaseModel):
    """Hasil pembacaan jabatan lokal untuk audit: status dan sumbernya saja, tanpa nilainya."""

    status: PositionStatus = PositionStatus.MISSING
    sumber: PositionSource | None = None
    # With the extractor: whether its verified title is the one the local reading found; null when either is missing.
    cocok_llm: bool | None = None


class Reader(str, Enum):
    """Who read the name and the period: the extractor (LLM), or the local filename-and-regex check."""

    LLM = "llm"
    PATTERN = "pola"


class LlmPeriod(BaseModel):
    """The period the extractor read, after local verification; only what the document writes (no counted end)."""

    tanggal_mulai: date | None = None
    tanggal_selesai: date | None = None
    durasi_bulan: int | None = None


class SignatureLog(BaseModel):
    """Hasil Tahap 4 untuk audit: dari mana keputusan datang, tanpa citra atau teks halaman."""

    sumber: str | None = None  # digital | annotation | image | vector | pixels
    jangkar: str | None = None  # nama | sebutan; null = jangkar tidak ditemukan
    roi: tuple[float, float, float, float] | None = None  # page fractions x0, top, x1, bottom
    rasio_tinta: float | None = None
    komponen_tertinggi: float | None = None  # in printed-word heights
    anotasi_di_luar_roi: int = 0
    kandidat_roi: int = 0
    meterai: int = 0  # meterai di dalam ROI; warnanya dibuang sebelum tinta diukur
    # What the extractor saw in a crop around the ROI, when one was sent (signed | unsigned | unclear), and how sure.
    llm: Literal["signed", "unsigned", "unclear"] | None = None
    llm_conf: float | None = None


class TokenUsage(BaseModel):
    """The tokens one extractor call used, as its provider reports them. Counts only, never text."""

    masuk: int = 0  # input tokens read fresh
    cache_tulis: int = 0  # input tokens written to the prompt cache
    cache_baca: int = 0  # input tokens read from the prompt cache
    keluar: int = 0  # output tokens, thinking included

    def plus(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            masuk=self.masuk + other.masuk,
            cache_tulis=self.cache_tulis + other.cache_tulis,
            cache_baca=self.cache_baca + other.cache_baca,
            keluar=self.keluar + other.keluar,
        )


class FileLog(BaseModel):
    """Bukti data minimization per file. Tidak boleh memuat isi teks atau PII."""

    n_halaman: int | None = None
    jenis_halaman: dict[int, PageKind] = Field(default_factory=dict)
    format_halaman: dict[int, str] = Field(default_factory=dict)  # page -> format issue code; standard pages absent
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
    putaran_halaman: dict[int, int] = Field(default_factory=dict)  # page -> clockwise turn applied to a turned photo
    periode_regex: RegexPeriod | None = None
    pembaca: Reader | None = None
    # Pattern mode asked the extractor because the patterns could not confirm the name or the period.
    llm_cadangan: bool = False
    periode_llm: LlmPeriod | None = None
    nik: NikLog | None = None
    posisi: PositionLog | None = None
    validasi_nama_file: dict[str, str] = Field(default_factory=dict)  # "nama"/"periode" -> cocok|mirip|tidak_cocok
    validasi_vendor: str | None = None  # cocok | tidak_cocok; null when no vendor folder was given or no page was read
    # What decided it: the folder's name looked up in the page text ("teks"), or the employer the extractor read ("llm").
    vendor_dari: Literal["teks", "llm"] | None = None
    tanda_tangan: SignatureLog | None = None
    extractor: str | None = None
    # Every extractor call for this file, in order: "teks" (masked P_id text), "nik" or "ttd" (an image crop).
    panggilan_llm: list[str] = Field(default_factory=list)
    token_panggilan: list[TokenUsage | None] = Field(default_factory=list)  # each call's, in that order; null: none reported
    token_llm: TokenUsage | None = None  # all those calls together; null when nothing was sent or none was reported
    # Counted only when an extractor actually sends text; preparing the masked payload alone sends nothing.
    teks_keluar_karakter: int = 0
    pola_dimask: int = 0
    alasan: list[str] = Field(default_factory=list)


class FileResult(BaseModel):
    file: str
    nama_karyawan: str | None = None
    # Read locally from the raw P_id text and never part of the extractor payload. Exports mask it unless
    # `output.nik: full`; the website shows it masked by default.
    nik: str | None = None
    posisi: str | None = None  # the employee's job title, read locally from the P_id text
    entitas: str | None = None  # from the filename `Nama - Masa Kontrak - ENTITAS`, as written; not in the document
    vendor: str | None = None  # the vendor folder (`root/<vendor>/...`), as given; looked up in the document text
    vendor_dokumen: str | None = None  # with the extractor: the employer company it read, after verification
    tanggal_mulai: date | None = None
    tanggal_selesai: date | None = None
    durasi_bulan: int | None = None
    status_ttd: SignatureStatus | None = None
    keputusan: Decision = Decision.REVIEW_PRIORITY
    flags: list[str] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    # Plain-language note HR can forward to the vendor; page numbers and format issues only, never content.
    catatan_vendor: str | None = None
    log: FileLog = Field(default_factory=FileLog)
