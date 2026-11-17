"""
Compare every reader setup tried on the two synthetic datasets, the first dataset (output/) and the second
dataset (output-v2/): accuracy, HR's check load and Claude's cost.

Only scans and photos are compared: digital PDFs are read from their text layer and come out the same in every
setup. Each setup is a benchmark run (runs/<key>) or a website accuracy report
(contract-pdf-generator/accuracy/<folder>), listed in SETUPS below. A report may add "+<job.json>": a later rerun of
some of its files, whose rows replace theirs.

Run, from ocr-benchmark:

    .venv/Scripts/python tools/comparison.py
    .venv/Scripts/python ocr-benchmark/tools/comparison.py

Prints, with the rich library (one row per setup, one section per dataset):
    1. All setups, both datasets together: accuracy, check load and cost
    2. Correct values per field, per dataset
    3. Wrong values without a yellow mark, and what every setup read there
and saves a plain-text copy to results/comparison.txt. NIKs are shown in full: both datasets are synthetic, so only
use this on generated contracts.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

BENCH_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BENCH_DIR), str(BENCH_DIR / "tools")]

from bench.paths import GENERATOR, RUNS  # noqa: E402
from compare_runs import bench_run, website_report  # noqa: E402  (also puts the generator on sys.path)
from compare_accuracy import CORRECT, FIELDS, UNDECIDED, Truth  # noqa: E402
from copy_to_onedrive import period_folder, safe_folder  # noqa: E402

OUTPUT_PATH = BENCH_DIR / "results" / "comparison.txt"
IMAGE_FORMS = {"scan", "photo"}
SETS = {"output": "First dataset", "output-v2": "Second dataset"}  # output/ and output-v2/


@dataclass(frozen=True)
class Setup:
    label: str
    short: str  # column header in table 6
    sources: dict[str, str] = field(default_factory=dict)  # dataset -> run key, or "web:<report>[+<job.json>]"
    # dataset -> a rerun of the CPU sample with the laptop lightly loaded, for setups whose main run kept every core
    # busy (the CPU seconds of a busy run are higher than the setup's own cost); without one, the main run is used
    cpu_runs: dict[str, str] = field(default_factory=dict)


SETUPS = [
    Setup("Tesseract alone", "T",
          {"output": "tesseract_fast-img", "output-v2": "tesseract_fast-v2-img"}),
    Setup("RapidOCR alone", "R",
          {"output": "rapid_v6_small-img", "output-v2": "rapid_v6_small-v2-img"}),
    Setup("Tesseract → RapidOCR", "T→R",
          {"output": "tesseract_second-stamp", "output-v2": "tesseract_second-v2-stamp"}),
    Setup("RapidOCR → Tesseract", "R→T",
          {"output": "rapid_second-img", "output-v2": "rapid_second-v2-img"},
          {"output": "rapid_second-cpu50", "output-v2": "rapid_second-v2-cpu50"}),
    # The Claude runs of 2026-11-13 with the signature agreement rule of 2026-11-16 applied to their saved answers
    # (`-agree`: pipeline._agreement_settles replayed, no new Claude calls). The website runs of 2026-11-11 (report
    # folders 20261111-141325 and 20261111-125049, plus runs/web-rerun47-job.json) gave the same flow without a CPU figure.
    Setup("Tesseract → RapidOCR → Claude (now)", "T→R→C",
          {"output": "tesseract_second-img-claude-agree", "output-v2": "tesseract_second-v2-img-claude-agree"},
          {"output": "tesseract_second-cpu50-claude", "output-v2": "tesseract_second-v2-cpu50-claude"}),
    Setup("RapidOCR → Tesseract → Claude", "R→T→C",
          {"output": "rapid_second-img-claude-agree", "output-v2": "rapid_second-v2-img-claude-agree"},
          {"output": "rapid_second-cpu50-claude", "output-v2": "rapid_second-v2-cpu50-claude"}),
]

# CPU seconds per file are averaged over the same contracts for every setup: bench.run's `--form scan --form photo
# --sample 50` (seed 7) of each dataset
CPU_SAMPLE = 50

# ================================================================
# Load
# ================================================================

def load_truths(dataset: str) -> dict[str, Truth]:
    """The scans and photos of one dataset, keyed by file name, with the path the website gives them."""
    records = json.loads((GENERATOR / dataset / "ground_truth.json").read_text(encoding="utf-8"))
    truths = {}
    for r in records:
        if r["meta"]["form"] in IMAGE_FORMS:
            folder = f"{safe_folder(r['meta']['company'])}/{safe_folder(period_folder(r))}"
            truths[r["file_name"]] = Truth(f"{folder}/{r['file_name']}", r)
    return truths


def load_source(source: str, truths: dict[str, Truth]) -> dict:
    """file name -> (Scored, Claude tokens, Claude calls, text sent to Claude, seconds, CPU seconds)."""
    if source.startswith("web:"):
        report, plus, patch = source[4:].partition("+")
        spec = str(GENERATOR / "accuracy" / report) + (plus + str(BENCH_DIR / patch) if patch else "")
        return website_report(spec, truths)
    return bench_run(source, truths)


def cpu_sample(dataset: str) -> list[str]:
    """The contracts bench.run reads with `--form scan --form photo --sample 50`: the same draw, the same seed."""
    records = sorted(json.loads((GENERATOR / dataset / "ground_truth.json").read_text(encoding="utf-8")),
                     key=lambda r: r["file_name"])
    images = [r for r in records if r["meta"]["form"] in IMAGE_FORMS]
    return sorted(r["file_name"] for r in random.Random(7).sample(images, min(CPU_SAMPLE, len(images))))


def peak_memory(key: str) -> float:
    """The most memory one worker of a benchmark run held while reading a contract, in MB: the worker process at its
    peak plus the program it started for that contract (Tesseract, or the Claude CLI)."""
    records = (json.loads(line) for line in (RUNS / key / "files.jsonl").read_text(encoding="utf-8").splitlines() if line)
    return max(r["peak_mb"] + r.get("child_peak_mb", 0.0) for r in records)


def metrics(files: list[tuple], cpu: list[float] | None = None, memory: float | None = None) -> dict:
    scored = [f[0] for f in files]
    cells = [c for s in scored for c in s.cells.values()]
    out = {
        "contracts": len(files),
        "fully_correct": sum(s.all_correct for s in scored),
        "cells_correct": sum(c["verdict"] == CORRECT for c in cells) / len(cells) if cells else None,
        "nothing_to_check": sum(s.ready for s in scored),
        "wrong_unmarked": sum(c["verdict"] != CORRECT and not c["flagged"] for c in cells),
        "wrong_marked": sum(c["verdict"] not in (CORRECT, UNDECIDED) and c["flagged"] for c in cells),
        "claude_tokens": sum(f[1] for f in files),
        "cpu": statistics.mean(cpu) if cpu else None,
        "memory": memory,
    }
    for name in FIELDS:
        out[f"correct_{name}"] = sum(s.cells[name]["verdict"] == CORRECT for s in scored if name in s.cells)
    return out


def load_all(console: Console) -> tuple[dict, dict, dict, dict]:
    """(files, cpu, memory, truths): files[(dataset, setup label)] = file name -> tuple; cpu[(dataset, setup label)] =
    CPU seconds of each contract of the CPU sample; memory[(dataset, setup label)] = peak MB per worker;
    truths[dataset] = file name -> Truth."""
    truths = {dataset: load_truths(dataset) for dataset in SETS}
    files, cpu, memory = {}, {}, {}
    for setup in SETUPS:
        for dataset, source in setup.sources.items():
            try:
                read = load_source(source, truths[dataset])
            except FileNotFoundError as exc:
                console.print(f"[yellow]Skipping {setup.label} on {SETS[dataset]}: {exc.filename} not found.[/yellow]")
                continue
            read = {n: v for n, v in read.items() if v[0].row is not None}
            if len(read) < len(truths[dataset]):
                console.print(f"[yellow]{setup.label} on {SETS[dataset]}: {len(read)} of "
                              f"{len(truths[dataset])} contracts read.[/yellow]")
            files[(dataset, setup.label)] = read
            if not source.startswith("web:"):  # website reports record no memory
                memory[(dataset, setup.label)] = peak_memory(source)

            sample = cpu_sample(dataset)
            measured = read
            if dataset in setup.cpu_runs:
                try:
                    measured = bench_run(setup.cpu_runs[dataset], {n: truths[dataset][n] for n in sample})
                except FileNotFoundError:
                    console.print(f"[yellow]{setup.label} on {SETS[dataset]}: no light-load CPU run yet.[/yellow]")
                    continue
            seconds = [measured[n][5] for n in sample if n in measured and measured[n][5] is not None]
            if len(seconds) == len(sample):
                cpu[(dataset, setup.label)] = seconds
            else:
                console.print(f"[yellow]{setup.label} on {SETS[dataset]}: CPU for {len(seconds)} of "
                              f"{len(sample)} sample contracts.[/yellow]")
    return files, cpu, memory, truths


# ================================================================
# Format
# ================================================================

def _pct(v):
    return f"{v * 100:.2f}%"


def _int(v):
    return f"{v:,.0f}"


def _tokens(v):
    return f"{v / 1e6:.2f} M" if v else "0"


def _cpu(v):
    return f"{v:.1f}"


def _mb(v):
    return f"{v:,.0f} MB"


# (key, header, formatter, better): better is "high", "low" or None (shown, never coloured)
SUMMARY_COLUMNS = [
    ("contracts", "Contracts", _int, None),
    ("fully_correct", "Fully\ncorrect", _int, "high"),
    ("cells_correct", "Cells\ncorrect", _pct, "high"),
    ("nothing_to_check", "Contracts with\nnothing to check", _int, "high"),
    ("wrong_unmarked", "Wrong cells,\nnot marked", _int, "low"),
    ("wrong_marked", "Wrong cells,\nmarked", _int, "low"),
    ("claude_tokens", "Claude\ntokens", _tokens, None),
    ("cpu", "CPU s\n/ file", _cpu, None),
    ("memory", "Peak RAM\n/ worker", _mb, None),
]
CORRECT_COLUMNS = [(f"correct_{name}", name, _int, "high") for name in FIELDS]


def _cell(value, fmt, best, worst, suffix=""):
    """Best value of the column in bold green with a *, worst in red, the rest plain."""
    if value is None:
        return Text("-  ", style="dim")
    text = fmt(value) + suffix
    if value == best:
        return Text(text + " *", style="bold green")
    if value == worst:
        return Text(text + "  ", style="red")
    return Text(text + "  ")


def _best_worst(values, better):
    known = [v for v in values if v is not None]
    if not better or len(known) < 2 or len(set(known)) == 1:
        return None, None
    high, low = max(known), min(known)
    return (high, low) if better == "high" else (low, high)


def _table(title, caption=None):
    return Table(title=title, title_style="bold", caption=caption, box=box.ROUNDED,
                 header_style="bold", title_justify="left", caption_justify="left")


def metric_table(title, sections, columns, caption=None):
    """
    sections: [(section name, [(setup, metrics), ...]), ...]. One row per setup, one section per dataset; best value of
    each column green and worst red, within its section (the datasets differ in size).
    """
    table = _table(title, caption or "[bold green]* best[/] / [red]worst[/] in the column, within each dataset.")
    table.add_column("Dataset", style="bold cyan", no_wrap=True)
    table.add_column("Setup", no_wrap=True)
    for _, header, _, _ in columns:
        table.add_column(header, justify="right", no_wrap=True)
    for i, (section, rows) in enumerate(sections):
        if i:
            table.add_section()
        limits = {key: _best_worst([m.get(key) for _, m in rows], better) for key, _, _, better in columns}
        for j, (setup, m) in enumerate(rows):
            cells = [_cell(m.get(key), fmt, *limits[key]) for key, _, fmt, _ in columns]
            table.add_row(section if j == 0 else "", setup.label, *cells)
    return table


# ================================================================
# Unmarked mistakes
# ================================================================

def _value(name: str, value) -> str:
    if value is None or value == "":
        return "undecided" if name == "signature" else "empty"
    text = str(value)  # NIKs in full: the datasets are synthetic, and the digit that differs is the point
    return text if len(text) <= 30 else text[:29] + "…"


def unmarked_table(files, truths):
    """One row per (set, contract, field) that any setup got wrong without a yellow mark; per setup what it read."""
    keys = sorted({(dataset, name, f) for (dataset, _), read in files.items() for name, v in read.items()
                   for f, c in v[0].cells.items() if c["verdict"] != CORRECT and not c["flagged"]},
                  key=lambda k: (list(SETS).index(k[0]), FIELDS.index(k[2]), k[1]))
    if not keys:
        return None
    legend = ", ".join(f"{s.short} = {s.label}" for s in SETUPS)
    table = _table("3. Wrong values without a yellow mark (HR would not see them), and what every setup read",
                   f"[green]ok[/] = right; [yellow](y)[/] = wrong or undecided but marked yellow; "
                   f"[bold red]✗[/] = wrong and not marked; - = not run.\n{legend}")
    table.add_column("Dataset", style="bold cyan", no_wrap=True)
    table.add_column("Contract", no_wrap=True, max_width=34, overflow="ellipsis")
    table.add_column("Field", no_wrap=True)
    table.add_column("Expected", no_wrap=True)
    for setup in SETUPS:
        table.add_column(setup.short, justify="right", no_wrap=True)
    previous = None
    for dataset, name, f in keys:
        if previous is not None and dataset != previous:
            table.add_section()
        cells = []
        for setup in SETUPS:
            read = files.get((dataset, setup.label))
            if read is None or name not in read:
                cells.append(Text("-", style="dim"))
                continue
            c = read[name][0].cells[f]
            if c["verdict"] == CORRECT:
                cells.append(Text("ok", style="green"))
            else:
                text = _value(f, c["got"])
                cells.append(Text(f"{text} (y)", style="yellow") if c["flagged"] else Text(f"{text} ✗", style="bold red"))
        expected = _value(f, truths[dataset][name].expected()[f])
        table.add_row(SETS[dataset] if dataset != previous else "", Path(name).stem, f, expected, *cells)
        previous = dataset
    return table


# ================================================================
# Run
# ================================================================

def comparison(output_path: Path = OUTPUT_PATH):
    console = Console(record=True)
    files, cpu, memory, truths = load_all(console)
    sections = []
    for dataset, set_name in SETS.items():
        rows = [(s, metrics(list(files[(dataset, s.label)].values()), cpu.get((dataset, s.label)),
                            memory.get((dataset, s.label))))
                for s in SETUPS if (dataset, s.label) in files]
        sections.append((f"{set_name} ({len(truths[dataset])})", rows))
    both = [(s, metrics([v for d in SETS for v in files[(d, s.label)].values()],
                        [c for d in SETS for c in cpu[(d, s.label)]] if all((d, s.label) in cpu for d in SETS) else None,
                        max(memory[(d, s.label)] for d in SETS) if all((d, s.label) in memory for d in SETS) else None))
            for s in SETUPS if all((d, s.label) in files for d in SETS)]

    cpu_note = (f"CPU s / file: the same {CPU_SAMPLE} scans and photos per dataset for every setup, read with the laptop "
                "lightly loaded; it includes Tesseract's and the Claude CLI's own processes.\n"
                "Peak RAM / worker: the most memory one worker held, its own process plus the program it started for "
                "that contract (Tesseract or the Claude CLI); N workers need about N times this.")
    tables = [
        metric_table("1. All setups on the scans and photos of both datasets (973): accuracy, check load and cost",
                     [("Both datasets", both)], SUMMARY_COLUMNS,
                     caption="[bold green]* best[/] / [red]worst[/] in the column.  Fully correct: all 9 fields right "
                             "(yellow or not).  Contracts with nothing to check: no yellow cell.\n" + cpu_note),
        metric_table("2. Correct values per field", sections, CORRECT_COLUMNS),
        unmarked_table(files, truths),
    ]
    tables = [t for t in tables if t]

    # Never cut columns short: widen the output to fit the widest table, also for comparison.txt.
    wide = console.options.update(max_width=10_000)
    console.width = max([console.width] + [console.measure(t, options=wide).maximum for t in tables])
    for t in tables:
        console.print(t)
        console.print()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    console.save_text(str(output_path))  # plain-text copy, without colours
    console.print(f"[dim]Saved: {output_path}[/dim]", soft_wrap=True)


if __name__ == "__main__":
    comparison()
