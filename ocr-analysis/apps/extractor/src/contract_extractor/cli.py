"""CLI batch: `contract-extractor run <file-atau-folder>`, `contract-extractor evaluate <results> <label>`,
`contract-extractor serve-stdio` (worker untuk backend website), dan `contract-extractor xlsx` (Excel untuk website)."""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError

from contract_extractor.common.config import ExtractorProvider, ExtractorSettings, Settings, load_settings
from contract_extractor.evaluation import EvaluationError, KelompokFilter, format_report, report_json, write_detail
from contract_extractor.evaluation import evaluate as evaluate_results
from contract_extractor.output.export import write_outputs
from contract_extractor.extractor_method.llm.base import ExtractorUnavailable
from contract_extractor.extractor_method.llm.factory import build_extractor
from contract_extractor.document.ocr.engine import OcrUnavailable, build_runner
from contract_extractor.pipeline import process_path
from contract_extractor.output.workbook import WorkbookSpec, build_workbook
from contract_extractor.worker import serve, write_message

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
    vendor_folders: Annotated[
        bool,
        typer.Option(
            "--vendor-folders",
            help="Folder pertama di bawah input adalah vendor (<vendor>/<masa kontrak>/<pdf>), dicocokkan dengan dokumen",
        ),
    ] = False,
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

    results = process_path(input_path, settings, recursive, ocr, extractor, vendor_folders)
    written = write_outputs(results, out_dir, settings.output.formats, settings.output.nik == "full")

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


@app.command("serve-stdio")
def serve_stdio(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", exists=True, dir_okay=False, resolve_path=True, help="File konfigurasi YAML"),
    ] = None,
    allow_auto: Annotated[
        Optional[bool],
        typer.Option("--allow-auto/--no-allow-auto", help="Timpa decision.allow_auto (website: env ALLOW_AUTO)"),
    ] = None,
    claude: Annotated[
        bool,
        typer.Option(
            "--claude",
            help="Claude membantu pola teks (alur website): jabatan, potongan NIK/tanda tangan bila ragu; API key bila ada, else login",
        ),
    ] = False,
    model: Annotated[Optional[str], typer.Option("--model", help="Timpa extractor.model (website: env CLAUDE_MODEL)")] = None,
    effort: Annotated[
        Optional[str], typer.Option("--effort", help="Timpa extractor.effort (website: env CLAUDE_EFFORT)")
    ] = None,
) -> None:
    """Worker untuk backend website: PDF masuk lewat stdin, hasil JSON keluar lewat stdout (protokol di worker.py)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    protocol = sys.stdout.buffer
    sys.stdout = sys.stderr  # a stray print from any library must never corrupt the protocol stream
    settings = load_settings(config)
    if allow_auto is not None:
        settings.decision.allow_auto = allow_auto
    try:
        _override_extractor(settings, claude, model, effort)
    except (ValidationError, ValueError) as exc:
        write_message(protocol, {"ready": False, "error": "config_invalid"})
        raise typer.Exit(code=2) from exc
    try:
        ocr = build_runner(settings)
    except OcrUnavailable as exc:
        write_message(protocol, {"ready": False, "error": "ocr_unavailable"})
        raise typer.Exit(code=2) from exc
    try:
        extractor = build_extractor(settings)
    except ExtractorUnavailable as exc:
        if not claude:  # an extractor the config file turned on
            write_message(protocol, {"ready": False, "error": "extractor_unavailable"})
            raise typer.Exit(code=2) from exc
        # The patterns must keep working without Claude; the files it would have helped with stay in review.
        logging.getLogger(__name__).warning("Claude unavailable; the patterns run without it")
        extractor = None
    serve(sys.stdin.buffer, protocol, settings, ocr, extractor)


def _override_extractor(settings: Settings, claude: bool, model: str | None, effort: str | None) -> None:
    """Apply the website's Claude settings over the config file, validated by the same rules as the file."""
    changes: dict[str, object] = {}
    if claude:
        changes.update(enabled=True, provider=ExtractorProvider.CLAUDE, assist=True)
    if model:
        changes["model"] = model
    if effort:
        changes["effort"] = effort
    if changes:
        settings.extractor = ExtractorSettings.model_validate({**settings.extractor.model_dump(), **changes})


MAX_SPEC_BYTES = 64 * 1024 * 1024


@app.command("xlsx")
def xlsx() -> None:
    """Untuk backend website: spesifikasi workbook JSON masuk lewat stdin, file .xlsx keluar lewat stdout."""
    raw = sys.stdin.buffer.read(MAX_SPEC_BYTES + 1)
    if len(raw) > MAX_SPEC_BYTES:
        typer.echo("spesifikasi terlalu besar", err=True)
        raise typer.Exit(code=2)
    try:
        spec = WorkbookSpec.model_validate_json(raw)
    except ValidationError as exc:
        # Where and what failed, never the input itself: the spec holds employee names and NIKs.
        problems = sorted({f"{'.'.join(str(p) for p in e['loc']) or 'workbook'}: {e['msg']}" for e in exc.errors()})
        typer.echo(f"spesifikasi tidak valid: {'; '.join(problems[:5])}", err=True)
        raise typer.Exit(code=2) from None
    sys.stdout.buffer.write(build_workbook(spec))
    sys.stdout.buffer.flush()
