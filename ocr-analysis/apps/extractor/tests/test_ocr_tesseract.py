"""Real Tesseract runs on synthetic scans. Skipped when Tesseract with ind+eng is not installed."""

import re
from datetime import date

import pytest
from helpers import (
    BODY_FILLER,
    EMPLOYEE_NIK,
    PAGE_H,
    PERIOD_LINES,
    PageExtras,
    flow,
    llm_answer,
    make_pdf,
    pasted_photo_pdf_from,
    scan_pdf_from,
    signature_rows,
    signed_contract_pdf,
)

from contract_extractor.common.config import ExtractorSettings, Settings
from contract_extractor.extractor_method.llm.base import ExtractionResult, ImageAnswer, ImageTask
from contract_extractor.common.models import Decision, PageKind, RegexPeriod, SignatureStatus
from contract_extractor.document.pdf.render import PageRenderer
from contract_extractor.pipeline import (
    FLAG_FORMAT_NONSTANDARD,
    REASON_OCR_LOW_CONFIDENCE,
    REASON_IDENTITY_PAGE_NOT_FOUND,
    REASON_SIGNATURE_LLM_SEES_NONE,
    REASON_SIGNATURE_PAGE_NOT_FOUND,
    process_file,
)
from contract_extractor.document.pages.page_classify import ISSUE_PHOTO_IN_DOCUMENT

pytestmark = pytest.mark.tesseract


@pytest.fixture(scope="module")
def scanned_contract(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scan")
    native = signed_contract_pdf(folder / "native.pdf")
    return scan_pdf_from(native, folder / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", skew_deg=1.5)


def test_full_read_of_a_crooked_scan_passes_quality(ocr_runner, scanned_contract):
    with PageRenderer(scanned_contract) as renderer:
        outcome = ocr_runner.read(renderer, 1, "budi santoso")
    assert outcome.passed, outcome
    assert outcome.engine == "tesseract"
    assert outcome.mean_conf >= 0.8
    text = outcome.page.text.lower()
    assert "perjanjian kerja" in text and "budi santoso" in text


def test_scanned_contract_end_to_end(ocr_runner, scanned_contract):
    result = process_file(scanned_contract, Settings(), ocr_runner)

    assert set(result.log.jenis_halaman.values()) == {PageKind.SCAN}
    assert result.log.p_id == [1]
    assert result.log.p_ttd == 5
    assert REASON_IDENTITY_PAGE_NOT_FOUND not in result.log.alasan
    assert REASON_SIGNATURE_PAGE_NOT_FOUND not in result.log.alasan
    assert REASON_OCR_LOW_CONFIDENCE not in result.log.alasan
    assert result.log.ocr_p_id == "tesseract"
    assert result.log.ocr_conf[1] >= 0.8

    # Data-minimization proof: only P_id and P_ttd were read; the middle pages never were.
    assert result.log.komponen_halaman == {1: ["tesseract@300dpi"], 5: ["tesseract@300dpi", "signature@300dpi"]}

    # Tahap 4 on the scan: nobody signed this synthetic contract.
    assert result.status_ttd is SignatureStatus.UNSIGNED
    assert result.log.tanda_tangan.jangkar == "nama"

    # Tahap 3, local part, on OCR text: period found by regex, NIK and birth line masked.
    assert result.log.periode_regex == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))
    assert result.log.pola_dimask >= 2


def test_scanned_contract_payload_is_masked_and_answer_verifies(ocr_runner, scanned_contract):
    sent: list[str] = []

    class RecordingExtractor:
        name = "fake-llm"

        def extract(self, masked_text: str) -> ExtractionResult:
            sent.append(masked_text)
            return llm_answer(period_quote="mulai tanggal 1 Januari 2026 sampai dengan")

    result = process_file(scanned_contract, Settings(), ocr_runner, RecordingExtractor())

    [payload] = sent
    assert not re.search(r"\d{5,}", payload)  # no NIK fragment survives OCR + masking
    assert "Lahir" not in payload and "1990" not in payload
    assert (result.nama_karyawan, result.tanggal_selesai) == ("Budi Santoso", date(2026, 12, 31))
    assert result.log.validasi_nama_file == {"nama": "cocok", "periode": "cocok"}


def test_contract_photographed_into_word_is_read_and_reported(tmp_path, ocr_runner):
    native = signed_contract_pdf(tmp_path / "native.pdf")
    pasted = pasted_photo_pdf_from(native, tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    result = process_file(pasted, Settings(), ocr_runner)

    # Before, the Word footer made these pages native, so only the footer was read and the photo was ignored.
    assert set(result.log.jenis_halaman.values()) == {PageKind.UNCLASSIFIED}
    assert result.log.format_halaman == {n: ISSUE_PHOTO_IN_DOCUMENT for n in range(1, 6)}
    assert result.catatan_vendor.startswith("Halaman 1, 2, 3, 4, 5: foto atau scan ditempel ke dokumen Word")
    assert FLAG_FORMAT_NONSTANDARD in result.flags

    assert (result.log.p_id, result.log.p_ttd) == ([1], 5)
    assert REASON_OCR_LOW_CONFIDENCE not in result.log.alasan
    assert result.log.periode_regex == RegexPeriod(tanggal_mulai=date(2026, 1, 1), tanggal_selesai=date(2026, 12, 31))
    assert result.status_ttd is SignatureStatus.UNSIGNED
    assert result.log.tanda_tangan.jangkar == "nama"
    assert result.keputusan is not Decision.AUTO


def test_signed_scan_is_detected_from_pixels(tmp_path, ocr_runner):
    native = signed_contract_pdf(tmp_path / "native.pdf", "company_vector", "vector")
    scan = scan_pdf_from(native, tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", skew_deg=1.5)
    result = process_file(scan, Settings(), ocr_runner)

    assert result.status_ttd is SignatureStatus.SIGNED
    assert result.log.tanda_tangan.sumber == "pixels"
    assert result.log.tanda_tangan.jangkar == "nama"
    assert result.confidence["ttd"] >= 0.8


class _CropReader:
    """An extractor that answers the text call with a good reading and every signature crop with `answer`."""

    name = "fake-llm"

    def __init__(self, answer: ImageAnswer) -> None:
        self.answer, self.crops = answer, []

    def extract(self, masked_text: str) -> ExtractionResult:
        return llm_answer()

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        self.crops.append(task)
        return self.answer


@pytest.mark.parametrize(
    "answer, status, reason",
    [
        (ImageAnswer(status="signed", confidence=0.95), SignatureStatus.SIGNED, None),  # the two readers agree
        (ImageAnswer(status="unsigned", confidence=0.7), SignatureStatus.UNCERTAIN, REASON_SIGNATURE_LLM_SEES_NONE),
    ],
)
def test_a_scan_read_signed_is_shown_to_the_extractor_too(tmp_path, ocr_runner, answer, status, reason):
    native = signed_contract_pdf(tmp_path / "native.pdf", "company_vector", "vector")
    scan = scan_pdf_from(native, tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", skew_deg=1.5)
    reader = _CropReader(answer)
    result = process_file(scan, Settings(extractor=ExtractorSettings(enabled=True, assist=True)), ocr_runner, reader)

    assert ImageTask.SIGNATURE in reader.crops and result.log.tanda_tangan.sumber == "pixels"
    assert result.status_ttd is status
    assert (reason in result.log.alasan) if reason else not any("signature_llm" in r for r in result.log.alasan)


def ruled_identity_scan(folder, dpi: int = 200, closing_page: bool = False):
    """A scanned identity page whose employee rows sit in a ruled table: a border round every row and between the
    label, colon and value columns, as 15% of the generator's contracts print them. `closing_page` adds a second page
    with the signature block, so the identity page is not also the signature page."""
    rows = [("Nama", "Budi Santoso"), ("Tempat/Tgl. Lahir", "Bandung, 1 Januari 1990"), ("NIK", EMPLOYEE_NIK),
            ("Jenis Kelamin", "Laki-laki"), ("No. HP", "081234567890"), ("Posisi", "Staff Administrasi")]
    top, step, columns = 220.0, 20.0, (70.0, 190.0, 202.0, 540.0)
    layout = [(70.0, 80.0, "PERJANJIAN KERJA WAKTU TERTENTU"), (70.0, 110.0, "Nomor: 001/PKWT/I/2026"),
              (70.0, 140.0, "Nama : Andi Wijaya"), (70.0, 158.0, "Jabatan : Direktur"),
              (70.0, 190.0, "selanjutnya disebut PIHAK PERTAMA, dengan perseorangan yaitu:")]
    for n, (label, value) in enumerate(rows):
        y = top + n * step
        layout += [(columns[0] + 4, y, label), (columns[1] + 3, y, ":"), (columns[2] + 4, y, value)]
    bottom = top + len(rows) * step
    layout += [(70.0, bottom + 20, "selanjutnya disebut PIHAK KEDUA."), (70.0, bottom + 50, PERIOD_LINES[0]),
               (70.0, bottom + 68, PERIOD_LINES[1])]
    lines = [(columns[0], top - 5 + n * step, columns[-1], top - 5 + n * step) for n in range(len(rows) + 1)]
    lines += [(x, top - 5, x, bottom - 5) for x in columns]
    ops = "0.8 w " + " ".join(f"{x0} {PAGE_H - y0} m {x1} {PAGE_H - y1} l S" for x0, y0, x1, y1 in lines)
    pages: list[tuple[str, object]] = [("layout", layout)]
    if closing_page:
        closing = flow(["Pasal 10 Penutup"] + [BODY_FILLER] * 4) + signature_rows(300.0)
        pages.append(("layout", [(x, y, text) for y, x, text in closing]))
    native = make_pdf(folder / "native.pdf", pages, {0: PageExtras(ops=ops)})
    return scan_pdf_from(native, folder / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", dpi=dpi)


def test_ruled_identity_table_reads_every_row(tmp_path, ocr_runner):
    # Without erasing the borders, Tesseract dropped the name row and glued a border to the NIK (`(:13226...`).
    with PageRenderer(ruled_identity_scan(tmp_path)) as renderer:
        outcome = ocr_runner.read(renderer, 1, "budi santoso", erase_rules=True)
    assert outcome.passed, outcome
    text = outcome.page.text
    assert re.search(r"Nama\W+Budi Santoso", text)
    assert EMPLOYEE_NIK in re.findall(r"\d{12,}", text)  # the NIK alone, no border read as a digit
    assert "Staff Administrasi" in text


def test_ruled_identity_table_end_to_end(tmp_path, ocr_runner):
    result = process_file(ruled_identity_scan(tmp_path, closing_page=True), Settings(), ocr_runner)

    assert result.log.p_id == [1] and result.log.p_ttd == 2
    assert REASON_OCR_LOW_CONFIDENCE not in result.log.alasan
    assert result.log.validasi_nama_file["nama"] == "cocok"
    assert result.nik == EMPLOYEE_NIK
    assert result.posisi == "Staff Administrasi"
