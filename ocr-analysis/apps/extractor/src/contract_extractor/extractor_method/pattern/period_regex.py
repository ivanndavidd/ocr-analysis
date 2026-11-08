"""Tahap 3, jalur regex lokal: periode kontrak dari teks P_id, pembanding hasil extractor (Bagian 10 PRD).

Hanya tanggal setelah kata jangkar periode yang dihitung. Tanggal lahir dan tanggal di paragraf pembuka
(tanggal penandatanganan) diabaikan, dan tahun dua digit tidak pernah dibaca dari isi dokumen. Kandidat
yang saling bertentangan membuat field-nya null dan `ambigu` true, bukan memilih salah satu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from contract_extractor.common.dates import MONTH_PATTERN, month_number, months_between, safe_date
from contract_extractor.common.models import RegexPeriod

ANCHOR_WINDOW = 120  # a date counts only within this many characters after an anchor
BIRTH_WINDOW = 40  # dates this close after `lahir` are birth dates
BIRTH_DATE_WINDOW = 80  # how far after `lahir` the NIK check looks for the birth date
DURATION_WINDOW = 60
PROBATION_WINDOW = 40
MAX_DURATION_MONTHS = 120

_START, _END = "start", "end"
_T = TypeVar("_T")


# Letters Tesseract confuses on scans (`sampai` → `sampal`, `berlaku` → `bertaku`). Anchor words accept these so one
# misread letter does not drop the anchor; an anchor still only counts within ANCHOR_WINDOW of a date.
_OCR_SLIPS = {"i": "[il1|!]", "l": "[lit1|]", "t": "[tl]", "o": "[o0]"}


def _tolerant(token: str) -> str:
    return "".join(_OCR_SLIPS.get(c, re.escape(c)) for c in token.lower())


# Between the words of an anchor phrase: spaces and line breaks, and the same OCR debris `_GAP` skips in a date. A
# table border can start the next line with `|` (`sampai` / `| dengan`): 2 period conflicts in 184 synthetic scans
# and photos, where the end date then took `mulai` as its anchor.
_PHRASE_GAP = r"[\s\"'|]+"


def _words(*phrases: str) -> str:
    return "|".join(_PHRASE_GAP.join(_tolerant(tok) for tok in p.split()) for p in phrases)


def _kw(*phrases: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z])(?:{_words(*phrases)})(?![a-z])", re.IGNORECASE)


# `1 Januari 2026`, `01 Jan. 2026`, `1 (satu) Januari 2026`, `tanggal 1 bulan Januari tahun 2026`. OCR debris
# (`"`, `'`, `|`) between the parts is skipped: `16 " April 2045` is still 16 April 2045.
_GAP = r"[\s\"'|]"
_TEXT_DATE = re.compile(
    rf"(?<!\d)(\d{{1,2}}){_GAP}*(?:\([a-z\s]+\){_GAP}*)?(?:bulan\s+)?({MONTH_PATTERN})\.?,?{_GAP}+(?:tahun\s+)?(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
# NUMERIC_DATE, plus one OCR space beside a separator (`16-04- 2045`). Kept local: masking also uses NUMERIC_DATE
# to spare dates, and a looser pattern there could spare a split NIK.
_NUMERIC_DATE_OCR = re.compile(r"(?<!\d)(\d{1,2}) ?([/.\-]) ?(\d{1,2}) ?\2 ?(\d{4})(?!\d)")
# `s/d`, `s.d.`, `s.d`; never a bare `sd`, which is also the school level `SD` on identity pages.
_RANGE_WORDS = rf"(?:{_words('sampai dengan', 'hingga')}|s\s*[/.]\s*d\.?)(?![a-z])"
_RANGE = re.compile(rf"(?<![a-z]){_RANGE_WORDS}", re.IGNORECASE)
_RANGE_RIGHT_AFTER = re.compile(rf"[\s,]*{_RANGE_WORDS}", re.IGNORECASE)
_ANCHORS: list[tuple[re.Pattern[str], str]] = [
    (_kw("mulai", "dimulai", "berlaku efektif", "sejak", "terhitung"), _START),
    (_kw("berakhir"), _END),
    (_RANGE, _END),
]
# `periode 7 Februari 2032 (tujuh februari dua ribu tiga puluh dua) - 6 Mei 2032`: two dates joined by a dash, the
# first optionally spelled out in brackets. A dash is everywhere in contracts, so the pair counts only after a
# period word.
_DASH_BETWEEN = re.compile(r"\s*(?:\([^()\d]{0,120}\)\s*)?[-–]\s*")
_DASH_CONTEXT = _kw("periode", "jangka waktu", "berlangsung", "berlaku", "masa kontrak")
_BIRTH = re.compile(r"lahir", re.IGNORECASE)  # also matches `kelahiran`
_NAME_LABEL = re.compile(r"^\s*(?:\d+[.)]\s*)?nama\s*:", re.IGNORECASE | re.MULTILINE)

_DURATION = re.compile(r"(?<!\d)(\d{1,3})\s*(?:\([a-z\s]+\)\s*)?(bulan|tahun)(?![a-z])", re.IGNORECASE)
_DURATION_ANCHOR = _kw("jangka waktu", "selama", "masa kontrak", "masa berlaku")
_PROBATION = re.compile(r"percobaan", re.IGNORECASE)


@dataclass(frozen=True)
class _DateHit:
    start: int
    end: int
    value: date


@dataclass(frozen=True)
class _Anchor:
    end: int
    role: str


def _dates(text: str) -> list[_DateHit]:
    hits: list[_DateHit] = []
    for m in _TEXT_DATE.finditer(text):
        month = month_number(m.group(2))
        value = safe_date(int(m.group(3)), month, int(m.group(1))) if month else None
        if value:
            hits.append(_DateHit(m.start(), m.end(), value))
    for m in _NUMERIC_DATE_OCR.finditer(text):
        value = safe_date(int(m.group(4)), int(m.group(3)), int(m.group(1)))
        if value:
            hits.append(_DateHit(m.start(), m.end(), value))
    return sorted(hits, key=lambda h: h.start)


def _dash_roles(dates: list[_DateHit], text: str) -> dict[int, str]:
    """Roles of dates in a `date - date` pair that follows a period word, by index in `dates`."""
    context_ends = [m.end() for m in _DASH_CONTEXT.finditer(text)]
    roles: dict[int, str] = {}
    for i, (first, second) in enumerate(zip(dates, dates[1:])):
        if not _DASH_BETWEEN.fullmatch(text, first.end, second.start):
            continue
        if any(end <= first.start <= end + ANCHOR_WINDOW for end in context_ends):
            roles[i], roles[i + 1] = _START, _END
    return roles


def _role(hit: _DateHit, anchors: list[_Anchor], text: str) -> str | None:
    """A date right before `s/d` opens a range; otherwise the nearest anchor within the window decides."""
    if _RANGE_RIGHT_AFTER.match(text, hit.end):
        return _START
    preceding = [a for a in anchors if a.end <= hit.start <= a.end + ANCHOR_WINDOW]
    return max(preceding, key=lambda a: a.end).role if preceding else None


def _durations(text: str, cutoff: int, dates: list[_DateHit]) -> list[int]:
    anchor_ends = [m.end() for m in _DURATION_ANCHOR.finditer(text)]
    found: list[int] = []
    for m in _DURATION.finditer(text, cutoff):
        if any(d.start < m.end() and m.start() < d.end for d in dates):
            continue  # `1 bulan Januari` is part of a date, not a duration
        if not any(end <= m.start() <= end + DURATION_WINDOW for end in anchor_ends):
            continue
        if _PROBATION.search(text, max(0, m.start() - PROBATION_WINDOW), m.end() + PROBATION_WINDOW):
            continue
        months = int(m.group(1)) * (12 if m.group(2).lower() == "tahun" else 1)
        if 1 <= months <= MAX_DURATION_MONTHS:
            found.append(months)
    return found


def dates_in_text(text: str) -> set[date]:
    """Every valid four-digit-year date in the text, anchored or not; used to verify extractor answers."""
    return {hit.value for hit in _dates(text)}


def durations_in_text(text: str) -> set[int]:
    """Every `N bulan` / `N tahun` in months that is not part of a date; used to verify extractor answers."""
    dates = _dates(text)
    found = set()
    for m in _DURATION.finditer(text):
        if not any(d.start < m.end() and m.start() < d.end for d in dates):
            found.add(int(m.group(1)) * (12 if m.group(2).lower() == "tahun" else 1))
    return found


def birth_dates(text: str) -> set[date]:
    """The first date after each `lahir`, within BIRTH_DATE_WINDOW; used to check the NIK locally. Wider than
    BIRTH_WINDOW because a sentence puts the place first (`lahir di Kota ... pada tanggal 15 April 2009`)."""
    dates = _dates(text)
    found = set()
    for m in _BIRTH.finditer(text):
        after = [d for d in dates if m.end() <= d.start <= m.end() + BIRTH_DATE_WINDOW]
        if after:
            found.add(after[0].value)
    return found


def _single(values: list[_T]) -> tuple[_T | None, bool]:
    """The one distinct value, and whether several distinct values conflicted."""
    distinct = list(dict.fromkeys(values))
    return (distinct[0] if len(distinct) == 1 else None), len(distinct) > 1


def find_period(text: str) -> RegexPeriod:
    """Collect anchored dates and durations from raw P_id text; runs locally, before any masking."""
    name_label = _NAME_LABEL.search(text)
    cutoff = name_label.start() if name_label else 0
    births = [m.end() for m in _BIRTH.finditer(text)]
    anchors = [_Anchor(m.end(), role) for pattern, role in _ANCHORS for m in pattern.finditer(text)]

    dates = _dates(text)
    dash_roles = _dash_roles(dates, text)
    starts: list[date] = []
    ends: list[date] = []
    for i, hit in enumerate(dates):
        if hit.start < cutoff or any(b <= hit.start <= b + BIRTH_WINDOW for b in births):
            continue
        role = dash_roles.get(i) or _role(hit, anchors, text)
        if role == _START:
            starts.append(hit.value)
        elif role == _END:
            ends.append(hit.value)

    start, start_conflict = _single(starts)
    end, end_conflict = _single(ends)
    duration, duration_conflict = _single(_durations(text, cutoff, dates))
    ambiguous = start_conflict or end_conflict or duration_conflict

    if start and end and end <= start:
        start = end = None
        ambiguous = True
    if start and end and duration and months_between(start, end) != duration:
        start = end = duration = None
        ambiguous = True
    return RegexPeriod(tanggal_mulai=start, tanggal_selesai=end, durasi_bulan=duration, ambigu=ambiguous)
