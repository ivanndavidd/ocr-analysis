"""Tahap 3c: verifikasi lokal jawaban extractor terhadap teks yang dikirim, lalu level ekstraksi (Bagian 10 dan 12 PRD).

Jawaban LLM tidak dipercaya begitu saja: setiap field harus disertai kutipan yang ada di teks sumber, dan nilai yang
tidak ada di teks sumber dibuang (jadi null), bukan diterima. Teks sumber adalah payload ter-mask, persis yang
dilihat LLM. Field diverifikasi sendiri-sendiri, jadi satu field yang salah tidak membuang yang lain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from contract_extractor.common.dates import end_from_duration, months_between
from contract_extractor.extractor_method.llm.base import ExtractionResult
from contract_extractor.common.models import Level, RegexPeriod
from contract_extractor.extractor_method.pattern.filename import Match, name_printed, normalize_name
from contract_extractor.extractor_method.pattern.period_regex import dates_in_text, durations_in_text

EVIDENCE_MIN = 90  # a quote must be found in the source text at least this fuzzily
SURE = 0.9  # the model's own confidence a field needs before the extraction can be green
# A job title the model fixed for an OCR misread may differ from its quote by one letter edit per this many letters,
# at least one: `Kurlr` -> `Kurir` and `Fi inance` -> `Finance` pass, `Kurir` -> `Kasir` and `Gudang` -> `Sales` don't.
FIX_LETTERS_PER_EDIT = 6
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

REASON_NAME_NOT_IN_SOURCE = "name_not_in_source"
REASON_PERIOD_NOT_IN_SOURCE = "period_not_in_source"
REASON_POSITION_NOT_IN_SOURCE = "position_not_in_source"
REASON_REGEX_DISAGREES = "period_regex_disagrees"


@dataclass
class VerifiedExtraction:
    name: str | None
    position: str | None
    start: date | None
    end: date | None  # as written, or counted from the start and a written length
    duration_months: int | None  # as written, or counted from the dates
    written_months: int | None  # only what the document writes, for the period record
    regex_agrees: bool  # the local regex read the same period
    regex_conflicts: bool  # the local regex read both dates, unambiguously, and they differ
    confidence: dict[str, float]  # nama, periode, posisi: the model's own, 0.0 for a dropped field
    reasons: list[str] = field(default_factory=list)
    vendor: str | None = None  # the employer company, inside its quote and the quote inside the source
    position_fixed: bool = False  # the title is not printed letter for letter: the model fixed an OCR misread


def _plain(text: str) -> str:
    """Lowercase letters and digits only; digits stay because a period quote is mostly dates."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _quote_found(quote: str | None, source: str) -> bool:
    quote_norm = _plain(quote or "")
    return bool(quote_norm) and fuzz.partial_ratio(quote_norm, _plain(source)) >= EVIDENCE_MIN


def _name_found(name: str, source: str) -> bool:
    name_norm = normalize_name(name)
    return any(name_printed(name_norm, line) for line in source.splitlines())


def _text_found(value: str, quote: str | None, source: str) -> bool:
    """A free-text value (the job title) must be inside its own quote, and the quote inside the source."""
    value_norm = _plain(value)
    return (
        bool(value_norm)
        and _quote_found(quote, source)
        and fuzz.partial_ratio(value_norm, _plain(quote or "")) >= EVIDENCE_MIN
    )


def _printed(value: str, source: str) -> bool:
    """The value's words, letter for letter and in order, in the source (case and punctuation aside)."""
    return f" {_plain(value)} " in f" {_plain(source)} "


def _small_fix(value: str, quote: str | None) -> bool:
    """The value is a few letters away from a run of the quote's words (one word more or fewer than the value has):
    the model fixed an OCR misread of a word, rather than writing another word."""
    value_norm, words = _plain(value), _plain(quote or "").split()
    if not value_norm or not words:
        return False
    allowed = max(1, len(value_norm) // FIX_LETTERS_PER_EDIT)
    size = len(value_norm.split())
    spans = (
        " ".join(words[start : start + n])
        for n in range(max(1, size - 1), size + 2)
        for start in range(0, max(1, len(words) - n + 1))
    )
    return any(Levenshtein.distance(value_norm, span, score_cutoff=allowed) <= allowed for span in spans)


def _position_found(value: str, quote: str | None, source: str) -> tuple[bool, bool]:
    """Whether the job title is backed by its quote and the source, and whether the model fixed an OCR misread in
    it. A fixed title must still be close to its quote (`_text_found`, or a few letters away by `_small_fix`)."""
    if not _quote_found(quote, source):
        return False, False
    if not (_text_found(value, quote, source) or _small_fix(value, quote)):
        return False, False
    return True, not _printed(value, source)


def regex_agrees(start: date | None, end: date | None, duration: int | None, regex: RegexPeriod | None) -> bool:
    """The local regex path found the same period, and found it without conflicting candidates."""
    if regex is None or regex.ambigu or start is None or regex.tanggal_mulai != start:
        return False
    if regex.tanggal_selesai is not None:
        return regex.tanggal_selesai == end
    if regex.durasi_bulan is not None:
        return regex.durasi_bulan == duration
    return False


def _regex_conflicts(start: date | None, end: date | None, regex: RegexPeriod | None) -> bool:
    if regex is None or regex.ambigu or not (regex.tanggal_mulai and regex.tanggal_selesai and start and end):
        return False
    return (regex.tanggal_mulai, regex.tanggal_selesai) != (start, end)


def verify_extraction(extraction: ExtractionResult, source: str, regex: RegexPeriod | None) -> VerifiedExtraction:
    reasons: list[str] = []

    answer = extraction.nama_karyawan
    name = answer.nilai
    if name and not (_quote_found(answer.kutipan, source) and _name_found(name, source)):
        reasons.append(REASON_NAME_NOT_IN_SOURCE)
        name = None

    answer = extraction.posisi
    position, position_fixed = answer.nilai, False
    if position:
        found, position_fixed = _position_found(position, answer.kutipan, source)
        if not found:
            reasons.append(REASON_POSITION_NOT_IN_SOURCE)
            position = None

    # A vendor that fails verification is only dropped: the folder's vendor is then looked up in the text instead.
    vendor = extraction.vendor.nilai
    if vendor and not _text_found(vendor, extraction.vendor.kutipan, source):
        vendor = None

    dates = dates_in_text(source)

    def checked_date(value: date | None, quote: str | None) -> date | None:
        return value if value is not None and value in dates and _quote_found(quote, source) else None

    start = checked_date(extraction.tanggal_mulai.nilai, extraction.tanggal_mulai.kutipan)
    end = checked_date(extraction.tanggal_selesai.nilai, extraction.tanggal_selesai.kutipan)
    written = extraction.durasi_bulan.nilai
    if written is not None and not (
        written in durations_in_text(source) and _quote_found(extraction.durasi_bulan.kutipan, source)
    ):
        written = None
    if start and end and end <= start:  # two real dates in the wrong roles: neither can be trusted
        start = end = None
    answered = (extraction.tanggal_mulai.nilai, extraction.tanggal_selesai.nilai, extraction.durasi_bulan.nilai)
    if (start, end, written) != answered:
        reasons.append(REASON_PERIOD_NOT_IN_SOURCE)

    counted_end = end or (end_from_duration(start, written) if start and written else None)
    duration = written or (months_between(start, end) if start and end else None)
    agrees = regex_agrees(start, counted_end, duration, regex)
    if start and not agrees:
        reasons.append(REASON_REGEX_DISAGREES)

    period_conf = [extraction.tanggal_mulai.confidence]
    period_conf.append(extraction.tanggal_selesai.confidence if end else extraction.durasi_bulan.confidence)
    confidence = {
        "nama": extraction.nama_karyawan.confidence if name else 0.0,
        "periode": min(period_conf) if start and counted_end else 0.0,
        "posisi": extraction.posisi.confidence if position else 0.0,
    }
    return VerifiedExtraction(
        name,
        position,
        start,
        counted_end,
        duration,
        written,
        agrees,
        _regex_conflicts(start, counted_end, regex),
        confidence,
        reasons,
        vendor,
        position_fixed,
    )


def extraction_level(verified: VerifiedExtraction, filename_period: Match | None) -> Level:
    """Green when the name and the whole period are verified in the source, the model is sure of them, and a second,
    independent source reads the same period: the local regex, or a standard filename. Like pattern mode, a missing
    piece is yellow; red is kept for a contradiction the filename does not settle."""
    if verified.regex_conflicts and filename_period is not Match.COCOK:
        return Level.RED
    if verified.name is None or verified.start is None or verified.end is None:
        return Level.YELLOW
    sure = min(verified.confidence["nama"], verified.confidence["periode"]) >= SURE
    confirmed = verified.regex_agrees or (filename_period is Match.COCOK and not verified.regex_conflicts)
    return Level.GREEN if sure and confirmed else Level.YELLOW
