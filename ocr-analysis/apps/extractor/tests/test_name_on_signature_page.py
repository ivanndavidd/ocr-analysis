"""The filename's name confirmed in the employee's column of the signature page when the identity page does not show
it. Synthetic names and pages only."""

from datetime import date

import pytest
from helpers import BODY_FILLER, flow, page_text, signature_rows

from contract_extractor import pipeline
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import FileResult, Level, RegexPeriod
from contract_extractor.extractor_method.pattern.filename import parse_filename
from contract_extractor.signature.signature import employee_name_printed


def _closing_page(rows: list[tuple[float, float, str]], number: int = 5) -> PageText:
    return page_text(number, flow([BODY_FILLER] * 18) + rows)


def _block(company: str = "Andi Wijaya", employee: str = "Budi Santoso") -> PageText:
    """signature_rows' two columns: PIHAK PERTAMA and the company signer left, PIHAK KEDUA and the employee right."""
    rows = signature_rows(350.0, employee)
    return _closing_page([(y, x, company if t == "Andi Wijaya" else t) for y, x, t in rows])


def test_the_employee_column_confirms_the_name():
    assert employee_name_printed(_block(), "Budi Santoso")
    assert employee_name_printed(_block(employee="BUDI SANTOSO, S.Kom"), "Budi Santoso")  # titles are dropped


def test_the_company_signer_never_confirms_a_name():
    assert not employee_name_printed(_block(), "Andi Wijaya")


@pytest.mark.parametrize(
    "filename_name",
    [
        "Budi S.",  # an initial: a shortened name can match someone else's
        "Gara N.",  # the stress-test case, the company signer `Kasim Garang`
        "Budi",  # too short to tell apart
        "Budi Hartono",  # first name only in common
    ],
)
def test_short_shortened_or_other_names_are_not_confirmed(filename_name):
    assert not employee_name_printed(_block(company="Kasim Garang"), filename_name)


@pytest.mark.parametrize(
    "printed, filename_name, confirmed",
    [
        ("Dasa Jaiman", "Diana Jaiman", False),  # same surname, another first name: passed as one string before
        ("Dinda Yolanda", "Sadina Yolanda", False),
        ("Adhiarja Siregar", "Adhiarja", False),  # a first name alone is not the printed name
        ("BUDI SANT0SO", "Budi Santoso", True),  # one OCR slip in a long word
        ("BUDI SANTOSA", "Budi Santoso", True),  # cannot be told from an OCR slip
        ("ABD KARIM", "Abd.karim", True),  # OCR split the word
        ("ABDKARIM HARAHAP", "Abd Karim Harahap", True),  # OCR joined two words
        ("RINA SARI", "Rini Sari", False),  # a slip in a four-letter word is another name
    ],
)
def test_the_printed_name_must_be_the_filename_name_word_by_word(printed, filename_name, confirmed):
    assert employee_name_printed(_block(employee=printed), filename_name) is confirmed


def test_a_name_in_both_columns_is_not_confirmed():
    assert not employee_name_printed(_block(company="Budi Santoso"), "Budi Santoso")


def test_a_name_without_any_party_label_is_not_confirmed():
    rows = [(r[0], r[1], r[2]) for r in signature_rows(350.0) if r[2] not in ("PIHAK PERTAMA", "PIHAK KEDUA", "Direktur", "Tenaga Kerja")]
    assert not employee_name_printed(_closing_page(rows), "Budi Santoso")


def test_a_title_right_under_the_name_decides_the_party():
    # Stacked blocks: the employee's name sits under the company's label, but its own title says employee.
    rows = [
        (430.0, 80.0, "PIHAK PERTAMA"),
        (500.0, 80.0, "Budi Santoso"),
        (514.0, 80.0, "Direktur"),
        (560.0, 80.0, "Siti Rahma"),
        (574.0, 80.0, "Karyawan"),
    ]
    page = _closing_page(rows)
    assert not employee_name_printed(page, "Budi Santoso")  # the company's title is right under it
    assert employee_name_printed(page, "Siti Rahma")


# --- the pattern check: only after the identity page confirmed the period, only on a separate signature page --------

FILE = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
YEAR_2026 = RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))
NAME_MISSING = ["PERJANJIAN KERJA WAKTU TERTENTU", "Nama : 8udl 5ant0s0", "Jabatan : Staff Gudang"]  # OCR garbled it


def _check(
    lines: list[str], regex: RegexPeriod | None, signature: PageText | None, p_ttd: int | None = 5
) -> tuple[Level, FileResult]:
    result = FileResult(file=FILE)
    result.log.periode_regex = regex
    result.log.p_id = [1]
    result.log.p_ttd = p_ttd
    level = pipeline._check_without_extractor(result, parse_filename(FILE), [page_text(1, flow(lines))], signature)
    return level, result


def test_the_signature_page_confirms_a_name_the_identity_page_lost():
    level, result = _check(NAME_MISSING, YEAR_2026, _block())
    assert level is Level.GREEN
    assert result.nama_karyawan == "Budi Santoso"
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok", "nama_halaman": "tanda_tangan"}
    assert "name_not_in_source" not in result.log.alasan


@pytest.mark.parametrize(
    "regex",
    [
        None,  # no period either: the identity page may be the wrong page
        RegexPeriod(tanggal_mulai=date(2026, 1, 1)),
        RegexPeriod(tanggal_mulai=date(2026, 1, 2), tanggal_selesai=date(2026, 12, 31)),  # close, not the same
    ],
)
def test_no_signature_page_lookup_without_a_confirmed_period(regex):
    level, result = _check(NAME_MISSING, regex, _block())
    assert level is Level.YELLOW
    assert result.log.validasi_nama_file["nama"] == "tidak_cocok"
    assert "nama_halaman" not in result.log.validasi_nama_file


def test_no_lookup_when_the_signature_page_is_an_identity_page():
    level, result = _check(NAME_MISSING, YEAR_2026, _block(), p_ttd=1)
    assert level is Level.YELLOW and result.log.validasi_nama_file["nama"] == "tidak_cocok"


def test_another_persons_signature_page_does_not_confirm_the_name():
    level, result = _check(NAME_MISSING, YEAR_2026, _block(employee="Rina Kartika"))
    assert level is Level.YELLOW
    assert "name_not_in_source" in result.log.alasan and result.nama_karyawan is None


def test_a_name_on_the_identity_page_needs_no_signature_page():
    level, result = _check(["Nama : Budi Santoso"], YEAR_2026, None)
    assert level is Level.GREEN
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}
