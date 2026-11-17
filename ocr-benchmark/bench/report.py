"""Write results/summary.md (the tables to share) and results/engines.csv from results/summary.json.

    .venv/Scripts/python -m bench.report
"""

from __future__ import annotations

import csv
import json

from bench.paths import RESULTS

FIELD_LABEL = {"name": "Name", "nik": "NIK", "position": "Position", "start": "Start", "end": "End",
               "months": "Months", "signature": "Signature", "vendor": "Vendor", "entity": "Entity"}
OCR_FIELDS = ["name", "nik", "position", "start", "end", "months", "signature"]
GROUP_LABEL = {"scan:color": "Scan, colour", "scan:gray": "Scan, grey", "scan:bw": "Scan, black-and-white (1-bit)",
               "photo:clean": "Photo, clean", "photo:clutter": "Photo, objects on the desk", "photo:blurry": "Photo, slightly blurry"}


def pct(a: float, b: float, digits: int = 1) -> str:
    return f"{100 * a / b:.{digits}f}%" if b else "-"


def num(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:,.{digits}f}"


def table(head: list[str], rows: list[list[str]], align: str | None = None) -> str:
    align = align or "l" + "r" * (len(head) - 1)
    sep = ["---:" if a == "r" else "---" for a in align]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def engine_rows(engines: dict, scope: str) -> list[list[str]]:
    rows = []
    for e in engines.values():
        s = e[scope]
        r = e["resources"]
        img_t = e["timing"]["image"]
        rows.append([
            e["label"],
            f"{s['all_correct']} / {s['files']} ({pct(s['all_correct'], s['files'])})",
            pct(s["values_correct"], s["values"]),
            str(s["wrong_unmarked_values"]),
            f"{s['no_yellow']} ({s['no_yellow_but_wrong']} wrong)",
            num(img_t["mean_s"]),
            num(cpu_per_image(e)),
            num(r["files_per_hour"], 0),
            num(memory_per_worker(r), 0),
        ])
    return rows


def cpu_per_image(e: dict) -> float | None:
    """CPU-seconds for one scan or photo: measured per process when available, else machine-wide minus idle."""
    r = e["resources"]
    return r.get("cpu_s_per_image_file") or r["cpu_s_per_file"]


def memory_per_worker(r: dict) -> float | None:
    """A worker's peak memory, plus the peak of the Tesseract program it runs beside it."""
    return (r["worker_peak_mb"] or 0) + (r.get("tesseract_process_peak_mb") or 0) or None


MODEL_PARTS = {"tesseract_fast": ["tesseract_fast (ind+eng)"], "tesseract_best": ["tesseract_best (ind+eng)"],
               "doctr_crnn": ["doctr db_resnet50-79bd7d70", "doctr crnn_vgg16_bn-0417f351"],
               "doctr_parseq": ["doctr db_resnet50-79bd7d70", "doctr parseq-56125471"],
               "easyocr": ["easyocr craft_mlt_25k", "easyocr latin_g2"]}


def model_mb(e: dict, sizes: dict) -> float | None:
    """The models this engine loads (the folder may hold other variants too)."""
    base = e["key"].split("-")[0]
    known = sizes.get("models_mb", {})
    parts = MODEL_PARTS.get(base)
    if parts is None and base.startswith("rapid_v6_"):
        tier = base.removeprefix("rapid_v6_")
        parts = [f"rapidocr PP-OCRv6_det_{tier}", f"rapidocr PP-OCRv6_rec_{tier}"]
    if parts and all(x in known for x in parts):
        return sum(known[x] for x in parts)
    return e["resources"]["model_mb"]


def conditions(e: dict) -> str:
    r = e["resources"]
    return f"{r['workers']}x{r['threads_per_worker']}, {r.get('priority', 'normal')}"


def field_matrix(engines: dict, key: str) -> str:
    head = ["Engine", *[FIELD_LABEL[f] for f in OCR_FIELDS]]
    rows = []
    for e in engines.values():
        cells = []
        for f in OCR_FIELDS:
            c = e[key][f]
            cells.append(f"{c['correct']} ({c['wrong_unmarked']})")
        rows.append([e["label"], *cells])
    return table(head, rows)


def field_detail(engines: dict, key: str, field: str) -> str:
    head = ["Engine", "Correct", "Wrong value", "Not found", "Undecided", "Marked yellow", "Wrong, not marked"]
    rows = []
    for e in engines.values():
        c = e[key][field]
        rows.append([e["label"], f"{c['correct']} / {c['n']}", str(c["wrong"]), str(c["missing"]), str(c["undecided"]),
                     str(c["marked"]), str(c["wrong_unmarked"])])
    return table(head, rows)


def main() -> None:
    data = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    sizes = json.loads((RESULTS / "install_size.json").read_text(encoding="utf-8")) if (RESULTS / "install_size.json").exists() else {}
    engines = data["engines"]
    full = {k: v for k, v in engines.items() if v["complete"]}
    screen = {k: v for k, v in engines.items() if not v["complete"]}
    md: list[str] = ["# OCR engine benchmark: 800 synthetic PKWT contracts", ""]

    any_e = next(iter(engines.values()))
    m = dict(any_e["machine"])
    try:  # the processor's marketing name (platform.processor() gives only the family on Windows)
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            m["cpu"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    md += [
        "Every engine reads the same contracts through the production extractor pipeline (same page selection, "
        "preprocessing, patterns, NIK checks and signature analysis); only the OCR engine changes. **Claude is off**: "
        "everything is read on the laptop, so these numbers are the local-only baseline, lower than the website with "
        "Claude helping. Scoring and yellow marks use the website's own code.",
        "",
        f"Machine: {m['cpu']}, {m['cores']} cores / {m['threads']} threads, {m['ram_gb']} GB RAM, {m['os']}. "
        "Every engine runs on CPU only. Full runs: 3 worker processes x 2 threads each at below-normal priority, "
        "while the laptop was in normal use, so wall-clock times carry some noise; CPU-seconds are measured per "
        "process (Tesseract's own program included) and are not affected by other apps.",
        "",
        "Digital PDFs (327 of 800) are read from their text layer, so they come out the same whatever the engine; "
        "the engines differ on the 473 scans and phone photos.",
        "",
    ]
    findings = RESULTS / "findings.md"
    if findings.exists():
        md += [findings.read_text(encoding="utf-8").strip(), ""]

    if full:
        md += ["## 1. Headline: all 800 contracts", "",
               table(["Engine", "Files fully correct", "Values correct", "Wrong, not marked", "Files with no yellow",
                      "s per scan/photo", "CPU-s per scan/photo", "Files per hour", "Peak MB per worker"],
                     engine_rows(full, "overall")), "",
               "## 2. Scans and photos only (473 contracts)", "",
               table(["Engine", "Files fully correct", "Values correct", "Wrong, not marked", "Files with no yellow",
                      "s per scan/photo", "CPU-s per scan/photo", "Files per hour", "Peak MB per worker"],
                     engine_rows(full, "image")), "",
               "### Per field, scans and photos: correct (wrong and not marked)", "",
               field_matrix(full, "fields_image"), ""]
        for f in OCR_FIELDS:
            md += [f"#### {FIELD_LABEL[f]} (scans and photos)", "", field_detail(full, "fields_image", f), ""]
        groups = sorted({g for e in full.values() for g in e["fields_by_group"] if g != "digital"})
        md += ["### Files fully correct by scan and photo type", "",
               table(["Engine", *[GROUP_LABEL.get(g, g) for g in groups]],
                     [[e["label"], *[f"{_all_correct(e, g)}" for g in groups]] for e in full.values()]), ""]
        agree = data.get("agreement_with_baseline", {})
        rows = []
        for k, per in agree.items():
            if k in full:
                for field in ("nik", "position", "start", "end"):
                    a = per[field]
                    rows.append([full[k]["label"], FIELD_LABEL[field], str(a["image_files"]),
                                 f"{a['agree']} ({pct(a['agree'], a['image_files'])})", str(a["agree_wrong"]),
                                 str(a["yellow"]), str(a["yellow_same_correct"]), str(a["yellow_same_wrong"])])
        if rows:
            md += ["## 3. A second local reader beside Tesseract", "",
                   "On the 473 scans and photos: how often the engine reads exactly the same value as today's "
                   "Tesseract, and whether that shared value is ever wrong. The last three columns are the cells "
                   "Tesseract leaves yellow (Perlu dicek) without Claude, and how many of them the second engine "
                   "confirms with the same value: today the website sends a crop of the NIK line to Claude for this.", "",
                   table(["Engine", "Field", "Scans and photos", "Same value as Tesseract", "Same but wrong",
                          "Tesseract yellow", "Confirmed, right", "Confirmed, wrong"], rows, "llrrrrrr"), ""]
        md += ["## 4. Resources", "",
               table(["Engine", "Wall time (800)", "Files per hour", "Mean CPU %", "CPU-s per file", "OCR calls per scan/photo",
                      "s per OCR call", "Peak MB, Python worker", "Peak MB, Tesseract program", "Pool peak MB",
                      "Start-up s", "Model MB"],
                     [[e["label"], f"{e['resources']['wall_s'] / 60:.0f} min", num(e["resources"]["files_per_hour"], 0),
                       num(e["resources"]["cpu_mean_pct"], 0), num(e["resources"]["cpu_s_per_file"]),
                       num(e["ocr"]["calls_per_image_file"]), num(e["ocr"]["ocr_s_per_call"], 2),
                       num(e["resources"]["worker_peak_mb"], 0), num(e["resources"].get("tesseract_process_peak_mb"), 0),
                       num(e["resources"]["pool_peak_mb"], 0),
                       num(e["resources"]["engine_start_s"]), num(model_mb(e, sizes))] for e in full.values()]), ""]
    if sizes:
        md += ["### Installation size beyond today's extractor", "",
               table(["Engine family", "Extra packages", "MB"],
                     [[fam, ", ".join(sizes[fam]["packages"]), num(sizes[fam]["packages_mb"])]
                      for fam in ("tesseract", "rapidocr", "doctr", "easyocr") if fam in sizes], "llr"), ""]
    if screen:
        md += ["## 5. Screening: every variant on the same 60 random scans and photos", "",
               "Speed columns are only roughly comparable here: the runs had different worker counts and "
               "priorities (last column), and the earlier Tesseract samples count CPU machine-wide, not per process. "
               "Section 4 has the like-for-like numbers.", "",
               table(["Variant", "Files fully correct", "Values correct", "Wrong, not marked", "Files with no yellow",
                      "s per scan/photo", "CPU-s per scan/photo", "Files per hour", "Peak MB per worker",
                      "Workers x threads, priority"],
                     [[*row, conditions(e)] for row, e in zip(engine_rows(screen, "overall"), screen.values())],
                     "lrrrrrrrrl"), "",
               "Each sample run beside Tesseract on exactly the same contracts (runs stopped at the time limit cover "
               "fewer than 60):", "",
               table(["Variant", "Contracts", "Fully correct", "Fully correct, Tesseract", "Wrong, not marked",
                      "Wrong, not marked, Tesseract"],
                     [[e["label"], str(e["same_files"]["files"]), str(e["same_files"]["all_correct"]),
                       str(e["baseline_same_files"]["all_correct"]), str(e["same_files"]["wrong_unmarked_values"]),
                       str(e["baseline_same_files"]["wrong_unmarked_values"])]
                      for e in screen.values() if "same_files" in e]), "",
               "Per field: correct (wrong and not marked)", "", field_matrix(screen, "fields"), ""]
    (RESULTS / "summary.md").write_text("\n".join(md), encoding="utf-8")

    with (RESULTS / "engines.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["engine", "label", "files", "all_correct", "values_correct_pct", "wrong_unmarked", "no_yellow",
                    "image_all_correct", "image_files", "s_per_image_file", "cpu_s_per_file", "files_per_hour",
                    "worker_peak_mb", "pool_peak_mb", "model_mb", "start_s"])
        for k, e in engines.items():
            o, i, r = e["overall"], e["image"], e["resources"]
            w.writerow([k, e["label"], o["files"], o["all_correct"], round(100 * o["values_correct"] / o["values"], 2),
                        o["wrong_unmarked_values"], o["no_yellow"], i["all_correct"], i["files"],
                        round(e["timing"]["image"]["mean_s"] or 0, 2), round(r["cpu_s_per_file"] or 0, 2),
                        round(r["files_per_hour"] or 0), r["worker_peak_mb"], r["pool_peak_mb"], round(model_mb(e, sizes) or 0, 1),
                        round(r["engine_start_s"] or 0, 1)])
    print(f"wrote {RESULTS / 'summary.md'}")


def _all_correct(engine: dict, group: str) -> str:
    table_ = engine["fields_by_group"].get(group)
    if not table_:
        return "-"
    n = next(iter(table_.values()))["n"]
    # files fully correct are not in the per-field table; count from the per-group files summary when present
    full = engine.get("files_by_group", {}).get(group)
    return f"{full['all_correct']} / {n}" if full else "-"


if __name__ == "__main__":
    main()
