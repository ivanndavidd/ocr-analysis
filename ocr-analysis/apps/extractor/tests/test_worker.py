"""`process_bytes` and the `serve-stdio` worker the website backend talks to. Synthetic PDFs only."""

import io
import json
import os
import subprocess
import sys

import pytest
from helpers import signed_contract_pdf

from contract_extractor import cli
from contract_extractor.common.config import ExtractorProvider, InputSettings, Settings
from contract_extractor.pipeline import FLAG_INPUT_REJECTED, process_bytes, process_file
from contract_extractor.worker import ERROR_BAD_HEADER, ERROR_TOO_LARGE, serve

FILENAME = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"


def frame(request_id, filename, data):
    header = json.dumps({"id": request_id, "filename": filename, "size": len(data)}).encode() + b"\n"
    return header + data


def run_worker(stdin_bytes, settings=None):
    out = io.BytesIO()
    served = serve(io.BytesIO(stdin_bytes), out, settings or Settings(), None, None)
    return served, [json.loads(line) for line in out.getvalue().splitlines()]


def test_bytes_and_path_give_the_same_result(contracts_dir, tmp_path):
    files = sorted(contracts_dir.glob("*.pdf")) + [signed_contract_pdf(tmp_path / FILENAME, "annotation")]
    for path in files:
        from_path = process_file(path, Settings())
        from_bytes = process_bytes(path.read_bytes(), path.name, Settings())
        assert from_bytes.model_dump() == from_path.model_dump(), path.name


def test_bytes_are_validated_like_files():
    settings = Settings(input=InputSettings(max_file_mb=1))
    cases = {
        ("x.txt", b"%PDF-1.4\n"): "not_pdf_extension",
        ("x.pdf", b""): "size_out_of_range",
        ("x.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024)): "size_out_of_range",
        ("x.pdf", b"hello, not a pdf"): "not_pdf_magic",
    }
    for (name, data), reason in cases.items():
        result = process_bytes(data, name, settings)
        assert FLAG_INPUT_REJECTED in result.flags
        assert result.log.alasan == [reason]


def test_worker_answers_each_request_in_order(tmp_path):
    pdf = signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()
    broken = "Dewi Lestari - 1jan-31mar26 - SNA.pdf"
    served, messages = run_worker(frame(1, FILENAME, pdf) + frame(2, broken, b"%PDF-1.4\nbroken"))

    assert served == 2
    assert messages[0] == {"ready": True, "claude": False}
    first, second = messages[1], messages[2]
    assert first["id"] == 1 and first["ok"] is True
    assert first["result"]["file"] == FILENAME
    assert first["result"]["log"]["p_id"] == [1]
    assert first["filename"] == {
        "name": "Budi Santoso",
        "start": "2026-01-01",
        "end": "2026-12-31",
        "duration_months": None,
        "entity": None,
        "flags": [],
    }
    assert second["id"] == 2 and second["ok"] is True  # a broken PDF is a result with flags, not a worker failure
    assert "processing_error" in second["result"]["flags"]
    assert second["filename"]["start"] == "2026-01-01"
    assert second["filename"]["entity"] == second["result"]["entitas"] == "SNA"  # kept although the PDF is unreadable


def test_oversized_body_is_skipped_and_the_next_request_still_lines_up(tmp_path):
    pdf = signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1)
    served, messages = run_worker(frame(1, "big.pdf", big) + frame(2, FILENAME, pdf), Settings(input=InputSettings(max_file_mb=1)))

    assert served == 1
    assert messages[1] == {"id": 1, "ok": False, "error": ERROR_TOO_LARGE}
    assert messages[2]["id"] == 2 and messages[2]["result"]["file"] == FILENAME


@pytest.mark.parametrize(
    "header",
    [
        b"not json\n",
        b'{"id": 1, "filename": "a.pdf"}\n',
        b'{"id": "1", "filename": "a.pdf", "size": 3}\n',
        b'{"id": 1, "filename": "a.pdf", "size": -1}\n',
        b'{"id": 1, "filename": "", "size": 3}\n',
        b"[1, 2, 3]\n",
        b'{"id": 1, "filename": "' + b"a" * 5000 + b'.pdf", "size": 3}\n',
    ],
)
def test_untrusted_header_stops_the_worker(header):
    served, messages = run_worker(header + frame(2, "a.pdf", b"%PDF-1.4\n"))
    assert served == 0
    assert messages == [{"ready": True, "claude": False}, {"id": None, "ok": False, "error": ERROR_BAD_HEADER}]


def test_worker_stops_quietly_when_the_backend_closes_mid_file():
    served, messages = run_worker(b'{"id": 1, "filename": "a.pdf", "size": 100}\n%PDF-1.4')
    assert served == 0
    assert messages == [{"ready": True, "claude": False}]


@pytest.mark.tesseract
def test_serve_stdio_round_trip_through_a_real_process(tmp_path, tesseract_cmd):
    pdf = signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()
    env = {**os.environ, "TESSERACT_CMD": str(tesseract_cmd)}
    done = subprocess.run(
        [sys.executable, "-c", "from contract_extractor.cli import app; app()", "serve-stdio"],
        input=frame(7, FILENAME, pdf),
        capture_output=True,
        env=env,
        timeout=180,
        check=True,
    )
    messages = [json.loads(line) for line in done.stdout.splitlines()]
    assert messages[0] == {"ready": True, "claude": False}
    assert messages[1]["id"] == 7 and messages[1]["ok"] is True
    assert messages[1]["result"]["status_ttd"] == "signed"
    assert b"Budi" not in done.stderr  # logs refer to files by hash, never by name


def test_serve_stdio_turns_claude_on_with_the_website_settings():
    settings = Settings()
    cli._override_extractor(settings, True, "claude-sonnet-5-5", "high")
    assert (settings.extractor.enabled, settings.extractor.provider) == (True, ExtractorProvider.CLAUDE)
    assert (settings.extractor.model, settings.extractor.effort) == ("claude-sonnet-5-5", "high")

    untouched = Settings()
    cli._override_extractor(untouched, False, None, None)
    assert untouched.extractor == Settings().extractor


@pytest.mark.parametrize("args", [["--effort", "extreme"], ["--model", "claude; rm -rf /"]])
def test_serve_stdio_refuses_invalid_claude_settings_before_reading_anything(args):
    done = subprocess.run(
        [sys.executable, "-c", "from contract_extractor.cli import app; app()", "serve-stdio", "--claude", *args],
        input=b"",
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2
    assert [json.loads(line) for line in done.stdout.splitlines()] == [{"ready": False, "error": "config_invalid"}]


def test_the_vendor_folder_rides_along_in_the_header(tmp_path):
    pdf = signed_contract_pdf(tmp_path / FILENAME, "annotation").read_bytes()
    header = {"id": 1, "filename": FILENAME, "size": len(pdf), "vendor": "PT Contoh Sejahtera"}
    _, messages = run_worker(json.dumps(header).encode() + b"\n" + pdf)
    assert messages[1]["result"]["vendor"] == "PT Contoh Sejahtera"
    assert messages[1]["result"]["log"]["validasi_vendor"] in ("cocok", "tidak_cocok")

    bad = {"id": 2, "filename": FILENAME, "size": 3, "vendor": ["PT"]}
    _, messages = run_worker(json.dumps(bad).encode() + b"\n%PD")
    assert messages[1] == {"id": None, "ok": False, "error": ERROR_BAD_HEADER}


def test_serve_stdio_turns_claude_on_for_the_website_flow():
    settings = Settings()
    cli._override_extractor(settings, True, None, None)
    assert (settings.extractor.enabled, settings.extractor.assist) == (True, True)


def test_the_patterns_still_start_when_claude_cannot(tmp_path):
    env = {**os.environ, "CLAUDE_CODE_CMD": str(tmp_path / "missing-claude.exe"), "ANTHROPIC_API_KEY": "", "PATH": ""}
    done = subprocess.run(
        [sys.executable, "-c", "from contract_extractor.cli import app; app()", "serve-stdio", "--claude"],
        input=b"", capture_output=True, env=env, timeout=60, check=False,
    )
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    if lines and lines[0].get("error") == "ocr_unavailable":
        pytest.skip("Tesseract is not installed")
    assert lines == [{"ready": True, "claude": False}] and done.returncode == 0
