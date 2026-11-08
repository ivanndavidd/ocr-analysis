"""CLI batch: `contract-extractor run <file-atau-folder>` dan `contract-extractor evaluate <results> <label>`."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Annotated, Optional

import typer

from contract_extractor.config import load_settings
from contract_extractor.evaluation import EvaluationError, KelompokFilter, format_report, report_json, write_detail
from contract_extractor.evaluation import evaluate as evaluate_results
from contract_extractor.export import write_outputs
from contract_extractor.extractors.base import ExtractorUnavailable
from contract_extractor.extractors.factory import build_extractor
from contract_extractor.ocr.engine import OcrUnavailable, build_runner
from contract_extractor.pipeline import process_path

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Ekstraksi nama karyawan, masa kontrak, dan status tanda tangan dari PDF kontrak."""


@app.command()
def run(
    input_path: Annotated[
        Path, typer.Argument(exists=True, readable=True, resolve_path=True, help="File PDF atau folder berisi PDF")
    ],
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", exists=True, dir_okay=False, resolve_path=True, help="File konfigurasi YAML"),
    ] = None,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", file_okay=False, resolve_path=True, help="Folder keluaran"),
    ] = None,
    recursive: Annotated[bool, typer.Option("--recursive", "-r", help="Ikut memproses subfolder")] = False,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = load_settings(config)
    out_dir = out or settings.output.dir.resolve()
    try:
        # Local OCR is part of the product; refuse to run degraded rather than silently skip scans.
        ocr = build_runner(settings)
        # An enabled extractor that cannot run would send every file to review; stop before reading any file.
        extractor = build_extractor(settings)
    except (OcrUnavailable, ExtractorUnavailable) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if extractor is None:
        typer.echo("extractor nonaktif (extractor.enabled: false): tidak ada teks yang dikirim, semua file masuk review")

    results = process_path(input_path, settings, recursive, ocr, extractor)
    written = write_outputs(results, out_dir, settings.output.formats)

    summary = Counter(r.keputusan.value for r in results)
    typer.echo(f"{len(results)} file diproses: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    for path in written:
        typer.echo(f"ditulis: {path}")


@app.command()
def evaluate(
    results: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True, help="results.json dari `run`")
    ],
    labels: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True, help="CSV label Fase 0 (format: label-template.csv)"),
    ],
    kelompok: Annotated[KelompokFilter, typer.Option("--kelompok", "-k", help="Kelompok label yang dievaluasi")] = KelompokFilter.SEMUA,
    json_out: Annotated[
        Optional[Path], typer.Option("--json", dir_okay=False, resolve_path=True, help="Tulis laporan sebagai JSON")
    ] = None,
    detail: Annotated[
        Optional[Path],
        typer.Option("--detail", dir_okay=False, resolve_path=True, help="Tulis hasil per file (memuat nama file) sebagai CSV"),
    ] = None,
) -> None:
    """Hitung angka gate PRD (Bagian 5 dan 14) dari hasil `run` dan label manual."""
    try:
        report = evaluate_results(results, labels, kelompok)
    except EvaluationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_report(report))
    if json_out:
        json_out.write_text(json.dumps(report_json(report), ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"ditulis: {json_out}")
    if detail:
        write_detail(report, detail)
        typer.echo(f"ditulis: {detail}")
