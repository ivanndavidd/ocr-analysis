"""Tahap 3a: masking teks P_id sebelum keluar dari mesin lokal, dengan pemeriksaan ulang fail closed."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from contract_extractor.common.dates import NUMERIC_DATE

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
# ID lines keep their label, so the extractor can tell the employee block apart, but lose the whole value,
# even when OCR broke the digits into short runs (`3171O1234567OOO1`) that the digit-run rule misses.
_ID_LABEL = r"nik|no\.?\s*ktp|nomor\s*ktp|(?:no\.?|nomor)\s*induk\s*kependudukan"
_ID_VALUE = re.compile(
    rf"^(\s*(?:\d+[.)]\s*)?(?:{_ID_LABEL}|npwp|no\.?\s*rek(?:ening)?|nomor\s*rekening)\b\s*[:.]?\s*)(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
# A NIK inside a sentence (`pemegang NIK 3171O1234567OOO1, bertempat ...`): the value up to the next comma goes,
# for the same reason as above. Only values holding digits count, so `NIK dan alamat` is left alone.
_ID_IN_SENTENCE = re.compile(rf"(?<![a-z])((?:{_ID_LABEL})(?![a-z])\s*[:.]?\s*)([^,;\n]{{1,30}})", re.IGNORECASE)
_MIN_ID_DIGITS = 4
_PLACEHOLDER = "\x00"

REDACTED_LINE = "[REDACTED-LINE]"
REDACTED_ID = "[REDACTED-ID]"
PAGE_SEPARATOR = "\n\n"  # two characters, so digits on either side can never join into one run


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

    return NUMERIC_DATE.sub(swap, text), kept


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
    protected, n = _DIGIT_RUN.subn(REDACTED_ID, protected)
    result.counts["deret_angka"] += n
    masked = _restore_dates(protected, kept)

    def redact_id_value(m: re.Match[str]) -> str:
        if m.group(2).strip() == REDACTED_ID:
            return m.group(0)
        result.counts["nilai_identitas"] += 1
        return m.group(1) + REDACTED_ID

    def redact_id_in_sentence(m: re.Match[str]) -> str:
        value = m.group(2)
        if REDACTED_ID in value or sum(ch.isdigit() for ch in value) < _MIN_ID_DIGITS:
            return m.group(0)
        result.counts["nilai_identitas"] += 1
        return m.group(1) + REDACTED_ID

    masked = _ID_VALUE.sub(redact_id_value, masked)
    result.text = _ID_IN_SENTENCE.sub(redact_id_in_sentence, masked)
    assert_no_leak(result.text)
    return result


def mask_pages(pages: list[str], max_chars_per_page: int) -> MaskResult:
    """Mask each page, then cut it to the limit. Masking first means a cut can never leave a partial NIK."""
    combined = MaskResult(text="")
    parts = []
    for page in pages:
        masked = mask_text(page)
        combined.counts.update(masked.counts)
        parts.append(masked.text[:max_chars_per_page])
    combined.text = PAGE_SEPARATOR.join(parts)
    assert_no_leak(combined.text)
    return combined


def has_long_digit_run(text: str) -> bool:
    """True when text holds a >= 10-digit run (NIK, rekening, HP), ignoring adjacent valid dates."""
    protected, _ = _protect_dates(text.replace(_PLACEHOLDER, ""))
    return _DIGIT_RUN.search(protected) is not None


def assert_no_leak(text: str) -> None:
    """Re-check with the same rules as masking; separators are not stripped globally."""
    if has_long_digit_run(text) or _RUPIAH.search(text):
        raise MaskLeakError("mask_leak")
