"""Several setups side by side on exactly the same contracts: benchmark runs (`runs/<key>`) and website accuracy
reports (`contract-pdf-generator/accuracy/<folder>`), on the files the first one read (a partial run, or only the
scans and photos). Scored with the website's own rules (compare_accuracy.score).

From ocr-benchmark, in Git Bash, with BENCH_DATASET as for the runs:
    PYTHONPATH=. .venv/Scripts/python tools/compare_runs.py "Rapid first=rapid_second-img" \
        "Tesseract first=tesseract_second-stamp" "Tesseract first + Claude=../contract-pdf-generator/accuracy/<folder>"

A website report may add `+<job.json>`: a later rerun of some of its files, whose rows replace theirs (same layout as
a report's job.json: job, niks).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import bench.score as bs
from bench.paths import DATASET
from compare_accuracy import CORRECT, FIELDS, UNDECIDED, WRONG, score

TOKEN_KEYS = ("masuk", "cache_tulis", "cache_baca", "keluar")
WEB_TOKEN_KEYS = ("input", "cacheWrite", "cacheRead", "output")


def bench_run(key: str, truths: dict) -> dict:
    """name -> (Scored, tokens, Claude calls, text sent, seconds, CPU seconds)."""
    folder = bs.RUNS / key
    files = {r["name"]: r for r in bs.read_jsonl(folder / "files.jsonl")}
    rows = {r["name"]: r for r in bs.read_jsonl(folder / "rows.jsonl")}
    out = {}
    for name, record in files.items():
        if name not in truths:
            continue
        row = rows.get(name)
        file = None
        if row and not row.get("error"):
            file = {"state": "done", "row": {"fields": row["fields"], "decision": row["decision"]}}
        log = (((record.get("reply") or {}).get("result") or {}).get("log")) or {}
        used = log.get("token_llm") or {}
        calls = log.get("panggilan_llm") or []
        out[name] = (score(truths[name], file, {truths[name].path: row.get("nik")} if row else {}),
                     sum(int(used.get(k) or 0) for k in TOKEN_KEYS), len(calls), "teks" in calls,
                     record["seconds"], record["cpu_s"] + record.get("child_cpu_s", 0.0))
    return out


def website_report(spec: str, truths: dict) -> dict:
    folder, _, patch = spec.partition("+")
    saved = json.loads((Path(folder) / "job.json").read_text(encoding="utf-8"))
    files = {f["path"].split("/")[-1]: f for f in saved["job"]["files"]}
    niks = dict(saved["niks"])
    if patch:
        rerun = json.loads(Path(patch).read_text(encoding="utf-8"))
        files.update({f["path"].split("/")[-1]: f for f in rerun["job"]["files"]})
        niks.update(rerun["niks"])
    out = {}
    for name, file in files.items():
        if name not in truths:
            continue
        calls = (file.get("row") or {}).get("claudeCalls") or []
        tokens = sum(int((c.get("tokens") or {}).get(k) or 0) for c in calls for k in WEB_TOKEN_KEYS)
        out[name] = (score(truths[name], file, niks), tokens, len(calls), any(c["kind"] == "text" for c in calls),
                     file.get("seconds"), None)
    return out


def main() -> None:
    columns = [arg.split("=", 1) for arg in sys.argv[1:]]
    truths = bs.load_truth()
    runs = {label: (website_report(src, truths) if "accuracy" in src else bench_run(src, truths)) for label, src in columns}
    first = next(iter(runs.values()))
    names = sorted(n for n, v in first.items() if v[0].row is not None)
    missing = {label: sum(n not in run for n in names) for label, run in runs.items()}
    print(f"{len(names)} contracts read by the first setup ({DATASET})")
    if any(missing.values()):
        print(f"  not read by: {missing}")

    width = max(21, *(len(label) + 2 for label in runs))
    print(f"{'':30}" + "".join(f"{label:>{width}}" for label in runs))

    def line(label: str, values: list) -> None:
        print(f"{label:30}" + "".join(f"{v:>{width}}" for v in values))

    picked = {label: [run[n] for n in names if n in run] for label, run in runs.items()}
    line("Fully correct", [sum(s.all_correct for s, *_ in p) for p in picked.values()])
    line("Nothing to check", [sum(s.ready for s, *_ in p) for p in picked.values()])
    line("Wrong, not marked (cells)", [sum(c["verdict"] != CORRECT and not c["flagged"] for s, *_ in p
                                           for c in s.cells.values()) for p in picked.values()])
    for field in FIELDS:
        cells = {label: [s.cells[field] for s, *_ in p if field in s.cells] for label, p in picked.items()}
        line(f"  {field}", [f"{sum(c['verdict'] == CORRECT for c in cs)} ({sum(c['verdict'] == WRONG for c in cs)} wr, "
                            f"{sum(c['flagged'] for c in cs)} y)" for cs in cells.values()])
        if field == "signature":
            line("    undecided", [sum(c["verdict"] == UNDECIDED for c in cs) for cs in cells.values()])
    line("Files that sent text to Claude", [sum(t for *_, t, _s, _c in p) for p in picked.values()])
    line("Claude calls", [sum(c for *_, c, _t, _s, _cpu in p) for p in picked.values()])
    line("Claude tokens", [f"{sum(tok for _s, tok, *_ in p) / 1e6:.2f} M" for p in picked.values()])
    secs = [[s for *_, s, _cpu in p if s is not None] for p in picked.values()]
    line("Seconds per file (1 worker)", [f"{statistics.mean(s):.1f}" if s else "-" for s in secs])
    cpu = [[c for *_, c in p if c is not None] for p in picked.values()]
    line("CPU seconds per file", [f"{statistics.mean(c):.1f}" if c else "-" for c in cpu])

    labels = list(runs)
    for other in labels[1:]:
        a, b = runs[labels[0]], runs[other]
        only_a = [n for n in names if n in b and a[n][0].all_correct and not b[n][0].all_correct]
        only_b = [n for n in names if n in b and b[n][0].all_correct and not a[n][0].all_correct]
        miss_b = Counter(f for n in only_a for f, c in b[n][0].cells.items() if c["verdict"] != CORRECT)
        miss_a = Counter(f for n in only_b for f, c in a[n][0].cells.items() if c["verdict"] != CORRECT)
        print(f"\nFully correct only with {labels[0]}: {len(only_a)} (the other misses {dict(miss_b)}); "
              f"only with {other}: {len(only_b)} (the other misses {dict(miss_a)})")


if __name__ == "__main__":
    main()
