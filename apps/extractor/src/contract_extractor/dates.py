"""Nama bulan Indonesia/Inggris dan pembentukan tanggal, dipakai parser nama file dan regex periode."""

from __future__ import annotations

import re
from datetime import date, timedelta

# Full names and the abbreviations seen in Indonesian contracts and filenames.
_MONTH_WORDS: dict[int, tuple[str, ...]] = {
    1: ("jan", "januari", "january"),
    2: ("feb", "peb", "februari", "pebruari", "february"),
    3: ("mar", "maret", "march"),
    4: ("apr", "april"),
    5: ("mei", "may"),
    6: ("jun", "juni", "june"),
    7: ("jul", "juli", "july"),
    8: ("agu", "agt", "ags", "aug", "agustus", "august"),
    9: ("sep", "sept", "september"),
    10: ("okt", "oct", "oktober", "october"),
    11: ("nov", "nop", "november", "nopember"),
    12: ("des", "dec", "desember", "december"),
}
MONTHS: dict[str, int] = {w: m for m, words in _MONTH_WORDS.items() for w in words}
# Regex alternation of every month word, longest first so `januari` wins over `jan`.
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
# d/m/yyyy with one repeated separator. Four-digit years only: document text never yields a two-digit year.
NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})([/.\-])(\d{1,2})\2(\d{4})(?!\d)")


def month_number(word: str) -> int | None:
    return MONTHS.get(word.lower().rstrip("."))


def expand_year(year: int) -> int:
    """Two-digit years are read as 20yy; only allowed for filenames, never document text."""
    return 2000 + year if year < 100 else year


def safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def months_between(start: date, end: date) -> int:
    """Whole contract months for an inclusive range, e.g. 1 Jan - 31 Mar = 3, 15 Jan - 14 Apr = 3."""
    after_end = end + timedelta(days=1)
    months = (after_end.year - start.year) * 12 + (after_end.month - start.month)
    return months - 1 if after_end.day < start.day else months
