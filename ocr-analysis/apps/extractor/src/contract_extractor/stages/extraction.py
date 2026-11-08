"""Tahap 3c: verifikasi lokal jawaban extractor terhadap teks yang dikirim, lalu level ekstraksi (Bagian 10 dan 12 PRD).

Jawaban LLM tidak dipercaya begitu saja: nama dan tanggal yang tidak ada di teks sumber dibuang (jadi null),
bukan diterima. Teks sumber adalah payload ter-mask, persis yang dilihat LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from contract_extractor.dates import end_from_duration, months_between
from contract_extractor.extractors.base import ExtractionResult
from contract_extractor.models import Level, RegexPeriod
from contract_extractor.stages.filename import name_in_text, normalize_name
from contract_extractor.stages.period_regex import dates_in_text, durations_in_text
from contract_extractor.stages.scoring import extraction_level

EVIDENCE_MIN = 90  # bukti must be found in the source text at least this fuzzily
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

REASON_NAME_NOT_IN_SOURCE = "name_not_in_source"
REASON_PERIOD_NOT_IN_SOURCE = "period_not_in_source"
REASON_REGEX_DISAGREES = "period_regex_disagrees"


@dataclass
class VerifiedExtraction:
    name: str | None
    start: date | None
    end: date | None
    duration_months: int | None
    level: Level
    confidence: dict[str, float]
    reasons: list[str] = field(default_factory=list)


def _plain(text: str) -> str:
    """Lowercase letters and digits only; digits stay because a period quote is mostly dates."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _quote_found(quote: str | None, source: str) -> bool:
    quote_norm = _plain(quote or "")
    return bool(quote_norm) and fuzz.partial_ratio(quote_norm, _plain(source)) >= EVIDENCE_MIN


def _name_found(name: str, source: str) -> bool:
    name_norm = normalize_name(name)
    return any(name_in_text(name_norm, line) for line in source.splitlines())


def _period_found(extraction: ExtractionResult, source: str) -> bool:
    if not _quote_found(extraction.bukti.periode, source):
        return False
    dates = dates_in_text(source)
    if any(d is not None and d not in dates for d in (extraction.tanggal_mulai, extraction.tanggal_selesai)):
        return False
    duration = extraction.durasi_bulan
    if duration is None:
        return True
    from_dates = (
        months_between(extraction.tanggal_mulai, extraction.tanggal_selesai)
        if extraction.tanggal_mulai and extraction.tanggal_selesai
        else None
    )
    return duration in durations_in_text(source) or duration == from_dates


def regex_agrees(start: date | None, end: date | None, duration: int | None, regex: RegexPeriod | None) -> bool:
    """The local regex path found the same period, and found it without conflicting candidates."""
    if regex is None or regex.ambigu or start is None or regex.tanggal_mulai != start:
        return False
    if regex.tanggal_selesai is not None:
        return regex.tanggal_selesai == end
    if regex.durasi_bulan is not None:
        return regex.durasi_bulan == duration
    return False


def verify_extraction(extraction: ExtractionResult, source: str, regex: RegexPeriod | None) -> VerifiedExtraction:
    reasons: list[str] = []

    name = (extraction.nama_karyawan or "").strip() or None
    name_ok = name is not None and _quote_found(extraction.bukti.nama, source) and _name_found(name, source)
    if name and not name_ok:
        reasons.append(REASON_NAME_NOT_IN_SOURCE)
        name = None

    period_ok = _period_found(extraction, source)
    start, end, duration = extraction.tanggal_mulai, extraction.tanggal_selesai, extraction.durasi_bulan
    if not period_ok:
        if start or end or duration:
            reasons.append(REASON_PERIOD_NOT_IN_SOURCE)
        start = end = duration = None
    if start and end is None and duration:
        end = end_from_duration(start, duration)
    if start and end and duration is None:
        duration = months_between(start, end)

    agrees = regex_agrees(start, end, duration, regex)
    if start and not agrees:
        reasons.append(REASON_REGEX_DISAGREES)

    any_null = name is None or start is None or end is None
    level = extraction_level(extraction.confidence, name_ok and period_ok, agrees, any_null)
    confidence = {
        "nama": extraction.confidence if name_ok else 0.0,
        "periode": extraction.confidence if period_ok and start else 0.0,
    }
    return VerifiedExtraction(name, start, end, duration, level, confidence, reasons)
