"""Tahap 5a: parsing nama file `Nama Karyawan - Masa Kontrak` dan pembandingan dengan hasil ekstraksi."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import PurePath

from rapidfuzz import fuzz

from contract_extractor.dates import expand_year, month_number, months_between, safe_date

FLAG_NONSTANDARD = "filename_nonstandard"
FLAG_UNPARSED = "filename_unparsed"

_SEPARATOR = re.compile(r"\s+[-–]\s+")
_RANGE_SEP = r"\s*(?:-|–|s/?d|sampai\s+dengan)\s*"
_TEXT_DATE = r"(\d{1,2})\s*([a-z]{3,9})\.?\s*"
_TEXT_RANGE = re.compile(
    rf"^{_TEXT_DATE}(\d{{4}}|\d{{2}})?{_RANGE_SEP}{_TEXT_DATE}(\d{{4}}|\d{{2}})$", re.IGNORECASE
)
_NUM_DATE = r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})"
_NUM_RANGE = re.compile(rf"^{_NUM_DATE}{_RANGE_SEP}{_NUM_DATE}$", re.IGNORECASE)
_DURATION = re.compile(r"^(\d{1,3})\s*(bulan|bln|months?|tahun|thn|years?)$", re.IGNORECASE)

_TITLES = re.compile(
    r"(?<![a-z])(?:sdri?|bpk|bapak|ibu|drs?|dra|ir|hj|"
    r"s\.\s?(?:kom|e|t|h|pd|ak|sos|si|ip|psi|ked|farm|ds)|a\.\s?md|m\.\s?(?:m|kom|si|t|h|ak|ba))\.?(?![a-z])",
    re.IGNORECASE,
)
_NON_LETTER = re.compile(r"[^a-z]+")


class Match(str, Enum):
    COCOK = "cocok"
    MIRIP = "mirip"
    TIDAK_COCOK = "tidak_cocok"


@dataclass(frozen=True)
class ParsedFilename:
    name: str
    start: date | None = None
    end: date | None = None
    duration_months: int | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)


def normalize_name(name: str) -> str:
    """Lowercase, drop titles and salutations, punctuation to spaces."""
    without_titles = _TITLES.sub(" ", name.lower())
    return " ".join(_NON_LETTER.sub(" ", without_titles).split())


def _parse_text_range(text: str) -> tuple[date, date] | None:
    m = _TEXT_RANGE.match(text)
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = m.groups()
    month1, month2 = month_number(m1), month_number(m2)
    if month1 is None or month2 is None:
        return None
    end_year = expand_year(int(y2))
    start_year = expand_year(int(y1)) if y1 else (end_year - 1 if month1 > month2 else end_year)
    start, end = safe_date(start_year, month1, int(d1)), safe_date(end_year, month2, int(d2))
    return (start, end) if start and end and start <= end else None


def _parse_numeric_range(text: str) -> tuple[date, date] | None:
    m = _NUM_RANGE.match(text)
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
    start = safe_date(expand_year(y1), m1, d1)
    end = safe_date(expand_year(y2), m2, d2)
    return (start, end) if start and end and start <= end else None


def parse_filename(filename: str) -> ParsedFilename:
    stem = PurePath(filename).stem.strip()
    parts = _SEPARATOR.split(stem, maxsplit=1)
    if len(parts) < 2 or not parts[0].strip():
        return ParsedFilename(name=stem, flags=(FLAG_NONSTANDARD,))

    name, period = parts[0].strip(), " ".join(parts[1].split())
    for parse in (_parse_text_range, _parse_numeric_range):
        parsed = parse(period)
        if parsed:
            return ParsedFilename(name=name, start=parsed[0], end=parsed[1])

    duration = _DURATION.match(period)
    if duration:
        n = int(duration.group(1))
        unit = duration.group(2).lower()
        months = n * 12 if unit.startswith(("tahun", "thn", "year")) else n
        return ParsedFilename(name=name, duration_months=months)

    return ParsedFilename(name=name, flags=(FLAG_UNPARSED,))


def name_score(a: str, b: str) -> float:
    """Best token_set_ratio with and without spaces, so `Abd.karim` matches `ABD KARIM` and `Abdkarim`."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.token_set_ratio(na, nb), fuzz.ratio(na.replace(" ", ""), nb.replace(" ", "")))


NAME_IN_TEXT_MIN = 85


def name_in_text(name_norm: str, text: str) -> bool:
    """Fuzzy search for a normalized name in a line; a line shorter than the name must match whole,
    so a bare first name (`Budi`) never counts as `Budi Santoso`."""
    if not name_norm:
        return False
    norm = normalize_name(text)
    if not norm:
        return False
    if len(norm) < len(name_norm):
        return fuzz.ratio(name_norm, norm) >= NAME_IN_TEXT_MIN
    return fuzz.partial_ratio(name_norm, norm) >= NAME_IN_TEXT_MIN


def compare_name(from_file: str, extracted: str) -> Match:
    score = name_score(from_file, extracted)
    if score >= 90:
        return Match.COCOK
    if score >= 75:
        return Match.MIRIP
    return Match.TIDAK_COCOK


def compare_period(
    parsed: ParsedFilename,
    start: date | None,
    end: date | None,
    duration_months: int | None = None,
) -> Match | None:
    """None when the filename carries no comparable period."""
    if parsed.start and parsed.end:
        if start is None or end is None:
            return Match.TIDAK_COCOK
        if (start, end) == (parsed.start, parsed.end):
            return Match.COCOK
        if abs((start - parsed.start).days) <= 3 and abs((end - parsed.end).days) <= 3:
            return Match.MIRIP
        return Match.TIDAK_COCOK

    if parsed.duration_months is not None:
        actual = duration_months
        if actual is None and start and end:
            actual = months_between(start, end)
        if actual is None:
            return Match.TIDAK_COCOK
        diff = abs(actual - parsed.duration_months)
        return Match.COCOK if diff == 0 else Match.MIRIP if diff == 1 else Match.TIDAK_COCOK

    return None
