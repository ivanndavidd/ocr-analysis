"""Real Tesseract runs on synthetic scans. Skipped when Tesseract with ind+eng is not installed."""

import pytest
from helpers import scan_pdf_from, signed_contract_pdf

from contract_extractor.config import Settings
from contract_extractor.models import PageKind
from contract_extractor.pdf.render import PageRenderer
from contract_extractor.pipeline import REASON_OCR_LOW_CONFIDENCE, REASON_PAGE_SELECTION_AMBIGUOUS, process_file

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
    assert REASON_PAGE_SELECTION_AMBIGUOUS not in result.log.alasan
    assert REASON_OCR_LOW_CONFIDENCE not in result.log.alasan
    assert result.log.ocr_p_id == "tesseract"
    assert result.log.ocr_conf[1] >= 0.8

    # Data-minimization proof: every page was read locally, and only P_id/P_ttd were read at full resolution.
    assert result.log.komponen_halaman[1] == ["tesseract@150dpi", "tesseract@300dpi"]
    assert result.log.komponen_halaman[3] == ["tesseract@150dpi"]
    assert result.log.komponen_halaman[5] == ["tesseract@150dpi", "tesseract@300dpi"]
