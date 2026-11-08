import csv
import json
import logging
from datetime import date

import numpy as np
import pytest
from helpers import BODY_FILLER, WORD_FOOTER, WORD_MARGIN, PageExtras, make_pdf, signed_contract_pdf
from openpyxl import load_workbook
from typer.testing import CliRunner

from contract_extractor import pipeline
from contract_extractor.cli import app
from contract_extractor.config import (
    DecisionSettings,
    ExtractorSettings,
    InputSettings,
    PageSelectionSettings,
    Settings,
)
from contract_extractor.export import write_csv, write_xlsx
from contract_extractor.extractors.base import Evidence, ExtractionResult, ExtractorError
from contract_extractor.models import Decision, FileResult, Level, PageKind, RegexPeriod, SignatureStatus
from contract_extractor.pipeline import (
    FLAG_AUTO_DISABLED,
    FLAG_FORMAT_NONSTANDARD,
    FLAG_INPUT_REJECTED,
    FLAG_PROCESSING_ERROR,
    REASON_EXTRACTOR_DISABLED,
    REASON_MASK_LEAK,
    REASON_OCR_UNAVAILABLE,
    REASON_PAGE_SELECTION_AMBIGUOUS,
    process_file,
    process_path,
)
from contract_extractor.stages.filename import parse_filename
from contract_extractor.stages.masking import MaskLeakError, mask_pages
from contract_extractor.stages.page_classify import ISSUE_PHOTO_IN_DOCUMENT, VENDOR_REQUEST


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
    assert native.log.jenis_halaman == {1: PageKind.NATIVE, 2: PageKind.NATIVE, 3: PageKind.BLANK}
    assert native.log.format_halaman == {} and native.catatan_vendor is None  # an empty page is not a format issue

    scan = results["Rina Wulandari - 12 bulan.pdf"]
    assert scan.log.jenis_halaman == {1: PageKind.SCAN, 2: PageKind.SCAN}
    assert REASON_PAGE_SELECTION_AMBIGUOUS in scan.log.alasan  # no OCR yet, so no text to select from

    broken = results["Dewi Lestari - 1jan-31mar26.pdf"]
    assert FLAG_PROCESSING_ERROR in broken.flags


def test_files_without_a_readable_signature_page_never_auto_pass(contracts_dir):
    for result in process_path(contracts_dir, Settings(decision=DecisionSettings(allow_auto=True))):
        assert result.keputusan is Decision.REVIEW_PRIORITY
        if FLAG_PROCESSING_ERROR not in result.flags:
            assert result.status_ttd is SignatureStatus.UNCERTAIN


def test_all_green_file_stays_in_review_until_auto_is_allowed(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", "annotation")

    gated = process_file(pdf, Settings(), extractor=FakeExtractor())
    assert gated.status_ttd is SignatureStatus.SIGNED
    assert (gated.keputusan, gated.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])

    allowed = process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True)), extractor=FakeExtractor())
    assert (allowed.keputusan, allowed.flags) == (Decision.AUTO, [])


def test_non_standard_page_is_read_noted_for_the_vendor_and_never_auto_passes(tmp_path):
    photo = np.full((640, 460), 200, np.uint8)
    pasted = (("layout", [(WORD_MARGIN, 760.0, WORD_FOOTER)]), PageExtras(images=[(photo, (72.0, 72.0, 468.0, 605.0))]))
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", appendix=pasted)
    result = process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True)), extractor=FakeExtractor())

    assert result.log.jenis_halaman[6] is PageKind.UNCLASSIFIED
    assert result.log.format_halaman == {6: ISSUE_PHOTO_IN_DOCUMENT}
    assert result.catatan_vendor == "Halaman 6: foto atau scan ditempel ke dokumen Word, tidak satu halaman penuh. " + VENDOR_REQUEST
    assert REASON_OCR_UNAVAILABLE in result.log.alasan  # the page goes to OCR instead of being skipped
    assert (result.log.p_id, result.log.p_ttd) == ([1], 5)
    # Every other component is green (see the all-green test above); the format alone sends it to review.
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_FORMAT_NONSTANDARD])

    write_csv([result], tmp_path / "r.csv")
    with (tmp_path / "r.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.DictReader(fh))["catatan_vendor"] == result.catatan_vendor


def test_native_pdf_selects_identity_and_signature_pages(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())

    assert result.log.p_id == [1]
    assert result.log.p_ttd == 5
    assert REASON_PAGE_SELECTION_AMBIGUOUS not in result.log.alasan
    assert 2 in result.log.halaman_sensitif
    assert sorted(result.log.skor_halaman) == [1, 2, 3, 4, 5]
    assert result.keputusan is Decision.REVIEW_PRIORITY  # extractor disabled, so extraction is red


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
    assert REASON_EXTRACTOR_DISABLED in result.log.alasan
    assert REASON_MASK_LEAK not in result.log.alasan

    [payload] = spy.payloads
    assert "Budi Santoso" in payload and "31 Desember 2026" in payload
    assert "3171012345670001" not in payload and "1990" not in payload
    assert "Gaji" not in payload and "7.500.000" not in payload  # page 2 never enters the payload

    # With the extractor disabled, preparing the payload sends nothing.
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
    extractor = FakeExtractor()
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=extractor)

    assert REASON_MASK_LEAK in result.log.alasan
    assert FLAG_PROCESSING_ERROR not in result.flags
    assert result.log.pola_dimask == 0
    assert result.keputusan is Decision.REVIEW_PRIORITY
    assert extractor.payloads == [] and result.log.teks_keluar_karakter == 0


GOOD_ANSWER = ExtractionResult(
    nama_karyawan="Budi Santoso",
    tanggal_mulai=date(2026, 1, 1),
    tanggal_selesai=date(2026, 12, 31),
    bukti=Evidence(
        nama="Nama : Budi Santoso",
        periode="mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026",
    ),
    confidence=0.95,
)


class FakeExtractor:
    name = "fake-llm"

    def __init__(self, answer: ExtractionResult = GOOD_ANSWER, error: ExtractorError | None = None):
        self.answer, self.error = answer, error
        self.payloads: list[str] = []

    def extract(self, masked_text: str) -> ExtractionResult:
        self.payloads.append(masked_text)
        if self.error:
            raise self.error
        return self.answer


def test_extractor_answer_is_verified_and_fills_the_result(tmp_path):
    extractor = FakeExtractor()
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=extractor)

    assert (result.nama_karyawan, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert result.confidence == {"nama": 0.95, "periode": 0.95, "ttd": 0.8}
    assert result.status_ttd is SignatureStatus.UNSIGNED  # no signature drawn on this contract
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}

    # Data-minimization proof: one masked P_id payload left the machine, and the log says so.
    [payload] = extractor.payloads
    assert "3171012345670001" not in payload and "Gaji" not in payload
    assert result.log.extractor == "fake-llm"
    assert result.log.teks_keluar_karakter == len(payload)
    assert result.log.komponen_halaman[1] == ["text_layer", "fake-llm:masked"]
    assert all("fake-llm:masked" not in result.log.komponen_halaman[n] for n in (2, 3, 4, 5))

    assert REASON_EXTRACTOR_DISABLED not in result.log.alasan
    # Every component is green (a clearly unsigned contract), so only the calibration gate keeps it from auto.
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])


def test_extracted_name_that_differs_from_the_filename_is_reported(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Rina Wulandari - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=FakeExtractor())
    assert result.log.validasi_nama_file == {"nama": "tidak_cocok", "periode": "cocok"}


def test_extractor_error_is_isolated_to_the_file(tmp_path):
    extractor = FakeExtractor(error=ExtractorError("api:RateLimitError"))
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=extractor)

    assert "extractor_error:api:RateLimitError" in result.log.alasan
    assert FLAG_PROCESSING_ERROR not in result.flags
    assert result.nama_karyawan is None
    assert result.log.teks_keluar_karakter > 0  # the request was sent even though it failed


def test_ambiguous_page_selection_sends_nothing(contracts_dir):
    extractor = FakeExtractor()
    results = [r for r in process_path(contracts_dir, Settings(), extractor=extractor) if r.log.n_halaman]
    assert len(results) == 2  # neither readable file has a signature page, so P_ttd is missing in both
    assert all(REASON_PAGE_SELECTION_AMBIGUOUS in r.log.alasan for r in results)
    assert extractor.payloads == []


def test_p_id_text_from_failed_ocr_is_never_sent():
    extractor = FakeExtractor()
    result = FileResult(file="Budi Santoso - 12 bulan.pdf")
    levels = pipeline._extract(result, "Nama : Budi Santoso", False, parse_filename(result.file), extractor)
    assert levels == {"extraction": Level.RED}
    assert extractor.payloads == [] and result.log.extractor is None


@pytest.mark.tesseract
def test_cli_refuses_an_enabled_extractor_that_cannot_run(contracts_dir, tmp_path, tesseract_cmd, monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", str(tesseract_cmd))
    config = tmp_path / "config.yaml"
    config.write_text("extractor:\n  enabled: true\n  provider: gemini-vertex\n", encoding="utf-8")
    outcome = CliRunner().invoke(app, ["run", str(contracts_dir), "-c", str(config), "--out", str(tmp_path / "out")])
    assert outcome.exit_code == 2
    assert "extractor.model is required" in outcome.output
    assert not (tmp_path / "out").exists()
