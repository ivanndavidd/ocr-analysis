"""Two runs side by side on exactly the files the first one read (for a partial run): RapidOCR as the main reader
against the product's Tesseract + second reader, per field, with time, CPU and memory.

From ocr-benchmark, in Git Bash (BENCH_DATASET as for the runs):
    PYTHONPATH=. BENCH_DATASET=output-v2 .venv/Scripts/python tools/compare_engines.py rapid_v6_small-v2-img tesseract_second-v2-stamp
"""

import statistics
import sys

import bench.score as bs
from compare_accuracy import CORRECT, FIELDS, UNDECIDED, WRONG

rapid_key, tess_key = sys.argv[1], sys.argv[2]
truths = bs.load_truth()
runs = {k: bs.score_run(k, truths) for k in (rapid_key, tess_key)}
names = sorted(n for n, s in runs[rapid_key]["_scored"].items() if s.row is not None)
files = {k: {r["name"]: r for r in bs.read_jsonl(bs.RUNS / k / "files.jsonl")} for k in runs}
print(f"{len(names)} scans and photos read by both\n")

head = f"{'':28}{'RapidOCR alone':>16}{'Tesseract+second':>18}"
print(head)


def line(label, a, b):
    print(f"{label:28}{a:>16}{b:>18}")


picked = {k: [runs[k]["_scored"][n] for n in names] for k in runs}
line("Fully correct", *(sum(s.all_correct for s in picked[k]) for k in runs))
line("Nothing to check", *(sum(s.ready for s in picked[k]) for k in runs))
line("Wrong, not marked (cells)", *(sum(c["verdict"] != CORRECT and not c["flagged"] for s in picked[k]
                                         for c in s.cells.values()) for k in runs))
for field in FIELDS:
    ok = [sum(s.cells[field]["verdict"] == CORRECT for s in picked[k]) for k in runs]
    wrong = [sum(s.cells[field]["verdict"] == WRONG for s in picked[k]) for k in runs]
    marked = [sum(s.cells[field]["flagged"] for s in picked[k]) for k in runs]
    extra = ""
    if field == "signature":
        und = [sum(s.cells[field]["verdict"] == UNDECIDED for s in picked[k]) for k in runs]
        extra = f"  undecided {und[0]} vs {und[1]}"
    line(f"  {field}", f"{ok[0]} ({wrong[0]} wr, {marked[0]} y)", f"{ok[1]} ({wrong[1]} wr, {marked[1]} y)")
    if extra:
        print(" " * 28 + extra)
secs = [statistics.mean(files[k][n]["seconds"] for n in names) for k in runs]
cpu = [statistics.mean(files[k][n]["cpu_s"] + files[k][n].get("child_cpu_s", 0.0) for n in names) for k in runs]
peak = [max(files[k][n]["peak_mb"] for n in names) for k in runs]
line("Seconds per file (1 worker)", f"{secs[0]:.1f}", f"{secs[1]:.1f}")
line("CPU seconds per file", f"{cpu[0]:.1f}", f"{cpu[1]:.1f}")
line("Worker peak memory (MB)", f"{peak[0]:.0f}", f"{peak[1]:.0f}")

# where they differ: files one gets fully right and the other does not
only_r = [n for n in names if runs[rapid_key]["_scored"][n].all_correct and not runs[tess_key]["_scored"][n].all_correct]
only_t = [n for n in names if runs[tess_key]["_scored"][n].all_correct and not runs[rapid_key]["_scored"][n].all_correct]
print(f"\nFully correct only with RapidOCR: {len(only_r)}; only with Tesseract+second: {len(only_t)}")
for label, group, k in (("RapidOCR wins", only_r, tess_key), ("Tesseract wins", only_t, rapid_key)):
    fields = [f for n in group for f, c in runs[k]["_scored"][n].cells.items() if c["verdict"] != CORRECT]
    from collections import Counter
    print(f"  {label}: the other engine misses {dict(Counter(fields))}")
