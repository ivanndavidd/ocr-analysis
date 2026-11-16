"""Export hasil ke JSON, CSV, dan Excel dengan skema tetap (FR-8)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from contract_extractor.common.config import OutputFormat
from contract_extractor.common.models import FileResult
from contract_extractor.extractor_method.pattern.nik import mask_nik

COLUMNS = [
    "file",
    "nama_karyawan",
    "nik",
    "posisi",
    "entitas",
    "vendor",
    "validasi_vendor",
    "tanggal_mulai",
    "tanggal_selesai",
    "durasi_bulan",
    "status_ttd",
    "keputusan",
    "flags",
    "confidence_nama",
    "confidence_periode",
    "confidence_nik",
    "confidence_posisi",
    "confidence_ttd",
    "alasan",
    "catatan_vendor",
]

# Values from PDFs and filenames are untrusted; a leading formula character would execute in Excel.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _nik(result: FileResult, full_nik: bool) -> str | None:
    """Masked unless `output.nik: full`: an exported file outlives the run and may be passed around."""
    return result.nik if full_nik or result.nik is None else mask_nik(result.nik)


def _row(result: FileResult, full_nik: bool) -> dict[str, object]:
    return {
        "file": result.file,
        "nama_karyawan": result.nama_karyawan,
        "nik": _nik(result, full_nik),
        "posisi": result.posisi,
        "entitas": result.entitas,
        "vendor": result.vendor,
        "validasi_vendor": result.log.validasi_vendor,
        "tanggal_mulai": result.tanggal_mulai.isoformat() if result.tanggal_mulai else None,
        "tanggal_selesai": result.tanggal_selesai.isoformat() if result.tanggal_selesai else None,
        "durasi_bulan": result.durasi_bulan,
        "status_ttd": result.status_ttd.value if result.status_ttd else None,
        "keputusan": result.keputusan.value,
        "flags": ";".join(result.flags),
        "confidence_nama": result.confidence.get("nama"),
        "confidence_periode": result.confidence.get("periode"),
        "confidence_nik": result.confidence.get("nik"),
        "confidence_posisi": result.confidence.get("posisi"),
        "confidence_ttd": result.confidence.get("ttd"),
        "alasan": ";".join(result.log.alasan),
        "catatan_vendor": result.catatan_vendor,
    }


def neutralize_formula(value: object) -> object:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def write_json(results: list[FileResult], path: Path, full_nik: bool = False) -> None:
    payload = [{**r.model_dump(mode="json"), "nik": _nik(r, full_nik)} for r in results]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(results: list[FileResult], path: Path, full_nik: bool = False) -> None:
    # utf-8-sig so Excel opens Indonesian names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: neutralize_formula(v) for k, v in _row(r, full_nik).items()})


def write_xlsx(results: list[FileResult], path: Path, full_nik: bool = False) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "hasil"
    ws.append(COLUMNS)
    for r in results:
        row = _row(r, full_nik)
        values = [row[col] for col in COLUMNS]
        ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in values])
        for cell in ws[ws.max_row]:
            # openpyxl turns any string starting with "=" into a formula; force plain text instead.
            if isinstance(cell.value, str) and cell.value.startswith(FORMULA_PREFIXES):
                cell.data_type = "s"
    wb.save(path)


_WRITERS = {
    OutputFormat.JSON: ("results.json", write_json),
    OutputFormat.CSV: ("results.csv", write_csv),
    OutputFormat.XLSX: ("results.xlsx", write_xlsx),
}


def write_outputs(
    results: list[FileResult], out_dir: Path, formats: list[OutputFormat], full_nik: bool = False
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        filename, writer = _WRITERS[fmt]
        target = out_dir / filename
        writer(results, target, full_nik)
        written.append(target)
    return written
