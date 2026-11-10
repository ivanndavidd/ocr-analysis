import csv
import json
import logging
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from helpers import (
    BODY_FILLER,
    EMPLOYEE_NIK,
    IDENTITY_LINES,
    PERIOD_LINES,
    WORD_FOOTER,
    WORD_MARGIN,
    PageExtras,
    flow,
    llm_answer,
    make_pdf,
    page_text,
    signed_contract_pdf,
)
from openpyxl import load_workbook
from typer.testing import CliRunner

from contract_extractor import pipeline
from contract_extractor.cli import app
from contract_extractor.common.layout import PageText
from contract_extractor.document.ocr.engine import OcrOutcome, OcrRunner
from contract_extractor.common.config import (
    DecisionSettings,
    ExtractorSettings,
    InputSettings,
    OutputFormat,
    PageSelectionSettings,
    Settings,
)
from contract_extractor.output.export import write_csv, write_outputs, write_xlsx
from contract_extractor.extractor_method.llm.base import (
    ExtractionResult,
    ExtractorError,
    ImageAnswer,
    ImageTask,
    MonthsAnswer,
)
from contract_extractor.extractor_method.llm.verification import REASON_REGEX_DISAGREES
from contract_extractor.common.models import (
    Decision,
    FileResult,
    Level,
    LlmPeriod,
    NikCheck,
    NikLog,
    NikStatus,
    PageKind,
    PositionLog,
    PositionSource,
    PositionStatus,
    Reader,
    RegexPeriod,
    SignatureLog,
    SignatureStatus,
    TokenUsage,
)
from contract_extractor.pipeline import (
    FLAG_AUTO_DISABLED,
    FLAG_FORMAT_NONSTANDARD,
    FLAG_INPUT_REJECTED,
    FLAG_PROCESSING_ERROR,
    REASON_EXTRACTOR_DISABLED,
    REASON_IDENTITY_PAGE_NOT_FOUND,
    REASON_MASK_LEAK,
    REASON_OCR_UNAVAILABLE,
    REASON_SIGNATURE_PAGE_GUESSED,
    REASON_SIGNATURE_PAGE_NOT_FOUND,
    REASON_SIGNATURE_PAGE_UNREADABLE,
    process_file,
    process_path,
)
from contract_extractor.extractor_method.pattern.filename import parse_filename
from contract_extractor.extractor_method.llm.masking import MaskLeakError, mask_pages
from contract_extractor.document.pages.page_classify import ISSUE_PHOTO_IN_DOCUMENT, VENDOR_REQUEST


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
    # no OCR yet, so no text to select from
    assert {REASON_IDENTITY_PAGE_NOT_FOUND, REASON_SIGNATURE_PAGE_NOT_FOUND} <= set(scan.log.alasan)

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


def test_non_standard_page_is_noted_for_the_vendor_and_never_auto_passes(tmp_path):
    photo = np.full((640, 460), 200, np.uint8)
    pasted = (("layout", [(WORD_MARGIN, 760.0, WORD_FOOTER)]), PageExtras(images=[(photo, (72.0, 72.0, 468.0, 605.0))]))
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", appendix=pasted)
    result = process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True)), extractor=FakeExtractor())

    assert result.log.jenis_halaman[5] is PageKind.UNCLASSIFIED
    assert result.log.format_halaman == {5: ISSUE_PHOTO_IN_DOCUMENT}
    assert result.catatan_vendor == "Halaman 5: foto atau scan ditempel ke dokumen Word, tidak satu halaman penuh. " + VENDOR_REQUEST
    assert REASON_OCR_UNAVAILABLE not in result.log.alasan  # a middle page is noted for the vendor, never read
    assert (result.log.p_id, result.log.p_ttd) == ([1], 6)
    # Every other component is green (see the all-green test above); the format alone sends it to review.
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_FORMAT_NONSTANDARD])

    write_csv([result], tmp_path / "r.csv")
    with (tmp_path / "r.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.DictReader(fh))["catatan_vendor"] == result.catatan_vendor


def test_a_first_page_photographed_sideways_is_turned_and_read_again(tmp_path):
    photo = np.full((792, 612), 255, np.uint8)
    photo[300:500, :40] = 0  # a dark strip on the left edge: the top of the page once it is turned clockwise
    last = np.full((792, 612), 255, np.uint8)
    last[390:400, 300:310] = 0  # a dot in the middle: no turn brings anything to the top
    pdf = make_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", [("image", photo), ("image", last)])
    identity = page_text(1, flow(IDENTITY_LINES + PERIOD_LINES)).words

    class ReadsOnlyUpright:
        name = "fake"

        def read(self, image: np.ndarray) -> list:
            return list(identity) if (image[: image.shape[0] // 8] < 128).any() else []

    result = process_file(pdf, Settings(), OcrRunner([ReadsOnlyUpright()]))
    assert result.log.putaran_halaman == {1: 90}  # the blank last page reads nothing in any turn and is never turned
    assert result.log.komponen_halaman[1] == ["fake@300dpi", "fake@300dpi"]  # upright, then turned
    assert (result.log.p_id, result.nik) == ([1], EMPLOYEE_NIK)


def test_a_pasted_photo_as_the_last_page_goes_to_ocr(tmp_path):
    photo = np.full((640, 460), 200, np.uint8)
    pages = [("text", "\n".join(IDENTITY_LINES + PERIOD_LINES)), ("layout", [(WORD_MARGIN, 760.0, WORD_FOOTER)])]
    extras = {1: PageExtras(images=[(photo, (72.0, 72.0, 468.0, 605.0))])}
    pdf = make_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", pages, extras)
    result = process_file(pdf, Settings())
    assert result.log.jenis_halaman[2] is PageKind.UNCLASSIFIED
    assert REASON_OCR_UNAVAILABLE in result.log.alasan  # read by OCR instead of skipped; no OCR is configured here


def test_native_pdf_selects_identity_and_signature_pages(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings())

    assert result.log.p_id == [1]
    assert result.log.p_ttd == 5
    assert REASON_IDENTITY_PAGE_NOT_FOUND not in result.log.alasan
    assert REASON_SIGNATURE_PAGE_NOT_FOUND not in result.log.alasan
    assert result.log.halaman_sensitif == [1]  # the salary page 2 is never read
    assert sorted(result.log.skor_halaman) == sorted(result.log.komponen_halaman) == [1, 5]  # nor any middle page
    # Extractor off: the filename's name and period are confirmed by the document text, so every component is green
    # and only `allow_auto: false` holds the file back.
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])
    assert (result.nama_karyawan, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True))).keputusan is Decision.AUTO


def test_a_missing_identity_page_is_named_on_its_own(tmp_path, monkeypatch):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    select = pipeline.select_pages
    monkeypatch.setattr(pipeline, "select_pages", lambda *args: replace(select(*args), p_id=[]))
    result = process_file(pdf, Settings())

    assert REASON_IDENTITY_PAGE_NOT_FOUND in result.log.alasan
    assert REASON_SIGNATURE_PAGE_NOT_FOUND not in result.log.alasan  # page 5 was found
    assert REASON_SIGNATURE_PAGE_UNREADABLE not in result.log.alasan  # the missing identity page explains it
    assert result.keputusan is Decision.REVIEW_PRIORITY


def test_economy_mode_never_opens_middle_pages(tmp_path):
    pdf = make_pdf(tmp_path / "Budi Santoso - 12 bulan.pdf", [("text", BODY_FILLER)] * 10)
    result = process_file(pdf, Settings(page_selection=PageSelectionSettings(economy_mode=True)))
    assert result.log.n_halaman == 10
    assert sorted(result.log.jenis_halaman) == [1, 10]


@pytest.mark.parametrize("economy_mode", [False, True])
def test_an_empty_last_page_is_skipped(tmp_path, economy_mode):
    # A Word export can end on an empty page; the signature page is the last page with something on it.
    pages = [("text", "\n".join(IDENTITY_LINES + PERIOD_LINES)), ("text", BODY_FILLER), ("blank", ""), ("blank", "")]
    pdf = make_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", pages)
    result = process_file(pdf, Settings(page_selection=PageSelectionSettings(economy_mode=economy_mode)))
    assert (result.log.p_id, result.log.p_ttd) == ([1], 2)
    assert result.log.jenis_halaman[4] is PageKind.BLANK


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


@pytest.mark.parametrize("full_nik, shown", [(False, "************0001"), (True, EMPLOYEE_NIK)])
def test_exports_mask_the_nik_unless_full_is_asked_for(tmp_path, full_nik, shown):
    results = [FileResult(file="a.pdf", nik=EMPLOYEE_NIK), FileResult(file="b.pdf")]
    written = write_outputs(results, tmp_path, list(OutputFormat), full_nik)

    assert [r["nik"] for r in json.loads(written[0].read_text(encoding="utf-8"))] == [shown, None]
    with written[1].open(encoding="utf-8-sig", newline="") as fh:
        assert [row["nik"] for row in csv.DictReader(fh)] == [shown, ""]
    ws = load_workbook(written[2]).active
    column = [cell.value for cell in ws[1]].index("nik") + 1
    assert [ws.cell(row=r, column=column).value for r in (2, 3)] == [shown, None]
    # json and csv are plain text (xlsx is zipped): the full number must not be anywhere in them
    assert full_nik or all(EMPLOYEE_NIK not in path.read_text(encoding="utf-8-sig") for path in written[:2])


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


GOOD_ANSWER = llm_answer()


class FakeExtractor:
    name = "fake-llm"

    def __init__(
        self,
        answer: ExtractionResult = GOOD_ANSWER,
        error: ExtractorError | None = None,
        images: dict[ImageTask, ImageAnswer | ExtractorError] | None = None,
    ):
        self.answer, self.error, self.images = answer, error, images or {}
        self.payloads: list[str] = []
        self.crops: list[tuple[ImageTask, bytes]] = []

    def extract(self, masked_text: str) -> ExtractionResult:
        self.payloads.append(masked_text)
        if self.error:
            raise self.error
        return self.answer

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        self.crops.append((task, png))
        reply = self.images.get(task, ExtractorError("image_unsupported"))
        if isinstance(reply, ExtractorError):
            raise reply
        return reply


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
    assert result.confidence == {"nama": 0.95, "periode": 0.95, "ttd": 0.8, "nik": 1.0}
    assert result.status_ttd is SignatureStatus.UNSIGNED  # no signature drawn on this contract
    # The NIK is read locally from the text layer and agrees with the printed birth date and gender.
    assert result.nik == EMPLOYEE_NIK
    assert result.log.nik == NikLog(status=NikStatus.FOUND, tanggal_lahir=NikCheck.MATCH, jenis_kelamin=NikCheck.MATCH)
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}

    # Data-minimization proof: one masked P_id payload left the machine, and the log says so.
    [payload] = extractor.payloads
    assert EMPLOYEE_NIK not in payload and EMPLOYEE_NIK[-4:] not in payload and "Gaji" not in payload
    assert result.log.extractor == "fake-llm"
    assert result.log.teks_keluar_karakter == len(payload)
    assert result.log.komponen_halaman[1] == ["text_layer", "fake-llm:masked"]
    assert all("fake-llm:masked" not in result.log.komponen_halaman.get(n, []) for n in (2, 3, 4, 5))

    assert REASON_EXTRACTOR_DISABLED not in result.log.alasan
    # Every component is green (a clearly unsigned contract), so only the calibration gate keeps it from auto.
    assert (result.keputusan, result.flags) == (Decision.REVIEW, [FLAG_AUTO_DISABLED])


def test_extracted_name_that_differs_from_the_filename_is_reported(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Rina Wulandari - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=FakeExtractor())
    assert result.log.validasi_nama_file == {"nama": "tidak_cocok", "periode": "cocok"}


def test_the_tokens_each_extractor_call_used_are_logged(tmp_path):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    used = TokenUsage(masuk=40, cache_baca=2500, keluar=900)
    answer = GOOD_ANSWER.model_copy(update={"usage": used})
    assert process_file(pdf, Settings(), extractor=FakeExtractor(answer)).log.token_llm == used
    failed = FakeExtractor(error=ExtractorError("truncated", TokenUsage(masuk=40, keluar=64000)))
    assert process_file(pdf, Settings(), extractor=failed).log.token_llm == TokenUsage(masuk=40, keluar=64000)
    assert process_file(pdf, Settings()).log.token_llm is None  # nothing sent


def test_extractor_error_is_isolated_to_the_file(tmp_path):
    extractor = FakeExtractor(error=ExtractorError("api:RateLimitError"))
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(), extractor=extractor)

    assert "extractor_error:api:RateLimitError" in result.log.alasan
    assert FLAG_PROCESSING_ERROR not in result.flags
    assert result.log.teks_keluar_karakter > 0  # the request was sent even though it failed
    # The file is still read the local way, as if the extractor were off.
    assert result.log.pembaca is Reader.PATTERN
    assert (result.nama_karyawan, result.tanggal_mulai) == ("Budi Santoso", date(2026, 1, 1))


def test_only_a_missing_identity_page_keeps_the_text_from_being_read(contracts_dir):
    extractor = FakeExtractor()
    results = _by_file(process_path(contracts_dir, Settings(), extractor=extractor))
    # Neither readable file has a signature page; only Budi's has an identity page with text. Budi's last page is
    # empty, so the page before it is checked as a guess.
    budi, rina = results["Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"], results["Rina Wulandari - 12 bulan.pdf"]
    assert REASON_SIGNATURE_PAGE_GUESSED in budi.log.alasan and (budi.log.p_id, budi.log.p_ttd) == ([1], 2)
    assert REASON_IDENTITY_PAGE_NOT_FOUND in rina.log.alasan
    assert len(extractor.payloads) == 1 and "Budi Santoso" in extractor.payloads[0]  # masked, like any identity page
    assert (budi.nama_karyawan, rina.nama_karyawan) == ("Budi Santoso", None)


@pytest.mark.parametrize("guess", [5, None])
def test_a_missing_signature_page_still_reads_the_identity_page_and_checks_the_guess(tmp_path, monkeypatch, guess):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", "annotation")
    select = pipeline.select_pages
    lost = lambda *args: replace(select(*args), p_ttd=None, ttd_guess=guess, level=Level.RED)  # noqa: E731
    monkeypatch.setattr(pipeline, "select_pages", lost)
    result = process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True)))

    assert (result.nama_karyawan, result.tanggal_mulai, result.nik is not None) == ("Budi Santoso", date(2026, 1, 1), True)
    assert result.log.p_ttd == guess
    assert result.keputusan is Decision.REVIEW_PRIORITY  # a guessed page never lets the file pass on its own
    if guess:
        assert REASON_SIGNATURE_PAGE_GUESSED in result.log.alasan
        assert result.status_ttd is SignatureStatus.SIGNED  # read from the guessed page
    else:
        assert REASON_SIGNATURE_PAGE_NOT_FOUND in result.log.alasan
        assert REASON_SIGNATURE_PAGE_UNREADABLE not in result.log.alasan  # the missing page explains it
        assert result.status_ttd is SignatureStatus.UNCERTAIN


def test_p_id_text_from_failed_ocr_is_never_sent():
    extractor = FakeExtractor()
    result = FileResult(file="Budi Santoso - 12 bulan.pdf")
    pipeline._extract(result, "Nama : Budi Santoso", False, parse_filename(result.file), extractor, [])
    assert extractor.payloads == [] and result.log.extractor is None
    assert pipeline.REASON_EXTRACTOR_NOT_SENT in result.log.alasan
    assert result.log.pembaca is Reader.PATTERN  # read the local way instead


def _llm_extract(answer, local_position=None, local_status=PositionStatus.MISSING, regex=None):
    """Run the cloud part of Tahap 3 on a one-page source with a fake answer."""
    lines = ["Nama : Budi Santoso", "NIK : [REDACTED-ID]", "bekerja sebagai Staff Gudang di Cikarang",
             "berlaku mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026"]
    result = FileResult(file=FILE, posisi=local_position)
    result.log.posisi = PositionLog(status=local_status, sumber=PositionSource.LABEL if local_position else None)
    result.log.periode_regex = regex
    result.log.p_id = [1]
    levels = pipeline._extract(result, "\n".join(lines), True, parse_filename(FILE), FakeExtractor(answer), [])
    return levels, result


def test_the_extractor_reading_is_used_and_recorded():
    levels, result = _llm_extract(llm_answer(), regex=YEAR_2026)
    assert result.log.pembaca is Reader.LLM
    assert result.log.periode_llm == LlmPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))
    assert levels == {"validasi_nama_file": Level.GREEN, "extraction": Level.GREEN}


def test_a_period_the_filename_confirms_is_green_when_the_regex_read_nothing():
    levels, result = _llm_extract(llm_answer(), regex=RegexPeriod())
    assert levels["extraction"] is Level.GREEN
    assert REASON_REGEX_DISAGREES in result.log.alasan  # still recorded


def test_the_extractor_position_is_added_or_compared_with_the_local_reading():
    title = dict(position="Staff Gudang", position_quote="bekerja sebagai Staff Gudang di Cikarang")
    _, added = _llm_extract(llm_answer(**title))
    assert (added.posisi, added.log.posisi.sumber, added.log.posisi.cocok_llm) == ("Staff Gudang", PositionSource.LLM, None)
    assert added.confidence["posisi"] == 0.95

    _, same = _llm_extract(llm_answer(**title), local_position="STAFF GUDANG")
    assert (same.posisi, same.log.posisi.sumber, same.log.posisi.cocok_llm) == ("Staff Gudang", PositionSource.LABEL, True)

    _, other = _llm_extract(llm_answer(**title), local_position="Kasir")
    assert (other.posisi, other.log.posisi.cocok_llm) == ("Staff Gudang", False)

    _, two = _llm_extract(llm_answer(**title), local_status=PositionStatus.CONFLICT)
    assert (two.posisi, two.log.posisi.status, two.log.posisi.cocok_llm) == ("Staff Gudang", PositionStatus.FOUND, False)

    _, kept = _llm_extract(llm_answer(), local_position="Kasir")
    assert (kept.posisi, kept.log.posisi.cocok_llm) == ("Kasir", None)  # no title from the extractor: local stays


@pytest.mark.tesseract
def test_cli_refuses_an_enabled_extractor_that_cannot_run(contracts_dir, tmp_path, tesseract_cmd, monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", str(tesseract_cmd))
    config = tmp_path / "config.yaml"
    config.write_text("extractor:\n  enabled: true\n  provider: gemini-vertex\n", encoding="utf-8")
    outcome = CliRunner().invoke(app, ["run", str(contracts_dir), "-c", str(config), "--out", str(tmp_path / "out")])
    assert outcome.exit_code == 2
    assert "extractor.model is required" in outcome.output
    assert not (tmp_path / "out").exists()


def _pattern_check(filename: str, lines: list[str], regex: RegexPeriod | None) -> tuple[Level, FileResult]:
    result = FileResult(file=filename)
    result.log.periode_regex = regex
    page = page_text(1, flow(lines))
    return pipeline._check_without_extractor(result, parse_filename(filename), [page]), result


FILE = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
YEAR_2026 = RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))


def test_pattern_mode_passes_when_the_document_confirms_the_filename():
    level, result = _pattern_check(FILE, ["Nama : BUDI SANTOSO", "NIK : 3199990101900001"], YEAR_2026)
    assert level is Level.GREEN
    assert (result.nama_karyawan, result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (
        "Budi Santoso",
        date(2026, 1, 1),
        date(2026, 12, 31),
        12,
    )
    assert result.log.alasan == [] and result.flags == []
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}  # the website shows both


def test_pattern_mode_does_not_find_a_short_name_inside_a_word():
    # `name_in_text` found `Ian` in `PERJANJIAN` and passed the name green.
    level, result = _pattern_check(
        "Ian - 1 Januari 2026 - 31 Desember 2026.pdf", ["PERJANJIAN KERJA WAKTU TERTENTU", "Nama : Andi Wijaya"], YEAR_2026
    )
    assert level is Level.YELLOW
    assert result.log.validasi_nama_file["nama"] == "tidak_cocok"
    assert result.nama_karyawan is None


@pytest.mark.parametrize(
    "filename, lines, regex, level, reason",
    [
        (FILE, ["Nama : Andi Wijaya"], YEAR_2026, Level.YELLOW, "name_not_in_source"),
        (FILE, ["Nama : Budi Santoso"], RegexPeriod(tanggal_mulai=date(2026, 1, 1)), Level.YELLOW, "period_not_in_source"),
        (FILE, ["Nama : Budi Santoso"], None, Level.YELLOW, "period_not_in_source"),
        (FILE, ["Nama : Budi Santoso"], RegexPeriod(ambigu=True), Level.RED, "period_conflict"),
        (
            FILE,
            ["Nama : Budi Santoso"],
            RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 6, 30)),
            Level.RED,
            "filename_mismatch",
        ),
        (
            FILE,
            ["Nama : Budi Santoso"],
            RegexPeriod(tanggal_mulai=date(2026, 1, 2), tanggal_selesai=date(2026, 12, 31)),
            Level.YELLOW,
            None,
        ),
        ("Budi Santoso.pdf", ["Nama : Budi Santoso"], YEAR_2026, Level.YELLOW, None),
    ],
)
def test_pattern_mode_never_passes_what_the_document_does_not_confirm(filename, lines, regex, level, reason):
    got, result = _pattern_check(filename, lines, regex)
    assert got is level
    if reason == "name_not_in_source":
        assert result.log.validasi_nama_file["nama"] == "tidak_cocok"
    if reason:
        assert reason in result.log.alasan + result.flags
    assert result.nama_karyawan is None and result.tanggal_mulai is None  # nothing unconfirmed is reported


@pytest.mark.parametrize(
    "extraction, anchor, level, reason",
    [
        (Level.GREEN, "nama", Level.GREEN, False),  # both pages proved right by their content
        (Level.YELLOW, "nama", Level.YELLOW, True),
        (Level.GREEN, "sebutan", Level.YELLOW, True),
        (Level.GREEN, None, Level.YELLOW, True),
    ],
)
def test_a_close_page_score_is_lifted_only_when_both_pages_are_confirmed(extraction, anchor, level, reason):
    result = FileResult(file="x.pdf")
    result.log.tanda_tangan = SignatureLog(jangkar=anchor)
    levels = {"page_selection": Level.YELLOW, "extraction": extraction}
    pipeline._confirm_page_selection(result, levels)
    assert levels["page_selection"] is level
    assert (pipeline.REASON_PAGE_SELECTION_LOW_MARGIN in result.log.alasan) is reason


def test_a_red_page_selection_is_never_lifted():
    result = FileResult(file="x.pdf")
    result.log.tanda_tangan = SignatureLog(jangkar="nama")
    levels = {"page_selection": Level.RED, "extraction": Level.GREEN}
    pipeline._confirm_page_selection(result, levels)
    assert levels["page_selection"] is Level.RED and result.log.alasan == []


def test_a_signature_without_a_clear_margin_says_why_it_needs_a_look(tmp_path, monkeypatch):
    from contract_extractor.signature.signature import SignatureResult

    weak = SignatureResult(SignatureStatus.SIGNED, "image", "sebutan", False, 0.8)
    monkeypatch.setattr(pipeline, "detect_signature", lambda *args, **kwargs: weak)
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pdf, Settings(decision=DecisionSettings(allow_auto=True)))
    assert result.keputusan is Decision.REVIEW
    assert pipeline.REASON_SIGNATURE_NOT_CLEAR in result.log.alasan


@pytest.mark.parametrize(
    "lines, regex, level, reason",
    [
        # the regex read no clear period, but both of the filename's dates are printed: confirmed
        (["Nama : Budi Santoso", "Kontrak ini berlaku 01/01/2026 hingga 31/12/2026."], RegexPeriod(), Level.GREEN, None),
        (["Nama : Budi Santoso", "Kontrak ini berlaku 01/01/2026 hingga 31/12/2026."], None, Level.GREEN, None),
        # one date is not enough
        (["Nama : Budi Santoso", "Kontrak ini berlaku sejak 1 Januari 2026."], None, Level.YELLOW, "period_not_in_source"),
        # the regex read another start: it vetoes, even though the filename's dates are printed too
        (
            ["Nama : Budi Santoso", "ditandatangani 1 Januari 2026; berlaku 2 Januari 2026 s/d 31 Desember 2026"],
            RegexPeriod(tanggal_mulai=date(2026, 1, 2)),
            Level.YELLOW,
            "period_not_in_source",
        ),
        (
            ["Nama : Budi Santoso", "berlaku 1 Januari 2026 s/d 31 Desember 2026"],
            RegexPeriod(ambigu=True),
            Level.RED,
            "period_conflict",
        ),
    ],
)
def test_pattern_mode_looks_up_the_filename_dates_where_the_regex_read_no_period(lines, regex, level, reason):
    got, result = _pattern_check(FILE, lines, regex)
    assert got is level
    if level is Level.GREEN:
        assert (result.tanggal_mulai, result.tanggal_selesai, result.durasi_bulan) == (YEAR_2026.tanggal_mulai, YEAR_2026.tanggal_selesai, 12)
        assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}
    else:
        assert reason in result.log.alasan
        assert "periode" not in result.log.validasi_nama_file and result.tanggal_mulai is None


def _vendor_check(vendor, id_lines, ttd_lines=None, read=None):
    result = FileResult(file=FILE, vendor=vendor, vendor_dokumen=read)
    p_ttd = page_text(2, flow(ttd_lines)) if ttd_lines else None
    pages = pipeline._PagesRead({}, [page_text(1, flow(id_lines))] if id_lines else [], True, p_ttd)
    return pipeline._check_vendor(result, pages), result


@pytest.mark.parametrize(
    "vendor, id_lines, ttd_lines, read, level, check, reason",
    [
        ("PT. Sarana Perkasa", ["atas nama PT SARANA PERKASA"], None, None, Level.GREEN, "cocok", None),
        ("PT Bina Semesta", ["Nama : Budi Santoso"], ["PT BINA SEMESTA", "Hendra Wijaya"], None, Level.GREEN, "cocok", None),
        ("PT. Sarana Perkasa", ["atas nama PT Sarana Ekasarana"], None, None, Level.YELLOW, "tidak_cocok", "vendor_not_in_source"),
        # the folder's name printed on the page decides first, whatever employer the extractor read
        ("PT Bina Semesta", ["atas nama PT Bina Semesta"], None, "PT Sarana Perkasa", Level.GREEN, "cocok", None),
        # not printed: the extractor's employer company decides
        ("PT Bina Semesta", ["atas nama BSI"], None, "PT. Bina Semesta Indonesia", Level.GREEN, "cocok", None),
        ("PT Bina Semesta", ["atas nama BSI"], None, "PT Sarana Perkasa", Level.YELLOW, "tidak_cocok", "vendor_mismatch"),
        # nothing to check: no vendor folder, or no page was read
        (None, ["atas nama PT Bina Semesta"], None, None, None, None, None),
        ("PT Bina Semesta", [], None, None, None, None, None),
    ],
)
def test_the_vendor_folder_is_checked_against_the_document(vendor, id_lines, ttd_lines, read, level, check, reason):
    got, result = _vendor_check(vendor, id_lines, ttd_lines, read)
    assert got is level
    assert result.log.validasi_vendor == check
    assert result.log.alasan == ([reason] if reason else [])
    if check is not None:
        printed = read is None or "BSI" not in id_lines[0]
        assert result.log.vendor_dari == ("teks" if printed else "llm")


def test_the_vendor_folder_reaches_the_result_and_its_check(tmp_path):
    folder = tmp_path / "PT Contoh Sejahtera" / "Januari 2026 - Desember 2026"
    folder.mkdir(parents=True)
    signed_contract_pdf(folder / FILE)
    [checked] = process_path(tmp_path, Settings(), recursive=True, vendor_folders=True)
    [unchecked] = process_path(tmp_path, Settings(), recursive=True)
    assert checked.vendor == "PT Contoh Sejahtera" and checked.log.validasi_vendor is not None
    assert unchecked.vendor is None and unchecked.log.validasi_vendor is None


FALLBACK_LINES = [
    "Nama : Budi Santoso",
    "bekerja sebagai Staff Gudang di Cikarang",
    "Perjanjian ini berlaku terhitung sejak 1 Januari 2026 untuk 12 (dua belas) bulan",
]


def _fallback(lines, regex=None, answer=None, local_position=None):
    """The website's flow (patterns first, the extractor assisting), on a one-page source with a fake answer."""
    result = FileResult(file=FILE, posisi=local_position)
    result.log.posisi = PositionLog(status=PositionStatus.FOUND if local_position else PositionStatus.MISSING)
    result.log.periode_regex = regex
    result.log.p_id = [1]
    extractor = FakeExtractor(answer or llm_answer())
    page = page_text(1, flow(lines))
    levels = pipeline._extract(result, "\n".join(lines), True, parse_filename(FILE), extractor, [page], assist=True)
    return levels, result, extractor


def test_the_patterns_keep_a_name_and_period_they_settle():
    confirmed = ["Nama : Budi Santoso", "berlaku mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026"]
    levels, result, extractor = _fallback(confirmed, YEAR_2026)
    assert levels == {"extraction": Level.GREEN} and len(extractor.payloads) == 1  # sent for the position all the same
    assert (result.log.pembaca, result.log.llm_cadangan, result.log.extractor) == (Reader.PATTERN, False, "fake-llm")
    assert result.nama_karyawan == "Budi Santoso" and result.log.panggilan_llm == ["teks"]

    differs = RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 6, 30))
    levels, result, extractor = _fallback(confirmed, differs)  # the document states another period: a person decides
    assert levels == {"extraction": Level.RED} and result.log.llm_cadangan is False


def test_the_fallback_reads_what_the_patterns_could_not_confirm():
    quote = "berlaku terhitung sejak 1 Januari 2026 untuk 12 (dua belas) bulan"
    answer = llm_answer(end=None, period_quote=quote, position="Staff Gudang",
                        position_quote="bekerja sebagai Staff Gudang di Cikarang")
    answer.durasi_bulan = MonthsAnswer(kutipan=quote, nilai=12, confidence=0.95)
    levels, result, extractor = _fallback(FALLBACK_LINES, answer=answer, local_position="Kasir")

    assert len(extractor.payloads) == 1
    assert (result.log.pembaca, result.log.llm_cadangan) == (Reader.LLM, True)
    assert levels["extraction"] is Level.GREEN  # the written length gives the end the filename names
    assert (result.tanggal_mulai, result.tanggal_selesai) == (date(2026, 1, 1), date(2026, 12, 31))
    assert "period_not_in_source" not in result.log.alasan  # the patterns' reason went with their answer
    # The extractor's verified title wins; the local reading stays as a check.
    assert (result.posisi, result.log.posisi.cocok_llm) == ("Staff Gudang", False)


def test_a_failed_fallback_leaves_the_patterns_answer():
    result = FileResult(file=FILE)
    page = page_text(1, flow(FALLBACK_LINES))
    extractor = FakeExtractor(error=ExtractorError("api:RateLimitError"))
    levels = pipeline._extract(result, "x", True, parse_filename(FILE), extractor, [page], assist=True)
    assert levels == {"extraction": Level.YELLOW} and result.log.pembaca is Reader.PATTERN
    assert "period_not_in_source" in result.log.alasan and "extractor_error:api:RateLimitError" in result.log.alasan
    assert result.log.panggilan_llm == ["teks"] and result.log.token_llm is None


USED = TokenUsage(masuk=10, cache_tulis=100, cache_baca=1000, keluar=50)


def test_every_call_adds_to_the_files_token_total():
    result, extractor = FileResult(file=FILE), FakeExtractor()
    pipeline._count_call(result, extractor, "teks", USED)
    pipeline._count_call(result, extractor, "nik", None)  # a provider that reported nothing
    pipeline._count_call(result, extractor, "ttd", TokenUsage(masuk=1, keluar=2))
    assert result.log.panggilan_llm == ["teks", "nik", "ttd"]
    assert result.log.token_panggilan == [USED, None, TokenUsage(masuk=1, keluar=2)]
    assert result.log.token_llm == TokenUsage(masuk=11, cache_tulis=100, cache_baca=1000, keluar=52)


def test_a_crop_is_cut_from_its_box_and_never_sent_larger_than_the_cap():
    image = np.zeros((4000, 3000), np.uint8)
    small = cv2.imdecode(np.frombuffer(pipeline._png(image, (0.1, 0.1, 0.2, 0.15)), np.uint8), cv2.IMREAD_UNCHANGED)
    assert small.shape == (200, 300)
    large = cv2.imdecode(np.frombuffer(pipeline._png(image, (0.0, 0.0, 1.0, 1.0)), np.uint8), cv2.IMREAD_UNCHANGED)
    assert max(large.shape) == pipeline.CROP_MAX_SIDE


def _signature_crop(status, answer):
    result = FileResult(file=FILE, status_ttd=status)
    result.log.tanda_tangan = SignatureLog()
    extractor = FakeExtractor(images={ImageTask.SIGNATURE: answer})
    image = np.full((3508, 2480), 255, np.uint8)
    decided = pipeline._signature_crop(result, extractor, image, (0.5, 0.6, 0.8, 0.7), 5)
    return decided, result, extractor


@pytest.mark.parametrize(
    "local, status, confidence, decided",
    [
        (SignatureStatus.UNCERTAIN, "signed", 0.95, SignatureStatus.SIGNED),
        (SignatureStatus.UNCERTAIN, "signed", 0.85, SignatureStatus.SIGNED),
        (SignatureStatus.UNCERTAIN, "signed", 0.75, None),  # not sure enough to call it signed
        (SignatureStatus.UNCERTAIN, "unsigned", 0.85, SignatureStatus.UNSIGNED),
        (SignatureStatus.UNCERTAIN, "unsigned", 0.8, None),  # the one wrong "unsigned" answer came at 0.80
        (SignatureStatus.UNCERTAIN, "unclear", 0.99, None),
        (SignatureStatus.UNSIGNED, "signed", 0.95, SignatureStatus.UNCERTAIN),  # the two readers disagree
        (SignatureStatus.UNSIGNED, "unsigned", 0.95, None),
        # a scan or photo read signed: any "unsigned" sends it to a person, since a wrong "signed" lets it through
        (SignatureStatus.SIGNED, "unsigned", 0.95, SignatureStatus.UNCERTAIN),
        (SignatureStatus.SIGNED, "unsigned", 0.6, SignatureStatus.UNCERTAIN),
        (SignatureStatus.SIGNED, "signed", 0.95, None),
        (SignatureStatus.SIGNED, "unclear", 0.9, None),
    ],
)
def test_the_extractor_decides_an_unsure_signature_only_when_sure(local, status, confidence, decided):
    got, result, extractor = _signature_crop(local, ImageAnswer(status=status, confidence=confidence, usage=USED))
    assert got is decided
    assert (result.log.tanda_tangan.llm, result.log.tanda_tangan.llm_conf) == (status, confidence)
    assert result.log.komponen_halaman == {5: ["fake-llm:potongan_ttd"]}
    assert result.log.panggilan_llm == ["ttd"] and result.log.token_llm == USED
    [(task, png)] = extractor.crops
    crop = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert task is ImageTask.SIGNATURE and crop.shape[0] < 3508 * 0.2  # the area, never the page


def test_a_failed_signature_crop_keeps_the_local_status():
    got, result, _ = _signature_crop(SignatureStatus.UNCERTAIN, ExtractorError("refusal", USED))
    assert got is None and "extractor_error:refusal" in result.log.alasan and result.log.token_llm == USED


def test_an_unsigned_text_layer_contract_is_shown_to_the_extractor(tmp_path):
    """A text-layer PDF that reads unsigned may carry an e-signature the ink check cannot see."""
    path = signed_contract_pdf(tmp_path / FILE)
    settings = Settings(extractor=ExtractorSettings(enabled=True, assist=True))
    signed = ImageAnswer(status="signed", confidence=0.97)
    extractor = FakeExtractor(images={ImageTask.SIGNATURE: signed})
    result = process_file(path, settings, extractor=extractor)
    assert result.status_ttd is SignatureStatus.UNCERTAIN and "signature_llm_differs" in result.log.alasan
    assert [task for task, _ in extractor.crops] == [ImageTask.SIGNATURE]  # its NIK is read from the text layer
    assert result.log.panggilan_llm == ["teks", "ttd"]

    plain = process_file(path, Settings(extractor=ExtractorSettings(enabled=True)), extractor=FakeExtractor())
    assert plain.status_ttd is SignatureStatus.UNSIGNED and plain.log.panggilan_llm == ["teks"]  # CLI mode: text only


# --- an identity page read again with its table borders erased ---------------------------------------------------------

POSITION_LINE = "PIHAK PERTAMA mempekerjakan PIHAK KEDUA sebagai Admin Gudang."


def _ocr_identity(*dropped: str) -> PageText:
    """The identity page as OCR gives it, without the lines that start with one of `dropped`."""
    lines = [line for line in IDENTITY_LINES + PERIOD_LINES + [POSITION_LINE] if not line.startswith(dropped or ("\0",))]
    return replace(page_text(1, flow(lines)), source="ocr:tesseract")


class _ErasingRunner:
    """OcrRunner stand-in whose erased read gives `erased`; None stands for a read that fails the quality check."""

    primary = SimpleNamespace(name="tesseract")
    read_dpi = 300

    def __init__(self, erased: PageText | None) -> None:
        self.erased, self.calls = erased, []

    def read_erased(self, renderer, number, quality_name=""):
        self.calls.append(number)
        return OcrOutcome(self.erased, "tesseract", 0.9, self.erased is not None, ["tesseract+rules_erased"])


@pytest.mark.parametrize(
    "plain, erased, adopted",
    [
        (_ocr_identity("NIK"), _ocr_identity(), True),  # the hidden NIK row comes back
        (_ocr_identity("PIHAK PERTAMA mempekerjakan"), _ocr_identity(), True),  # the position comes back
        (_ocr_identity("NIK"), _ocr_identity("Perjanjian ini berlaku"), False),  # but the period would go
        (_ocr_identity("NIK", "PIHAK PERTAMA mempekerjakan"), _ocr_identity("NIK"), True),  # one of the two is enough
        (_ocr_identity("NIK"), _ocr_identity("NIK"), False),  # nothing comes back
        (_ocr_identity("NIK"), None, False),  # the erased read fails the quality check
    ],
)
def test_an_identity_page_missing_a_value_is_read_again_with_its_borders_erased(plain, erased, adopted):
    pages, result = {1: plain}, FileResult(file="Budi Santoso - 12 bulan.pdf")
    pipeline._reveal_table_rows(None, _ErasingRunner(erased), [1], [1], pages, "", result)
    assert pages[1] is (erased if adopted else plain)
    assert ("tesseract+rules_erased@300dpi" in result.log.komponen_halaman.get(1, [])) is adopted


def test_an_identity_page_that_gives_the_nik_and_the_position_is_not_read_again():
    runner = _ErasingRunner(_ocr_identity())
    pipeline._reveal_table_rows(None, runner, [1], [1], {1: _ocr_identity()}, "", FileResult(file="x.pdf"))
    assert runner.calls == []


def test_a_nik_the_printed_birth_date_contradicts_is_not_a_reason_to_swap_the_page():
    lines = [line.replace("1 Januari 1990", "2 Februari 1991") for line in IDENTITY_LINES + PERIOD_LINES + [POSITION_LINE]]
    contradicted = replace(page_text(1, flow(lines)), source="ocr:tesseract")
    plain = _ocr_identity("NIK")
    pages = {1: plain}
    pipeline._reveal_table_rows(None, _ErasingRunner(contradicted), [1], [1], pages, "", FileResult(file="x.pdf"))
    assert pages[1] is plain
