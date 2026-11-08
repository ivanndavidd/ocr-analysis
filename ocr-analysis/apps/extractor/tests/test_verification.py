from datetime import date

import pytest

from contract_extractor.common.dates import end_from_duration
from contract_extractor.extractor_method.llm.base import DateAnswer, ExtractionResult, MonthsAnswer, TextAnswer
from contract_extractor.common.models import Level, RegexPeriod
from contract_extractor.extractor_method.llm.verification import (
    REASON_NAME_NOT_IN_SOURCE,
    REASON_PERIOD_NOT_IN_SOURCE,
    REASON_POSITION_NOT_IN_SOURCE,
    REASON_REGEX_DISAGREES,
    extraction_level,
    verify_extraction,
)
from contract_extractor.extractor_method.pattern.filename import Match
from contract_extractor.extractor_method.pattern.period_regex import find_period

# Masked payload as the extractor sees it. Names and numbers are fictional.
SOURCE = "\n".join(
    [
        "PERJANJIAN KERJA WAKTU TERTENTU",
        "Nama : Andi Wijaya",
        "Jabatan : Direktur",
        "Nama : Budi Santoso",
        "NIK : [REDACTED-ID]",
        "[REDACTED-LINE]",
        "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Staff Gudang di Cikarang.",
        "Perjanjian ini berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026.",
    ]
)
REGEX = find_period(SOURCE)
PERIOD_QUOTE = "mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026"


def answer(confidence: float = 0.95, **changes) -> ExtractionResult:
    base = {
        "nama_karyawan": TextAnswer(kutipan="Nama : Budi Santoso", nilai="Budi Santoso", confidence=confidence),
        "posisi": TextAnswer(kutipan="PIHAK KEDUA sebagai Staff Gudang di Cikarang", nilai="Staff Gudang", confidence=0.9),
        "tanggal_mulai": DateAnswer(kutipan=PERIOD_QUOTE, nilai=date(2026, 1, 1), confidence=confidence),
        "tanggal_selesai": DateAnswer(kutipan=PERIOD_QUOTE, nilai=date(2026, 12, 31), confidence=confidence),
    }
    return ExtractionResult(**{**base, **changes})


def test_verified_answer_that_agrees_with_regex_is_green():
    verified = verify_extraction(answer(), SOURCE, REGEX)
    assert (verified.name, verified.position, verified.start, verified.end, verified.duration_months) == (
        "Budi Santoso",
        "Staff Gudang",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert verified.written_months is None  # counted from the dates, not written
    assert extraction_level(verified, None) is Level.GREEN
    assert verified.confidence == {"nama": 0.95, "periode": 0.95, "posisi": 0.9}
    assert verified.reasons == []


def test_lower_llm_confidence_is_yellow():
    assert extraction_level(verify_extraction(answer(confidence=0.8), SOURCE, REGEX), None) is Level.YELLOW


def test_regex_disagreement_is_yellow_unless_the_filename_confirms():
    verified = verify_extraction(answer(), SOURCE, RegexPeriod(ambigu=True))
    assert REASON_REGEX_DISAGREES in verified.reasons
    assert extraction_level(verified, None) is Level.YELLOW
    assert extraction_level(verified, Match.MIRIP) is Level.YELLOW
    # The filename is a second, independent source: the model never sees it.
    assert extraction_level(verified, Match.COCOK) is Level.GREEN


def test_regex_reading_another_period_is_red_unless_the_filename_sides_with_the_answer():
    other = RegexPeriod(tanggal_mulai=date(2026, 2, 1), tanggal_selesai=date(2026, 12, 31))
    verified = verify_extraction(answer(), SOURCE, other)
    assert verified.regex_conflicts
    assert extraction_level(verified, None) is Level.RED
    assert extraction_level(verified, Match.COCOK) is Level.YELLOW


def test_name_not_in_the_source_is_rejected():
    verified = verify_extraction(
        answer(nama_karyawan=TextAnswer(kutipan="Nama : Citra Lestari", nilai="Citra Lestari", confidence=0.9)),
        SOURCE,
        REGEX,
    )
    assert verified.name is None
    assert REASON_NAME_NOT_IN_SOURCE in verified.reasons
    assert extraction_level(verified, Match.COCOK) is Level.YELLOW  # a missing piece, as in pattern mode
    assert verified.confidence["nama"] == 0.0
    assert verified.start == date(2026, 1, 1)  # the other fields keep their own verification


def test_first_name_alone_does_not_verify_a_full_name():
    verified = verify_extraction(
        answer(nama_karyawan=TextAnswer(kutipan="Nama : Budi Santoso", nilai="Budi Hartono", confidence=0.9)),
        SOURCE,
        REGEX,
    )
    assert verified.name is None


def test_a_name_without_its_quote_is_rejected():
    verified = verify_extraction(
        answer(nama_karyawan=TextAnswer(kutipan=None, nilai="Budi Santoso", confidence=0.9)), SOURCE, REGEX
    )
    assert verified.name is None


def test_date_not_in_the_source_is_rejected():
    verified = verify_extraction(
        answer(tanggal_mulai=DateAnswer(kutipan=PERIOD_QUOTE, nilai=date(2026, 2, 1), confidence=0.9)), SOURCE, REGEX
    )
    assert (verified.start, verified.end) == (None, date(2026, 12, 31))
    assert REASON_PERIOD_NOT_IN_SOURCE in verified.reasons
    assert extraction_level(verified, None) is Level.YELLOW


def test_dates_in_the_wrong_roles_are_both_dropped():
    verified = verify_extraction(
        answer(
            tanggal_mulai=DateAnswer(kutipan=PERIOD_QUOTE, nilai=date(2026, 12, 31), confidence=0.9),
            tanggal_selesai=DateAnswer(kutipan=PERIOD_QUOTE, nilai=date(2026, 1, 1), confidence=0.9),
        ),
        SOURCE,
        REGEX,
    )
    assert (verified.start, verified.end) == (None, None)
    assert REASON_PERIOD_NOT_IN_SOURCE in verified.reasons


def test_position_must_be_in_its_quote_and_the_source():
    made_up = answer(posisi=TextAnswer(kutipan="sebagai Kasir", nilai="Kasir", confidence=0.9))
    verified = verify_extraction(made_up, SOURCE, REGEX)
    assert verified.position is None
    assert REASON_POSITION_NOT_IN_SOURCE in verified.reasons
    assert verified.confidence["posisi"] == 0.0

    outside_quote = answer(posisi=TextAnswer(kutipan="Jabatan : Direktur", nilai="Staff Gudang", confidence=0.9))
    assert verify_extraction(outside_quote, SOURCE, REGEX).position is None


def test_duration_only_contract_gets_its_end_date_computed():
    source = "Nama : Budi Santoso\nJangka waktu 12 (dua belas) bulan terhitung sejak tanggal 1 Januari 2026."
    quote = "12 (dua belas) bulan terhitung sejak tanggal 1 Januari 2026"
    verified = verify_extraction(
        answer(
            tanggal_mulai=DateAnswer(kutipan=quote, nilai=date(2026, 1, 1), confidence=0.95),
            tanggal_selesai=DateAnswer(),
            durasi_bulan=MonthsAnswer(kutipan=quote, nilai=12, confidence=0.95),
            posisi=TextAnswer(),
        ),
        source,
        find_period(source),
    )
    assert (verified.end, verified.duration_months, verified.written_months) == (date(2026, 12, 31), 12, 12)
    assert extraction_level(verified, None) is Level.GREEN


def test_duration_that_is_not_in_the_source_is_rejected():
    verified = verify_extraction(
        answer(tanggal_selesai=DateAnswer(), durasi_bulan=MonthsAnswer(kutipan="selama 6 bulan", nilai=6, confidence=0.9)),
        SOURCE,
        REGEX,
    )
    assert verified.end is None and verified.written_months is None
    assert REASON_PERIOD_NOT_IN_SOURCE in verified.reasons


def test_an_empty_answer_is_yellow_without_reasons():
    verified = verify_extraction(ExtractionResult(), SOURCE, REGEX)
    assert extraction_level(verified, None) is Level.YELLOW and verified.reasons == []


def test_out_of_range_values_become_empty_instead_of_failing_the_answer():
    parsed = ExtractionResult.model_validate(
        {
            "nama_karyawan": {"kutipan": "x", "nilai": "  Budi   Santoso ", "confidence": 1.7},
            "durasi_bulan": {"kutipan": "x", "nilai": 500, "confidence": -1},
            "posisi": {"kutipan": "x", "nilai": "x" * 300, "confidence": 0.5},
        }
    )
    assert (parsed.nama_karyawan.nilai, parsed.nama_karyawan.confidence) == ("Budi Santoso", 1.0)
    assert (parsed.durasi_bulan.nilai, parsed.durasi_bulan.confidence) == (None, 0.0)
    assert parsed.posisi.nilai is None


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


def test_the_vendor_must_be_in_its_quote_and_the_source():
    source = SOURCE + "\nDalam hal ini bertindak untuk dan atas nama PT Contoh Sejahtera"
    quote = "bertindak untuk dan atas nama PT Contoh Sejahtera"
    kept = verify_extraction(answer(vendor=TextAnswer(kutipan=quote, nilai="PT Contoh Sejahtera", confidence=0.9)), source, None)
    assert kept.vendor == "PT Contoh Sejahtera"
    for vendor, vendor_quote in [("PT Lain Jaya", quote), ("PT Contoh Sejahtera", "atas nama PT Contoh Abadi"), ("PT Contoh Sejahtera", None)]:
        read = TextAnswer(kutipan=vendor_quote, nilai=vendor, confidence=0.9)
        dropped = verify_extraction(answer(vendor=read), source, None)
        assert dropped.vendor is None
        assert not any("vendor" in r for r in dropped.reasons)  # the folder is then looked up instead
