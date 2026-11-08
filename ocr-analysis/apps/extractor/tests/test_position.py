import dataclasses

import pytest
from helpers import EMPLOYEE_NIK, IDENTITY_LINES, PERIOD_LINES, flow, page_text, signed_contract_pdf

from contract_extractor import pipeline
from contract_extractor.common.config import Settings
from contract_extractor.common.layout import PageText
from contract_extractor.common.models import FileResult, PositionLog, PositionSource, PositionStatus
from contract_extractor.pipeline import process_file
from contract_extractor.extractor_method.pattern.position import find_position

COMPANY_LINES = IDENTITY_LINES[:6]  # the title, then `Nama`/`Jabatan : Direktur` for the company
EMPLOYEE_LINES = IDENTITY_LINES[6:]


def pages(*lines: str) -> list[PageText]:
    return [page_text(1, flow(list(lines)))]


def test_jabatan_row_in_the_employee_block_is_the_position():
    read = find_position(pages(*COMPANY_LINES, *EMPLOYEE_LINES[:2], "Jabatan : Staff Gudang", *EMPLOYEE_LINES[2:]))
    assert read.posisi == "Staff Gudang"
    assert read.log == PositionLog(status=PositionStatus.FOUND, sumber=PositionSource.LABEL)
    assert read.confidence == 1.0


def test_the_company_signatory_title_is_never_the_position():
    read = find_position(pages(*IDENTITY_LINES, *PERIOD_LINES))
    assert read.posisi is None and read.log.status is PositionStatus.MISSING


@pytest.mark.parametrize(
    "lines",
    [
        # the company block of the `hari, tanggal` opening, right above the employee's
        ["Nama : Andi Wijaya", "Jabatan : HR Manager PT Contoh Sejahtera", "Alamat : Jl. Sudirman No. 1, Jakarta",
         "Nama : Budi Santoso", "Alamat : Jl. Mawar No. 2, Bandung"],
        ["Andi Wijaya, selaku Direktur, dalam hal ini bertindak untuk dan atas nama PT Contoh Sejahtera."],
        ["diperjanjikan antara Andi Wijaya sebagai Direktur yang bertindak untuk dan atas nama PT Contoh Sejahtera"],
        ["Para Pihak sepakat dengan ketentuan sebagai berikut:", "PIHAK KEDUA wajib melaksanakan tugas sebagai berikut:"],
    ],
)
def test_lines_that_only_look_like_a_position_give_none(lines):
    assert find_position(pages(*lines)).posisi is None


@pytest.mark.parametrize(
    "line",
    [
        "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Kasir yang ditempatkan pada PT Klien Makmur.",
        "PERUSAHAAN memberikan pekerjaan kepada TENAGA KERJA sebagai Kasir, dimulai sejak tanggal 1 Januari 2026",
        "KARYAWAN wajib melaksanakan tugas sebagai Kasir dengan penuh tanggung jawab.",
        "PIHAK PERTAMA menerima PIHAK KEDUA sebagai karyawan kontrak dengan jabatan Kasir.",
    ],
)
def test_placement_sentence_gives_the_position(line):
    read = find_position(pages(line))
    assert read.posisi == "Kasir"
    assert read.log.sumber is PositionSource.SENTENCE


def test_sentence_wrapped_over_two_lines_is_read():
    assert find_position(pages("PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai", "Customer Care Agent.")).posisi == (
        "Customer Care Agent"
    )


def test_company_block_with_ocr_damaged_labels_stays_apart_from_the_employee_block():
    read = find_position(
        pages(
            "Hema : Andi Wijaya",  # OCR of `Nama`
            "Jabatan : HRD dan Hukum",
            "Alamat : Jl. Sudirman No. 1, Jakarta Selatan",
            "Dztem hal ini bertindak untuk dan atas nama PT Contoh Sejahtera, perseroan terbatas yang didirikan",
            "menurut hukum Indonesia, selanjutnya cisebut sebagai PERUSAHAAN:",
            "Hama Karyawan : Budi Santoso",
            "Tempat dan Tangzz! Lah : Bandung, 1 Januari 1990",
            f"NIK 2 {EMPLOYEE_NIK}",
            "Jenis Kelamin : Laki-laki",
        )
    )
    assert read.posisi is None and read.log.status is PositionStatus.MISSING


@pytest.mark.parametrize(
    "value, expected",
    [("Kurir |", "Kurir"), ("Store Leader :", "Store Leader"), ("HR Manager PT Contoh Sejahtera", None)],
)
def test_ocr_junk_is_trimmed_and_a_title_naming_a_company_is_refused(value, expected):
    assert find_position(pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", f"Jabatan : {value}")).posisi == expected


def test_identity_row_wrapped_address_keeps_the_block_together():
    read = find_position(
        pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", "Alamat : Jl. Mawar No. 2, RT 001/RW 002,",
              "Kelurahan Sukajadi, Kecamatan Sukasari, Kota Bandung", "Posisi : Admin QC Return")
    )
    assert read.posisi == "Admin QC Return" and read.log.sumber is PositionSource.LABEL


def test_the_row_and_the_sentence_agreeing_report_the_row():
    read = find_position(
        pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", "Jabatan : STAFF GUDANG",
              "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Staff Gudang yang ditempatkan pada PT Klien.")
    )
    assert read.posisi == "STAFF GUDANG" and read.log.sumber is PositionSource.LABEL


def test_two_different_positions_conflict_and_neither_is_reported():
    read = find_position(
        pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", "Jabatan : Kasir",
              "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Driver.")
    )
    assert read.posisi is None and read.log == PositionLog(status=PositionStatus.CONFLICT)


def test_confidence_comes_from_the_ocr_words_of_the_position():
    page = pages("PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Kasir.")[0]
    page = dataclasses.replace(page, words=tuple(dataclasses.replace(w, conf=0.7) for w in page.words))
    assert find_position([page]).confidence == 0.7


def test_pipeline_reports_the_position_but_never_logs_it(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())
    assert result.posisi is None  # the fixture names only the company's Direktur
    assert result.log.posisi == PositionLog(status=PositionStatus.MISSING)

    result = FileResult(file="x.pdf")
    pipeline._read_position(result, pages("PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Kasir."))
    assert result.posisi == "Kasir" and result.confidence["posisi"] == 1.0
    assert "Kasir" not in result.log.model_dump_json()
