from datetime import date

import pytest
from helpers import IDENTITY_LINES, PERIOD_LINES, flow, page_text

from contract_extractor.common.models import RegexPeriod
from contract_extractor.extractor_method.pattern.period_regex import find_period

NAME_BLOCK = "Nama : Budi Santoso\nNIK : 3171012345670001\n"


def period(text: str) -> RegexPeriod:
    return find_period(NAME_BLOCK + text)


def test_identity_page_gives_start_and_end_but_not_the_birth_date():
    found = find_period("\n".join(IDENTITY_LINES + PERIOD_LINES))
    assert found == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))


def test_signing_date_in_the_opening_paragraph_is_ignored():
    text = (
        "Perjanjian ini dibuat dan mulai berlaku pada tanggal 29 Desember 2025 oleh para pihak:\n"
        + NAME_BLOCK
        + "Perjanjian dimulai pada tanggal 5 Januari 2026 dan berakhir pada tanggal 4 Januari 2027."
    )
    assert find_period(text) == RegexPeriod(tanggal_mulai=date(2026, 1, 5), tanggal_selesai=date(2027, 1, 4))


def test_birth_date_after_an_anchor_is_ignored():
    found = period("Data berikut berlaku sejak penandatanganan. Tempat/Tanggal Lahir : Bandung, 02-02-1991")
    assert found == RegexPeriod()


@pytest.mark.parametrize(
    "sentence",
    [
        "berlaku efektif mulai tanggal 01-01-2026 sampai dengan tanggal 31-12-2026.",
        "berlaku efektif mulai tanggal 01/01/2026 s/d 31/12/2026.",
        "berlaku efektif mulai tanggal 01.01.2026 hingga 31.12.2026.",
        "dimulai 1 Jan. 2026 dan berakhir 31 Des. 2026.",
        "dimulai 1 (satu) Januari 2026 dan berakhir 31 (tiga puluh satu) Desember 2026.",
        "dimulai pada tanggal 1 bulan Januari tahun 2026 sampai dengan tanggal 31 bulan Desember tahun 2026.",
        "dimulai 1 January 2026 dan berakhir 31 December 2026.",
        "Periode kontrak 1 Januari 2026 s/d 31 Desember 2026.",
        "Periode kontrak 1 Januari 2026 s.d. 31 Desember 2026.",
        "Periode kontrak 1 Januari 2026 s.d 31 Desember 2026.",
        "terhitung mulai tanggal 1 Januari\n2026 sampai dengan\ntanggal 31 Desember 2026.",
    ],
)
def test_date_forms(sentence):
    assert period(sentence) == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))


# Tesseract output of synthetic scans whose period the regex used to miss.
@pytest.mark.parametrize(
    "ocr_text",
    [
        # `sampai` read as `sampal`, `berlaku` as `bertaku`
        "1. Perjanjian ini bertaku efektif mulai tanggal 16-04-2045 sampal dengan 15-04-2047, kecuali:",
        # a space after a separator
        "1. Perjanjian ini berlaku efektif mulai tanggal 16-04- 2045 sampai dengan 15-04-2047, kecuali:",
        # `s.d.` with table debris
        "1. Jangka waktu Perjanjian ini adalah selama 24 (dua puluh empat) bulan, yaitu mulai tanggal : |\n"
        "16 April 2045 s.d. 15 April 2047. |",
        # a stray quote between day and month
        '1. Jangka waktu Perjanjian ini adalah selama 24 (dua puluh empat) bulan, yaitu mulai tanggal 16 "\n'
        "April 2045 s.d. 15 April 2047.",
        # a table border read as `|` at the start of the next line, inside `sampai dengan`
        "1. Masa berlaku Perjanjian ini adalah terhitung mulai tanggal 16 April 2045 sampai\n"
        "| dengan tanggal 15 April 2047. :",
    ],
)
def test_ocr_slips_in_scans(ocr_text):
    found = period(ocr_text)
    assert (found.tanggal_mulai, found.tanggal_selesai, found.ambigu) == (date(2045, 4, 16), date(2047, 4, 15), False)


@pytest.mark.parametrize("scraps", [["|"], ["|", "| |"], ["�"]])
def test_border_scraps_read_as_lines_do_not_split_sampai_dengan(scraps):
    # OCR read a table border between the two words; before, the end date took `mulai` as its anchor: two starts.
    lines = ["Nama : Budi Santoso", "1. Masa berlaku Perjanjian ini adalah terhitung mulai tanggal 1 Juni 2049 sampai"]
    page = page_text(1, flow([*lines, *scraps, "dengan tanggal 31 Agustus 2049."]))
    found = find_period(page.text)
    assert (found.tanggal_mulai, found.tanggal_selesai, found.ambigu) == (date(2049, 6, 1), date(2049, 8, 31), False)


@pytest.mark.parametrize(
    "sentence",
    [
        "1. Hubungan kerja berdasarkan Perjanjian ini berlangsung untuk periode 7 Februari 2032\n"
        "(tujuh februari dua ribu tiga puluh dua) - 6 Mei 2032 (enam mei dua ribu tiga puluh dua).",
        "Periode kontrak: 07-02-2032 – 06-05-2032.",
    ],
)
def test_dash_between_two_dates_after_a_period_word(sentence):
    assert period(sentence) == RegexPeriod(tanggal_mulai=date(2032, 2, 7), tanggal_selesai=date(2032, 5, 6))


def test_dash_between_two_dates_without_a_period_word_is_ignored():
    assert period("Upah lembur dihitung 1 Januari 2026 - 31 Januari 2026.") == RegexPeriod()


def test_school_level_sd_is_not_a_range_word():
    assert period("Pendidikan terakhir : SD 1 Juli 2010.") == RegexPeriod()


def test_two_digit_years_are_never_read_from_document_text():
    assert period("dimulai 1 Januari 26 sampai dengan 31/12/26.") == RegexPeriod()


def test_dates_beyond_the_anchor_window_are_not_collected():
    filler = "Pihak Kedua wajib mematuhi seluruh peraturan perusahaan yang berlaku tanpa terkecuali. " * 2
    assert period("Perjanjian ini dimulai. " + filler + "Pada 1 Januari 2026 diadakan rapat.") == RegexPeriod()


def test_duration_only():
    found = period("Jangka waktu perjanjian ini adalah 12 (dua belas) bulan terhitung sejak tanggal 1 Januari 2026.")
    assert found == RegexPeriod(tanggal_mulai=date(2026, 1, 1), durasi_bulan=12)


def test_duration_in_years():
    assert period("Perjanjian ini berlaku untuk jangka waktu 1 (satu) tahun.").durasi_bulan == 12


def test_probation_duration_is_not_the_contract_duration():
    found = period("Masa percobaan selama 3 (tiga) bulan.\nJangka waktu perjanjian ini 12 bulan.")
    assert (found.durasi_bulan, found.ambigu) == (12, False)


def test_unanchored_durations_are_ignored():
    assert period("Upah dibayarkan setiap tanggal 25 bulan berjalan.").durasi_bulan is None


def test_matching_duration_and_dates_agree():
    found = period("Jangka waktu 12 bulan, dimulai 1 Januari 2026 sampai dengan 31 Desember 2026.")
    assert found == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31), durasi_bulan=12)


def test_conflicting_start_dates_leave_start_null():
    found = period("Kontrak dimulai 1 Januari 2026. Pasal 5: kontrak dimulai 1 Februari 2026 dan berakhir 31 Desember 2026.")
    assert found == RegexPeriod(tanggal_selesai=date(2026, 12, 31), ambigu=True)


def test_repeated_identical_dates_are_not_a_conflict():
    found = period("dimulai 1 Januari 2026 s/d 31 Desember 2026. Perjanjian berakhir pada 31 Desember 2026.")
    assert found == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))


def test_end_before_start_is_rejected():
    assert period("dimulai 31 Desember 2026 dan berakhir 1 Januari 2026.") == RegexPeriod(ambigu=True)


def test_duration_disagreeing_with_dates_nulls_all_three():
    found = period("Jangka waktu 6 bulan, dimulai 1 Januari 2026 sampai dengan 31 Desember 2026.")
    assert found == RegexPeriod(ambigu=True)


def test_without_a_name_block_nothing_is_cut_off():
    found = find_period("Perjanjian ini dimulai 1 Maret 2026 dan berakhir 31 Mei 2026.")
    assert found == RegexPeriod(tanggal_mulai=date(2026, 3, 1), tanggal_selesai=date(2026, 5, 31))


def test_invalid_calendar_dates_are_skipped():
    assert period("dimulai 31 Februari 2026 sampai dengan 31-13-2026.") == RegexPeriod()
