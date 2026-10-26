"""Tahap 3a: masking teks P_id sebelum keluar dari mesin lokal, dengan pemeriksaan ulang fail closed."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_SENSITIVE_LINE = re.compile(r"\b(?:gaji|upah|tunjangan|thr|bonus)\b", re.IGNORECASE)
_UNNEEDED_IDENTITY_LINE = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(?:"
    r"alamat|telepon|telp\.?|no\.?\s*(?:hp|telp|telepon)|ttl"
    r"|tempat\s*(?:/|,|dan)\s*(?:tgl\.?|tanggal)\s*lahir"
    r"|(?:tgl\.?|tanggal)\s*lahir"
    r")\b",
    re.IGNORECASE,
)
_RUPIAH = re.compile(r"\b(?:rp|idr)\.?\s*\d[\d.,]*(?:,-)?", re.IGNORECASE)
_MONEY_WORDS = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:juta|ribu|miliar|milyar)\b"
    r"|\b(?:(?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|sebelas"
    r"|belas|puluh|ratus|ribu|juta|miliar|milyar|seratus|seribu|sejuta)\s+)+rupiah\b",
    re.IGNORECASE,
)
# >= 10 digits with at most one separator between digits (NIK, rekening, HP).
_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d[\s.\-]?){9,}\d(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})([/.\-])(\d{1,2})\2(\d{4})(?!\d)")
_PLACEHOLDER = "\x00"

REDACTED_LINE = "[REDACTED-LINE]"


class MaskLeakError(Exception):
    """Raised when masked text still contains a sensitive pattern; the text must not be sent."""


@dataclass
class MaskResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _protect_dates(text: str) -> tuple[str, list[str]]:
    """Swap valid calendar dates for a digit-free placeholder so adjacent dates are not one digit run."""
    kept: list[str] = []

    def swap(m: re.Match[str]) -> str:
        day, month = int(m.group(1)), int(m.group(3))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        kept.append(m.group(0))
        return _PLACEHOLDER

    return _NUMERIC_DATE.sub(swap, text), kept


def _restore_dates(text: str, kept: list[str]) -> str:
    restored = iter(kept)
    return "".join(next(restored) if ch == _PLACEHOLDER else ch for ch in text)


def mask_text(text: str) -> MaskResult:
    """Mask money, long digit runs, and unneeded identity lines; raise MaskLeakError if anything survives."""
    result = MaskResult(text="")
    text = text.replace(_PLACEHOLDER, "")

    lines = []
    for line in text.splitlines():
        if _SENSITIVE_LINE.search(line):
            result.counts["baris_sensitif"] += 1
            lines.append(REDACTED_LINE)
        elif _UNNEEDED_IDENTITY_LINE.search(line):
            result.counts["baris_identitas"] += 1
            lines.append(REDACTED_LINE)
        else:
            lines.append(line)
    masked = "\n".join(lines)

    masked, n = _RUPIAH.subn("Rp [REDACTED]", masked)
    result.counts["rupiah"] += n
    masked, n = _MONEY_WORDS.subn("[REDACTED]", masked)
    result.counts["nominal"] += n

    protected, kept = _protect_dates(masked)
    protected, n = _DIGIT_RUN.subn("[REDACTED-ID]", protected)
    result.counts["deret_angka"] += n
    result.text = _restore_dates(protected, kept)

    assert_no_leak(result.text)
    return result


def assert_no_leak(text: str) -> None:
    """Re-check with the same rules as masking; separators are not stripped globally."""
    protected, _ = _protect_dates(text.replace(_PLACEHOLDER, ""))
    if _DIGIT_RUN.search(protected) or _RUPIAH.search(text):
        raise MaskLeakError("mask_leak")
