"""Score every engine's run against the ground truth and write the summary.

    .venv/Scripts/python -m bench.score [engine-key ...]      (default: every run in runs/ with rows.jsonl)

Each cell is scored with the same code as the website accuracy reports (contract-pdf-generator/compare_accuracy.py:
correct, wrong value, not found, undecided for the signature, and whether the website would have marked it yellow).
Writes results/summary.json (every number), results/summary.md (the tables to share), results/engines.csv,
results/fields.csv, and runs/<key>/scored.csv (per contract and field; synthetic data only).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from bench.paths import GENERATOR, GROUND_TRUTH, RESULTS, RUNS

sys.path.insert(0, str(GENERATOR))
from compare_accuracy import CORRECT, FIELDS, MISSING, UNDECIDED, WRONG, Truth, score  # noqa: E402
from copy_to_onedrive import period_folder, safe_folder  # noqa: E402

FORMS = ["digital", "scan", "photo"]
IMAGE_FORMS = {"scan", "photo"}
OCR_FIELDS = ["name", "nik", "position", "start", "end", "months", "signature"]  # what OCR can change
BASELINE = "tesseract_fast"


def load_truth() -> dict[str, Truth]:
    records = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    truths = {}
    for r in records:
        folder = f"{safe_folder(r['meta']['company'])}/{safe_folder(period_folder(r))}"
        truths[r["file_name"]] = Truth(f"{folder}/{r['file_name']}", r)
    return truths


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pctile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def subgroup(truth: Truth) -> str:
    meta = truth.record["meta"]
    if meta["form"] == "scan":
        return f"scan:{meta.get('scan_mode')}"
    if meta["form"] == "photo":
        return "photo:blurry" if meta.get("photo_blurry") else ("photo:clutter" if meta.get("photo_clutter") else "photo:clean")
    return "digital"


def score_run(key: str, truths: dict[str, Truth]) -> dict:
    folder = RUNS / key
    files = {r["name"]: r for r in read_jsonl(folder / "files.jsonl")}
    rows = {r["name"]: r for r in read_jsonl(folder / "rows.jsonl")}
    sessions = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    sessions = sessions if isinstance(sessions, list) else [sessions]
    names = sorted(n for n in files if n in truths)

    scored = {}
    for name in names:
        truth, row = truths[name], rows.get(name)
        file = None
        if row and not row.get("error"):
            file = {"state": "done", "row": {"fields": row["fields"], "decision": row["decision"]},
                    "seconds": files[name]["seconds"]}
        scored[name] = score(truth, file, {truth.path: row.get("nik")} if row else {})

    def tally(group: list[str]) -> dict:
        out = {}
        for field in FIELDS:
            c = Counter()
            for n in group:
                cell = scored[n].cells.get(field)
                if cell is None:
                    c[MISSING] += 1
                    continue
                c[cell["verdict"]] += 1
                c["marked"] += cell["flagged"]
                c["wrong_unmarked"] += cell["verdict"] != CORRECT and not cell["flagged"]
            out[field] = {"n": len(group), **{k: c[k] for k in (CORRECT, WRONG, MISSING, UNDECIDED, "marked", "wrong_unmarked")}}
        return out

    def files_summary(group: list[str]) -> dict:
        s = [scored[n] for n in group]
        decisions = Counter(x.row["decision"] for x in s if x.row is not None)
        return {
            "files": len(group),
            "read": sum(x.row is not None for x in s),
            "all_correct": sum(x.all_correct for x in s),
            "no_yellow": sum(x.ready for x in s),
            "no_yellow_but_wrong": sum(x.ready and not x.all_correct for x in s),
            "wrong_unmarked_values": sum(1 for x in s for c in x.cells.values() if c["verdict"] != CORRECT and not c["flagged"]),
            "values_correct": sum(1 for x in s for c in x.cells.values() if c["verdict"] == CORRECT),
            "values": len(group) * len(FIELDS),
            "decisions": dict(decisions),
            "auto_wrong": sum(1 for x in s if x.row is not None and x.row["decision"] == "auto" and not x.all_correct),
        }

    by_form = {f: [n for n in names if truths[n].form == f] for f in FORMS}
    image = [n for n in names if truths[n].form in IMAGE_FORMS]
    groups = defaultdict(list)
    for n in names:
        groups[subgroup(truths[n])].append(n)

    # time and resources
    recs = [files[n] for n in names]
    def secs(group):  # noqa: E306
        return [files[n]["seconds"] for n in group]
    timing = {
        form: {"mean_s": statistics.mean(secs(g)) if g else None, "median_s": statistics.median(secs(g)) if g else None,
               "p95_s": pctile(secs(g), 0.95) if g else None, "max_s": max(secs(g)) if g else None}
        for form, g in [*by_form.items(), ("image", image), ("all", names)]
    }
    img_recs = [files[n] for n in image]
    ocr = {
        "calls_per_image_file": statistics.mean(r["ocr_calls"] for r in img_recs) if img_recs else 0,
        "ocr_s_per_image_file": statistics.mean(r["ocr_s"] for r in img_recs) if img_recs else 0,
        "ocr_s_per_call": (sum(r["ocr_s"] for r in img_recs) / max(sum(r["ocr_calls"] for r in img_recs), 1)),
        "ocr_s_per_megapixel": (sum(r["ocr_s"] for r in img_recs) / max(sum(r["ocr_megapixels"] for r in img_recs), 1e-9)),
        "ocr_share_of_time": (sum(r["ocr_s"] for r in img_recs) / max(sum(r["seconds"] for r in img_recs), 1e-9)),
    }
    wall = sum(s["wall_s"] for s in sessions)
    samples = [x for s in sessions for x in s.get("samples", [])]
    threads = sessions[-1]["machine"]["threads"]
    idle = statistics.mean(s.get("idle_cpu_pct", 0.0) for s in sessions)
    busy_cpu_s = sum(max(x["cpu"] - idle, 0.0) / 100 * threads for x in samples)  # one sample per second
    init = [r["init_s"] for r in recs if "init_s" in r]
    resources = {
        "wall_s": wall,
        "files_per_hour": len(names) / wall * 3600 if wall else None,
        "workers": sessions[-1]["workers"],
        "threads_per_worker": sessions[-1]["threads_per_worker"],
        "cpu_mean_pct": statistics.mean(x["cpu"] for x in samples) if samples else None,
        "cpu_idle_pct_before": idle,
        # CPU the run's own processes used (Tesseract's included when measured): not disturbed by other apps
        "cpu_s_per_file": (statistics.mean(r["cpu_s"] + r.get("child_cpu_s", 0.0) for r in recs) if recs else None)
        if all("child_cpu_s" in r for r in recs) else busy_cpu_s / len(names) if names else None,
        "cpu_s_per_image_file": statistics.mean(r["cpu_s"] + r.get("child_cpu_s", 0.0) for r in img_recs)
        if img_recs and all("child_cpu_s" in r for r in img_recs) else None,
        "cpu_measured": "per process" if all("child_cpu_s" in r for r in recs) else "machine-wide minus idle",
        "cpu_s_per_file_machine": busy_cpu_s / len(names) if names else None,
        "tesseract_process_peak_mb": max((r.get("child_peak_mb", 0.0) for r in recs), default=0.0) or None,
        "paused_for_memory_s": sum(s.get("paused_for_memory_s", 0.0) for s in sessions),
        "priority": sessions[-1].get("priority", "normal"),
        "pool_peak_mb": max((x["pool_mb"] for x in samples), default=None),
        "pool_mean_mb": statistics.mean(x["pool_mb"] for x in samples) if samples else None,
        "worker_peak_mb": max((r["peak_mb"] for r in recs), default=None),
        "ram_used_peak_pct": max((x["ram_used_pct"] for x in samples), default=None),
        "engine_start_s": statistics.mean(init) if init else None,
        "model_mb": sum(sessions[-1]["model_files"].values()) / 2**20,
        "errors": sum(1 for r in recs if r["error"]),
    }

    # per contract and field, for the drill-down
    with (folder / "scored.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "form", "group", "field", "expected", "got", "verdict", "marked", "seconds", "ocr_calls"])
        for n in names:
            for field, cell in scored[n].cells.items():
                w.writerow([n, truths[n].form, subgroup(truths[n]), field, cell["expected"], cell["got"], cell["verdict"],
                            cell["flagged"], files[n]["seconds"], files[n]["ocr_calls"]])

    return {
        "key": key,
        "label": sessions[-1]["label"],
        "family": sessions[-1]["family"],
        "versions": sessions[-1]["versions"],
        "machine": sessions[-1]["machine"],
        "complete": len(names) == len(truths),
        "overall": files_summary(names),
        "image": files_summary(image),
        "by_form_files": {f: files_summary(g) for f, g in by_form.items()},
        "fields": tally(names),
        "fields_image": tally(image),
        "fields_by_form": {f: tally(g) for f, g in by_form.items()},
        "fields_by_group": {g: tally(ns) for g, ns in sorted(groups.items())},
        "files_by_group": {g: files_summary(ns) for g, ns in sorted(groups.items())},
        "timing": timing,
        "ocr": ocr,
        "resources": resources,
        "_scored": scored,  # dropped before writing
    }


def agreement(runs: dict[str, dict], truths: dict[str, Truth], base: str) -> dict:
    """A second reader beside the baseline: on scans and photos, how often both read the same value, and how often
    that shared value is right. If it is never wrong when they agree, agreement can turn a cell green without
    Claude."""
    out = {}
    # beside full runs the baseline's full run; beside a sample, the baseline's run on that same sample
    full_base = runs.get(base) if runs.get(base, {}).get("complete") else None
    sample_base = runs.get(f"{base}-screen")
    for key, run in runs.items():
        if key.split("-")[0] == base:
            continue
        b = full_base if run["complete"] else sample_base
        if b is not None:
            out[key] = _agree(b["_scored"], run["_scored"], truths)
    return out


def _agree(b: dict, o: dict, truths: dict[str, Truth]) -> dict:
    """Per field, on the scans and photos both read. `yellow_*`: the baseline's yellow cells, and how many of them
    the other engine read the same (right or wrong): those a second local reader could confirm."""
    if True:
        per_field = {}
        for field in ["nik", "name", "position", "start", "end", "months"]:
            agree = agree_right = disagree = either_right = 0
            yellow = yellow_agree_right = yellow_agree_wrong = 0
            n = 0
            for name in b:
                if name not in o or truths[name].form not in IMAGE_FORMS:
                    continue
                cb, co = b[name].cells.get(field), o[name].cells.get(field)
                if cb is None or co is None:
                    continue
                n += 1
                same = cb["got"] not in (None, "") and cb["got"] == co["got"]
                if cb["flagged"]:
                    yellow += 1
                    yellow_agree_right += same and cb["verdict"] == CORRECT
                    yellow_agree_wrong += same and cb["verdict"] != CORRECT
                if same:
                    agree += 1
                    agree_right += cb["verdict"] == CORRECT
                else:
                    disagree += 1
                    either_right += CORRECT in (cb["verdict"], co["verdict"])
            per_field[field] = {"image_files": n, "agree": agree, "agree_correct": agree_right,
                                "agree_wrong": agree - agree_right, "disagree": disagree,
                                "disagree_one_correct": either_right, "yellow": yellow,
                                "yellow_same_correct": yellow_agree_right, "yellow_same_wrong": yellow_agree_wrong}
        return per_field


def subset(run: dict, names: list[str]) -> dict:
    """The run's file counts on just these contracts, to compare a sample run with the baseline on the same files."""
    sc = [run["_scored"][n] for n in names if n in run["_scored"]]
    return {"files": len(sc), "all_correct": sum(x.all_correct for x in sc), "no_yellow": sum(x.ready for x in sc),
            "wrong_unmarked_values": sum(1 for x in sc for c in x.cells.values() if c["verdict"] != CORRECT and not c["flagged"]),
            "fields": {f: sum(x.cells.get(f, {}).get("verdict") == CORRECT for x in sc) for f in FIELDS}}


def main() -> None:
    truths = load_truth()
    keys = sys.argv[1:] or sorted(p.name for p in RUNS.iterdir() if (p / "rows.jsonl").exists() and "smoke" not in p.name)
    runs = {k: score_run(k, truths) for k in keys}
    agree = agreement(runs, truths, BASELINE)
    # each sample run beside the baseline's full run on exactly the same contracts
    if runs.get(BASELINE, {}).get("complete"):
        for key, run in runs.items():
            if not run["complete"]:
                names = list(run["_scored"])
                run["baseline_same_files"] = subset(runs[BASELINE], names)
                run["same_files"] = subset(run, names)
    RESULTS.mkdir(exist_ok=True)
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_scored"} for k, v in runs.items()}
    (RESULTS / "summary.json").write_text(json.dumps({"engines": clean, "agreement_with_baseline": agree}, indent=1,
                                                     ensure_ascii=False, default=str), encoding="utf-8")
    with (RESULTS / "fields.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["engine", "scope", "field", "n", "correct", "wrong", "not_found", "undecided", "marked", "wrong_unmarked"])
        for k, r in runs.items():
            for scope, table in [("all", r["fields"]), ("scan+photo", r["fields_image"]),
                                 *[(f, r["fields_by_form"][f]) for f in FORMS]]:
                for field, c in table.items():
                    w.writerow([k, scope, field, c["n"], c[CORRECT], c[WRONG], c[MISSING], c[UNDECIDED], c["marked"],
                                c["wrong_unmarked"]])
    print(json.dumps({k: {"all_correct": v["overall"]["all_correct"], "image_all_correct": v["image"]["all_correct"],
                          "wrong_unmarked": v["overall"]["wrong_unmarked_values"],
                          "files_per_hour": round(v["resources"]["files_per_hour"] or 0)} for k, v in runs.items()},
                     indent=1))


if __name__ == "__main__":
    main()
