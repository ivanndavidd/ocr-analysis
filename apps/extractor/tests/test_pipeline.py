import csv
import json
import logging
from datetime import date

import pytest
from helpers import BODY_FILLER, make_pdf, signed_contract_pdf
from openpyxl import load_workbook
from typer.testing import CliRunner

from contract_extractor import pipeline
from contract_extractor.cli import app
from contract_extractor.config import ExtractorSettings, InputSettings, PageSelectionSettings, Settings
from contract_extractor.export import write_csv, write_xlsx
from contract_extractor.models import Decision, FileResult, PageKind, RegexPeriod
from contract_extractor.pipeline import (
    FLAG_INPUT_REJECTED,
    FLAG_PIPELINE_INCOMPLETE,
    FLAG_PROCESSING_ERROR,
    REASON_MASK_LEAK,
    REASON_PAGE_SELECTION_AMBIGUOUS,
    process_file,
    process_path,
)
from contract_extractor.stages.masking import MaskLeakError, mask_pages


def _by_file(results):
    return {r.file: r for r in results}


def test_batch_continues_past_a_broken_file_and_ignores_non_pdf(contracts_dir):
    results = _by_file(process_path(contracts_dir, Settings()))
    assert sorted(results) == [
        "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf",
        "Dewi Lestari - 1jan-31mar26.pdf",
        "Rina Wulandari - 12 bulan.pdf",
    ]

    native = results["Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"]
    assert native.log.n_halaman == 3
    assert native.log.jenis_halaman == {1: PageKind.NATIVE, 2: PageKind.NATIVE, 3: PageKind.UNCLASSIFIED}

    scan = results["Rina Wulandari - 12 bulan.pdf"]
    assert scan.log.jenis_halaman == {1: PageKind.SCAN, 2: PageKind.SCAN}
    assert REASON_PAGE_SELECTION_AMBIGUOUS in scan.log.alasan  # no OCR yet, so no text to select from

    broken = results["Dewi Lestari - 1jan-31mar26.pdf"]
    assert FLAG_PROCESSING_ERROR in broken.flags


def test_incomplete_pipeline_never_auto_passes(contracts_dir):
    for result in process_path(contracts_dir, Settings()):
        assert result.keputusan is Decision.REVIEW_PRIORITY
        if FLAG_PROCESSING_ERROR not in result.flags:
            assert FLAG_PIPELINE_INCOMPLETE in result.flags
            assert "signature_not_implemented" in result.log.alasan


def test_native_pdf_selects_identity_and_signature_pages(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())

    assert result.log.p_id == [1]
    assert result.log.p_ttd == 5
    assert REASON_PAGE_SELECTION_AMBIGUOUS not in result.log.alasan
    assert 2 in result.log.halaman_sensitif
    assert sorted(result.log.skor_halaman) == [1, 2, 3, 4, 5]
    assert result.keputusan is Decision.REVIEW_PRIORITY  # later stages still pending


def test_economy_mode_never_opens_middle_pages(tmp_path):
    pdf = make_pdf(tmp_path / "Budi Santoso - 12 bulan.pdf", [("text", BODY_FILLER)] * 10)
    result = process_file(pdf, Settings(page_selection=PageSelectionSettings(economy_mode=True)))
    assert result.log.n_halaman == 10
    assert sorted(result.log.jenis_halaman) == [1, 2, 3, 6, 7, 8, 9, 10]


def test_rejects_oversized_and_fake_pdfs(tmp_path):
    big = tmp_path / "Budi Santoso - 12 bulan.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1))
    fake = tmp_path / "Rina Wulandari - 12 bulan.pdf"
    fake.write_bytes(b"hello, not a pdf")
    settings = Settings(input=InputSettings(max_file_mb=1))

    assert process_file(big, settings).log.alasan == ["size_out_of_range"]
    rejected = process_file(fake, settings)
    assert FLAG_INPUT_REJECTED in rejected.flags
    assert rejected.log.alasan == ["not_pdf_magic"]


def test_logs_never_contain_the_employee_name(contracts_dir, caplog):
    with caplog.at_level(logging.DEBUG):
        process_path(contracts_dir, Settings())
    joined = " ".join(record.getMessage() for record in caplog.records)
    for name in ("Budi", "Rina", "Dewi"):
        assert name not in joined


@pytest.mark.tesseract
def test_cli_writes_all_three_outputs(contracts_dir, tmp_path, tesseract_cmd, monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", str(tesseract_cmd))
    out = tmp_path / "out"
    outcome = CliRunner().invoke(app, ["run", str(contracts_dir), "--out", str(out)])
    assert outcome.exit_code == 0, outcome.output
    assert "3 file diproses" in outcome.output

    payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert len(payload) == 3
    assert (out / "results.csv").exists()
    assert (out / "results.xlsx").exists()


def test_cli_refuses_to_run_without_local_ocr(contracts_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", str(tmp_path / "missing" / "tesseract.exe"))
    monkeypatch.setenv("PATH", str(tmp_path))
    outcome = CliRunner().invoke(app, ["run", str(contracts_dir), "--out", str(tmp_path / "out")])
    assert outcome.exit_code == 2
    assert "tesseract not found" in outcome.output
    assert not (tmp_path / "out").exists()


def test_scan_pages_without_ocr_are_flagged_not_guessed(contracts_dir):
    scan = _by_file(process_path(contracts_dir, Settings()))["Rina Wulandari - 12 bulan.pdf"]
    assert "ocr_unavailable" in scan.log.alasan
    assert scan.log.p_id == [] and scan.log.p_ttd is None
    assert scan.log.periode_regex is None and scan.log.pola_dimask == 0


def test_exports_neutralize_formula_injection(tmp_path):
    results = [FileResult(file="=HYPERLINK(\"http://x\")", nama_karyawan="+cmd")]

    write_csv(results, tmp_path / "r.csv")
    with (tmp_path / "r.csv").open(encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["file"].startswith("'=")
    assert row["nama_karyawan"] == "'+cmd"

    write_xlsx(results, tmp_path / "r.xlsx")
    ws = load_workbook(tmp_path / "r.xlsx").active
    assert ws["A2"].data_type == "s"
    assert ws["A2"].value == "=HYPERLINK(\"http://x\")"


class MaskSpy:
    """Wraps mask_pages to capture the payload the extractor would receive."""

    def __init__(self, monkeypatch):
        self.payloads: list[str] = []
        monkeypatch.setattr(pipeline, "mask_pages", self)

    def __call__(self, pages, max_chars_per_page):
        masked = mask_pages(pages, max_chars_per_page)
        self.payloads.append(masked.text)
        return masked


def test_native_contract_gets_regex_period_and_a_masked_p_id_payload(tmp_path, monkeypatch):
    spy = MaskSpy(monkeypatch)
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())

    assert result.log.periode_regex == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))
    assert result.log.pola_dimask == 2  # NIK digit run and the birth line
    assert "extraction_not_implemented" in result.log.alasan
    assert REASON_MASK_LEAK not in result.log.alasan

    [payload] = spy.payloads
    assert "Budi Santoso" in payload and "31 Desember 2026" in payload
    assert "3171012345670001" not in payload and "1990" not in payload
    assert "Gaji" not in payload and "7.500.000" not in payload  # page 2 never enters the payload

    # Preparing the payload sends nothing; the extractor arrives in Fase 2b.
    assert result.log.teks_keluar_karakter == 0
    assert result.log.extractor is None


def test_payload_respects_the_per_page_character_limit(tmp_path, monkeypatch):
    spy = MaskSpy(monkeypatch)
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    process_file(pdf, Settings(extractor=ExtractorSettings(max_chars_per_page=500)))
    assert len(spy.payloads[0]) == 500


def test_mask_leak_fails_closed(tmp_path, monkeypatch):
    def leaky(pages, max_chars_per_page):
        raise MaskLeakError("mask_leak")

    monkeypatch.setattr(pipeline, "mask_pages", leaky)
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())

    assert REASON_MASK_LEAK in result.log.alasan
    assert FLAG_PROCESSING_ERROR not in result.flags
    assert result.log.pola_dimask == 0
    assert result.keputusan is Decision.REVIEW_PRIORITY
