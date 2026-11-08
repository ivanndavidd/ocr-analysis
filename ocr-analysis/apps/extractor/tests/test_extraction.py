from datetime import date

import pytest

from contract_extractor.dates import end_from_duration
from contract_extractor.extractors.base import Evidence, ExtractionResult
from contract_extractor.models import Level, RegexPeriod
from contract_extractor.stages.extraction import (
    REASON_NAME_NOT_IN_SOURCE,
    REASON_PERIOD_NOT_IN_SOURCE,
    REASON_REGEX_DISAGREES,
    verify_extraction,
)
from contract_extractor.stages.period_regex import find_period

# Masked payload as the extractor sees it. Names and numbers are fictional.
SOURCE = "\n".join(
    [
        "PERJANJIAN KERJA WAKTU TERTENTU",
        "Nama : Andi Wijaya",
        "Jabatan : Direktur",
        "Nama : Budi Santoso",
        "NIK : [REDACTED-ID]",
        "[REDACTED-LINE]",
        "Perjanjian ini berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026.",
    ]
)
REGEX = find_period(SOURCE)


def answer(**changes) -> ExtractionResult:
    base = {
        "nama_karyawan": "Budi Santoso",
        "tanggal_mulai": date(2026, 1, 1),
        "tanggal_selesai": date(2026, 12, 31),
        "bukti": Evidence(nama="Nama : Budi Santoso", periode="mulai tanggal 1 Januari 2026 sampai dengan tanggal 31"),
        "confidence": 0.95,
    }
    return ExtractionResult(**{**base, **changes})


def test_verified_answer_that_agrees_with_regex_is_green():
    verified = verify_extraction(answer(), SOURCE, REGEX)
    assert (verified.name, verified.start, verified.end, verified.duration_months) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert verified.level is Level.GREEN
    assert verified.confidence == {"nama": 0.95, "periode": 0.95}
    assert verified.reasons == []


def test_lower_llm_confidence_is_yellow():
    assert verify_extraction(answer(confidence=0.8), SOURCE, REGEX).level is Level.YELLOW


def test_regex_disagreement_is_yellow_not_red():
    verified = verify_extraction(answer(), SOURCE, RegexPeriod(ambigu=True))
    assert verified.level is Level.YELLOW
    assert REASON_REGEX_DISAGREES in verified.reasons


def test_name_not_in_the_source_is_rejected():
    verified = verify_extraction(
        answer(nama_karyawan="Citra Lestari", bukti=Evidence(nama="Nama : Citra Lestari", periode="mulai tanggal 1")),
        SOURCE,
        REGEX,
    )
    assert verified.name is None
    assert REASON_NAME_NOT_IN_SOURCE in verified.reasons
    assert verified.level is Level.RED
    assert verified.confidence["nama"] == 0.0


def test_first_name_alone_does_not_verify_a_full_name():
    verified = verify_extraction(answer(nama_karyawan="Budi Hartono"), SOURCE, REGEX)
    assert verified.name is None


def test_date_not_in_the_source_is_rejected():
    verified = verify_extraction(answer(tanggal_mulai=date(2026, 2, 1)), SOURCE, REGEX)
    assert (verified.start, verified.end) == (None, None)
    assert REASON_PERIOD_NOT_IN_SOURCE in verified.reasons
    assert verified.level is Level.RED


def test_missing_evidence_quote_is_red():
    verified = verify_extraction(answer(bukti=Evidence(nama=None, periode=None)), SOURCE, REGEX)
    assert verified.level is Level.RED


def test_duration_only_contract_gets_its_end_date_computed():
    source = "Nama : Budi Santoso\nJangka waktu 12 (dua belas) bulan terhitung sejak tanggal 1 Januari 2026."
    verified = verify_extraction(
        answer(
            tanggal_selesai=None,
            durasi_bulan=12,
            bukti=Evidence(nama="Nama : Budi Santoso", periode="12 (dua belas) bulan terhitung sejak tanggal 1 Januari 2026"),
        ),
        source,
        find_period(source),
    )
    assert (verified.end, verified.duration_months, verified.level) == (date(2026, 12, 31), 12, Level.GREEN)


def test_duration_that_is_not_in_the_source_is_rejected():
    verified = verify_extraction(answer(tanggal_selesai=None, durasi_bulan=6), SOURCE, REGEX)
    assert verified.start is None and verified.level is Level.RED


def test_null_fields_are_red():
    verified = verify_extraction(ExtractionResult(confidence=0.99), SOURCE, REGEX)
    assert verified.level is Level.RED and verified.reasons == []


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (date(2026, 1, 1), 12, date(2026, 12, 31)),
        (date(2026, 1, 15), 3, date(2026, 4, 14)),
        (date(2026, 11, 1), 3, date(2027, 1, 31)),
        (date(2026, 1, 31), 1, None),  # 31 February does not exist: left for review
    ],
)
def test_end_from_duration(start, months, end):
    assert end_from_duration(start, months) == end
