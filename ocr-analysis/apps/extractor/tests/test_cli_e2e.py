"""End to end through the CLI on a synthetic batch: native and scanned, signed and unsigned, one broken file.

Every stage runs, with the cloud extractor replaced by a recording fake and auto decisions allowed by config.
Skipped when Tesseract with ind+eng is not installed.
"""

import csv
import json
import logging
import re
from datetime import date

import pytest
from helpers import scan_pdf_from, signed_contract_pdf
from openpyxl import load_workbook
from typer.testing import CliRunner

from contract_extractor import cli
from contract_extractor.extractors.base import Evidence, ExtractionResult

pytestmark = pytest.mark.tesseract

FILENAME = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
ANSWER = ExtractionResult(
    nama_karyawan="Budi Santoso",
    tanggal_mulai=date(2026, 1, 1),
    tanggal_selesai=date(2026, 12, 31),
    bukti=Evidence(nama="Nama : Budi Santoso", periode="mulai tanggal 1 Januari 2026 sampai dengan"),
    confidence=0.95,
)


class RecordingExtractor:
    name = "fake-llm"

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def extract(self, masked_text: str) -> ExtractionResult:
        self.payloads.append(masked_text)
        return ANSWER


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    """Same file name in four vendor folders, as HR folders often are."""
    root = tmp_path_factory.mktemp("e2e")
    folder = root / "in"
    for name in ("unsigned", "signed", "scan", "broken"):
        (folder / name).mkdir(parents=True)
    signed_contract_pdf(folder / "unsigned" / FILENAME)
    signed_contract_pdf(folder / "signed" / FILENAME, "company_vector", "annotation")
    scan_pdf_from(signed_contract_pdf(root / "native.pdf", "company_vector", "vector"), folder / "scan" / FILENAME, skew_deg=1.5)
    (folder / "broken" / FILENAME).write_bytes(b"%PDF-1.4\nthis is not a real pdf body")
    config = root / "config.yaml"
    config.write_text("decision:\n  allow_auto: true\n", encoding="utf-8")
    return root, folder, config


@pytest.fixture(scope="module")
def run(batch, tesseract_cmd):
    root, folder, config = batch
    extractor = RecordingExtractor()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TESSERACT_CMD", str(tesseract_cmd))
        mp.setattr(cli, "build_extractor", lambda settings: extractor)
        logs = _LogCapture()
        logging.getLogger().addHandler(logs)
        try:
            outcome = CliRunner().invoke(cli.app, ["run", str(folder), "-r", "-c", str(config), "-o", str(root / "out")])
        finally:
            logging.getLogger().removeHandler(logs)
    rows = {row["file"]: row for row in json.loads((root / "out" / "results.json").read_text(encoding="utf-8"))}
    return outcome, rows, extractor.payloads, logs.messages, root / "out"


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_batch_runs_and_names_rows_by_relative_path(run):
    outcome, rows, _, _, _ = run
    assert outcome.exit_code == 0, outcome.output
    assert "4 file diproses" in outcome.output
    assert set(rows) == {f"{name}/{FILENAME}" for name in ("unsigned", "signed", "scan", "broken")}


def test_each_contract_gets_the_right_answer(run):
    _, rows, _, _, _ = run
    for name in ("unsigned", "signed", "scan"):
        row = rows[f"{name}/{FILENAME}"]
        assert (row["nama_karyawan"], row["tanggal_mulai"], row["tanggal_selesai"]) == ("Budi Santoso", "2026-01-01", "2026-12-31")
        assert row["log"]["validasi_nama_file"] == {"nama": "cocok", "periode": "cocok"}

    assert rows[f"unsigned/{FILENAME}"]["status_ttd"] == "unsigned"
    assert rows[f"signed/{FILENAME}"]["status_ttd"] == "signed"
    assert rows[f"signed/{FILENAME}"]["log"]["tanda_tangan"]["sumber"] == "annotation"
    assert rows[f"scan/{FILENAME}"]["status_ttd"] == "signed"
    assert rows[f"scan/{FILENAME}"]["log"]["tanda_tangan"]["sumber"] == "pixels"

    # All-green native files pass automatically once the config allows it; the broken one never does.
    assert rows[f"unsigned/{FILENAME}"]["keputusan"] == "auto"
    assert rows[f"signed/{FILENAME}"]["keputusan"] == "auto"
    assert rows[f"broken/{FILENAME}"]["keputusan"] == "review_priority"
    assert "processing_error" in rows[f"broken/{FILENAME}"]["flags"]  # a valid header, then a parse failure


def test_only_masked_identity_text_left_and_only_for_readable_files(run):
    _, rows, payloads, _, _ = run
    assert len(payloads) == 3
    for payload in payloads:
        assert not re.search(r"\d{5,}", payload)  # no NIK fragment
        assert "Lahir" not in payload and "1990" not in payload
        assert "Rp" not in payload and "7.500.000" not in payload  # the salary page was never read
    assert rows[f"broken/{FILENAME}"]["log"]["teks_keluar_karakter"] == 0
    assert all(rows[f"{n}/{FILENAME}"]["log"]["teks_keluar_karakter"] > 0 for n in ("unsigned", "signed", "scan"))


def test_full_resolution_reads_touch_only_identity_and_signature_pages(run):
    _, rows, _, _, _ = run
    for name in ("unsigned", "signed", "scan"):
        log = rows[f"{name}/{FILENAME}"]["log"]
        selected = {*log["p_id"], log["p_ttd"]}
        deep = {
            int(page)
            for page, components in log["komponen_halaman"].items()
            if any("300dpi" in c or c.endswith(":masked") for c in components)
        }
        assert deep <= selected, (name, deep, selected)
        assert "2" not in log["komponen_halaman"] or all("150dpi" in c or c == "text_layer" for c in log["komponen_halaman"]["2"])


def test_logs_carry_no_names_or_identity_numbers(run):
    _, _, _, messages, _ = run
    joined = "\n".join(messages)
    assert "Budi" not in joined and "Santoso" not in joined
    assert not re.search(r"\d{10,}", joined)


def test_csv_and_xlsx_hold_one_row_per_file(run):
    _, _, _, _, out = run
    with (out / "results.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 4
    sheet = load_workbook(out / "results.xlsx").active
    assert sheet.max_row == 5  # header + 4 files
