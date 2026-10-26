"""Synthetic fixtures only. Never copy real contracts from sample/ into tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import find_tesseract, make_pdf

from contract_extractor.config import OcrSettings, Settings
from contract_extractor.ocr.engine import OcrRunner, OcrUnavailable, build_runner


@pytest.fixture(scope="session")
def tesseract_cmd() -> Path:
    cmd = find_tesseract()
    if cmd is None:
        pytest.skip("Tesseract is not installed")
    return cmd


@pytest.fixture(scope="session")
def ocr_runner(tesseract_cmd: Path) -> OcrRunner:
    try:
        return build_runner(Settings(ocr=OcrSettings(tesseract_cmd=tesseract_cmd)))
    except OcrUnavailable as exc:
        pytest.skip(f"Tesseract cannot run with ind+eng: {exc}")

IDENTITY_PAGE = "\n".join(
    [
        "PERJANJIAN KERJA WAKTU TERTENTU",
        "Nama : Budi Santoso",
        "NIK : 3171012345670001",
        "Jangka waktu berlaku efektif mulai tanggal 1 Januari 2026",
        "sampai dengan tanggal 31 Desember 2026.",
    ]
)


@pytest.fixture
def contracts_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    make_pdf(
        folder / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf",
        [
            ("text", IDENTITY_PAGE),
            ("text", "Pasal 2\nPara pihak sepakat bahwa ketentuan dalam pasal ini berlaku selama masa kontrak."),
            ("blank", ""),
        ],
    )
    make_pdf(folder / "Rina Wulandari - 12 bulan.pdf", [("image", ""), ("image", "")])
    (folder / "Dewi Lestari - 1jan-31mar26.pdf").write_bytes(b"%PDF-1.4\nthis is not a real pdf body")
    (folder / "catatan.txt").write_text("bukan pdf", encoding="utf-8")
    return folder
