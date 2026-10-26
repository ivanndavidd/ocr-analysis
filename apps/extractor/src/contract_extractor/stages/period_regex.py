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

from contract_extractor.dates import MONTH_PATTERN, NUMERIC_DATE, month_number, months_between, safe_date
from contract_extractor.models import RegexPeriod

ANCHOR_WINDOW = 120  # a date counts only within this many characters after an anchor
BIRTH_WINDOW = 40  # dates this close after `lahir` are birth dates
DURATION_WINDOW = 60
PROBATION_WINDOW = 40
MAX_DURATION_MONTHS = 120

_START, _END = "start", "end"
_T = TypeVar("_T")


def _words(*phrases: str) -> str:
    return "|".join(r"\s+".join(re.escape(tok) for tok in p.split()) for p in phrases)


def _kw(*phrases: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z])(?:{_words(*phrases)})(?![a-z])", re.IGNORECASE)


# `1 Januari 2026`, `01 Jan. 2026`, `1 (satu) Januari 2026`, `tanggal 1 bulan Januari tahun 2026`.
_TEXT_DATE = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:\([a-z\s]+\)\s*)?(?:bulan\s+)?({MONTH_PATTERN})\.?,?\s+(?:tahun\s+)?(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_RANGE_WORDS = rf"(?:{_words('sampai dengan', 'hingga')}|s\s*/\s*d)(?![a-z])"
_RANGE = re.compile(rf"(?<![a-z]){_RANGE_WORDS}", re.IGNORECASE)
_RANGE_RIGHT_AFTER = re.compile(rf"[\s,]*{_RANGE_WORDS}", re.IGNORECASE)
_ANCHORS: list[tuple[re.Pattern[str], str]] = [
    (_kw("mulai", "dimulai", "berlaku efektif", "sejak", "terhitung"), _START),
    (_kw("berakhir"), _END),
    (_RANGE, _END),
]
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
    for m in NUMERIC_DATE.finditer(text):
        value = safe_date(int(m.group(4)), int(m.group(3)), int(m.group(1)))
        if value:
            hits.append(_DateHit(m.start(), m.end(), value))
    return sorted(hits, key=lambda h: h.start)


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
    starts: list[date] = []
    ends: list[date] = []
    for hit in dates:
        if hit.start < cutoff or any(b <= hit.start <= b + BIRTH_WINDOW for b in births):
            continue
        role = _role(hit, anchors, text)
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
