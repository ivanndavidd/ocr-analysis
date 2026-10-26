"""CLI batch: `contract-extractor run <file-atau-folder>`."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Annotated, Optional

import typer

from contract_extractor.config import load_settings
from contract_extractor.export import write_outputs
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

    results = process_path(input_path, settings, recursive)
    written = write_outputs(results, out_dir, settings.output.formats)

    summary = Counter(r.keputusan.value for r in results)
    typer.echo(f"{len(results)} file diproses: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    for path in written:
        typer.echo(f"ditulis: {path}")
