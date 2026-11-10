"""Run the generated contracts through the contract-OCR website and compare what it read with the ground truth.

It uses the website the Ctrl+Shift+B task starts (backend on http://127.0.0.1:3000), so the same workers, settings
and Claude login read the files, and the job also shows up on the website under "Hasil OCR terakhir". The PDFs must
already be in the website's folder: run copy_to_onedrive.py after generating. The script signs in to the website
(mock sign-in, no password) as a made-up benchmark user, BENCH_EMAIL below, so its runs are told apart in the history.

Run it with the contract-ocr venv (from the contract-ocr folder; `rich`, for the tables, comes with that venv):
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --limit 20          # a quick trial
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --form scan --form photo
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --job <job id>      # score a job still on the website
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --from <report folder>  # redo a report, no backend
    .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output-v2/ground_truth.json

Claude check: when the website has Claude switched on but its workers cannot reach it, a run would quietly read with
the text patterns only and look like a Claude run, so the script refuses to start one (--allow-without-claude starts it
anyway). A run with Claude is stopped and cancelled on the website when none of the first 10 files read reached Claude,
or 10 files in a row did not (the sign-in expired midway). With CLAUDE_MODE=off on the website, it reads with the text
patterns only, as asked. CLAUDE_BENCHMARK.md next to this script is the checklist for a full run with Claude.

Every field of the Excel row is scored against the ground truth: correct, wrong value, not found (nothing read where
the contract has a value), or, for the signature, undecided. A wrong value the website did not mark for checking
(yellow) is the number that matters most: HR would not look at it. The report also shows when the job ran, how long
each file took, and Claude's tokens per call (identity text, NIK crop, signature crop) and in total.

Each run writes to contract-pdf-generator/accuracy/<time>/ (outside output/, which the generator replaces):
    report.html   the report to open in a browser or share: summary, tables, and every file's fields
    report.txt    the console report
    detail.csv    one row per file and field; tokens.csv one row per Claude call; summary.json all the numbers
    job.json      what the website returned and the ground truth it was scored on, so --from can redo the report
                  after the backend forgot the job or the generator replaced output/

Synthetic data only: the report and the CSVs hold full NIKs and names from the ground truth.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from copy_to_onedrive import period_folder, safe_folder  # noqa: E402  (the same layout the copy uses)

FIELDS = ["name", "nik", "position", "start", "end", "months", "signature", "vendor", "entity"]
FIELD_LABEL = {"name": "Employee name", "nik": "NIK", "position": "Position", "start": "Contract start",
               "end": "Contract end", "months": "Duration (months)", "signature": "Employee signature",
               "vendor": "Vendor", "entity": "Entity (ENTITAS)"}
FORMS = ["digital", "scan", "photo"]
FORM_LABEL = {"digital": "Digital PDF", "scan": "Scan", "photo": "Phone photo"}
CALLS = ["text", "nik", "signature", "pages"]
CALL_LABEL = {"text": "Identity page text", "nik": "NIK line crop", "signature": "Signature area crop",
              "pages": "Whole first and last page"}
MODES = ["ocr", "full_page"]  # the website's read modes: OCR first, or the whole first and last page to Claude (test only)


def reading_label(run: dict) -> str:
    """How the job read, for the reports."""
    if run.get("mode") == "full_page":
        return "No OCR: the whole first and last page sent to Claude (test mode)"
    return "Text patterns, with Claude helping" if run["claude"] else "Text patterns only, no Claude"
DECISIONS = {"auto": "Auto, no check needed (Otomatis)", "review": "Review", "review_priority": "Review first (prioritas)"}
TOKEN_KEYS = ["input", "cacheWrite", "cacheRead", "output"]
TOKEN_LABEL = {"input": "Input", "cacheWrite": "Cache write", "cacheRead": "Cache read", "output": "Output"}
CORRECT, WRONG, MISSING, UNDECIDED = "correct", "wrong", "missing", "undecided"
VERDICT_LABEL = {CORRECT: "Correct", WRONG: "Wrong value", MISSING: "Not found", UNDECIDED: "Undecided"}
POLL_S = 5
HEALTH_WAIT_S = 180
ACTIVE = ("queued", "running")  # a job still waiting in the website's queue or being read
# The website's mock sign-in takes any email and name; the benchmark signs in as this made-up user (example.com is a
# reserved domain), never as a real person, so its runs show up in the shared history as the benchmark's.
BENCH_EMAIL, BENCH_NAME = "accuracy-check@example.com", "Accuracy check"
CLAUDE_FIRST = 10  # stop a Claude run when none of the first this many files read reached Claude
CLAUDE_STREAK = 10  # or when this many files in a row did not


# --- website API ------------------------------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, code: str, body: dict):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.body = status, code, body


class Website:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.session: str | None = None  # the sign-in cookie, sent back on every call; never printed

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("content-type", "application/json")
        if self.session:
            request.add_header("cookie", self.session)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                for header in response.headers.get_all("set-cookie") or []:
                    cookie = SimpleCookie(header)
                    for morsel in cookie.values():
                        self.session = f"{morsel.key}={morsel.value}" if morsel.value else None
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            raise ApiError(error.code, str(payload.get("error", "http_error")), payload) from None

    def sign_in(self) -> None:
        """The website asks every caller to sign in (since 2026-11-05); the benchmark signs in as its own user."""
        self.call("POST", "/api/auth/login", {"email": BENCH_EMAIL, "name": BENCH_NAME})
        if not self.session:
            sys.exit("The website did not hand out a sign-in cookie; check the backend terminal.")

    def health(self) -> dict:
        return self.call("GET", "/api/health")


def wait_until_ready(site: Website) -> dict:
    """Signs in, then waits for the OCR workers to start and to say whether Claude helps them."""
    deadline = time.monotonic() + HEALTH_WAIT_S
    try:
        site.sign_in()
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        sys.exit(f"The website backend is not running at {site.base}. Start it with Ctrl+Shift+B in VS Code.")
    except ApiError as error:
        sys.exit(f"The website refused the benchmark sign-in: {error}")
    while True:
        try:
            health = site.health()
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            sys.exit(f"Lost the website backend at {site.base}; check the backend terminal.")
        state = health["extractor"]["state"]
        if state == "ready" and health["claude"] != "starting":
            return health
        if state == "unavailable":
            sys.exit(f"The OCR workers are not usable ({health['extractor'].get('error') or state}); check the backend terminal.")
        if time.monotonic() > deadline:
            if state == "ready":
                return health  # Claude still 'starting': the Claude check below refuses it
            sys.exit(f"The OCR workers did not start in {HEALTH_WAIT_S} s ({state}); check the backend terminal.")
        time.sleep(2)


def claude_problem(state: str, mode: str, allow_without: bool) -> str | None:
    """Why a new job must not start, given the website's Claude state, or None. 'off' is the website's own setting
    (CLAUDE_MODE=off), so the patterns-only run is what was asked for; 'unavailable' or 'starting' means Claude is
    switched on but the workers cannot reach it, and the run would quietly read without it."""
    if state == "on":
        return None
    if mode == "full_page":
        return "The no-OCR mode needs Claude, which the website does not have ready now."
    if state == "off" or allow_without:
        return None
    return (f"Claude is switched on in the website's settings but its workers cannot reach it ({state}): the run would "
            "read with the text patterns only. Sign in to Claude Code on this PC (`claude auth login`) or set "
            "ANTHROPIC_API_KEY in ocr-analysis/apps/backend/.env, restart the website, and run again. "
            "To read with the text patterns only on purpose, add --allow-without-claude.")


def reached_claude(file: dict) -> bool:
    """A file read with at least one Claude call and no failed one."""
    row = file.get("row") or {}
    return bool(row.get("claudeCalls")) and not any(r.startswith("extractor_error") for r in row.get("reasons") or [])


class ClaudeStopped(Exception):
    pass


class ClaudeWatch:
    """Watches a run that is meant to use Claude, file by file as they finish: it stops the run when none of the first
    CLAUDE_FIRST files read reached Claude, or CLAUDE_STREAK files in a row did not. With Claude working nearly every
    file makes a call (800 of 800 on 2026-11-04), so either means Claude is not answering."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.reached = 0
        self.streak = 0
        self.failures: Counter = Counter()  # the kinds of failed calls, e.g. extractor_error:cli:exit_1 (no PII)

    def update(self, job: dict) -> None:
        for file in job["files"]:
            if file["state"] != "done" or file["path"] in self.seen:
                continue
            self.seen.add(file["path"])
            if reached_claude(file):
                self.reached += 1
                self.streak = 0
                continue
            self.streak += 1
            reasons = (file.get("row") or {}).get("reasons") or []
            self.failures.update(r for r in reasons if r.startswith("extractor_error"))
        if len(self.seen) >= CLAUDE_FIRST and self.reached == 0:
            raise ClaudeStopped(f"none of the first {len(self.seen)} files read reached Claude")
        if self.streak >= CLAUDE_STREAK:
            raise ClaudeStopped(f"the last {self.streak} files read in a row did not reach Claude")

    def detail(self) -> str:
        if not self.failures:
            return "no Claude call was made"
        return "failed calls: " + ", ".join(f"{kind} x{count}" for kind, count in self.failures.most_common(3))


def wait_for_job(site: Website, job_id: str, console: Console, watch: ClaudeWatch | None = None) -> dict:
    """Poll until the job stops (queued, then running): a progress bar in a terminal, a line every 10 files otherwise.
    With `watch`, raises ClaudeStopped when Claude is not answering."""

    def fetch() -> tuple[dict, int, int, str]:
        job = site.call("GET", f"/api/jobs/{job_id}")
        if watch:
            watch.update(job)
        states = Counter(f["state"] for f in job["files"])
        if job["status"] == "queued":
            note = f"(queued behind {job.get('queueAhead', '?')} run(s))"
        else:
            note = f"({states['error']} failed)" if states["error"] else ""
        return job, states["done"] + states["error"] + states["cancelled"], len(job["files"]), note

    job, done, total, note = fetch()
    if console.is_terminal:
        columns = (TextColumn("Reading"), BarColumn(), MofNCompleteColumn(), TextColumn("files"),
                   TextColumn("{task.fields[note]}"), TimeElapsedColumn(), TextColumn("left"), TimeRemainingColumn())
        with Progress(*columns, console=console) as progress:
            task = progress.add_task("job", total=total, completed=done, note=note)
            while job["status"] in ACTIVE:
                time.sleep(POLL_S)
                job, done, total, note = fetch()
                progress.update(task, completed=done, note=note)
        return job
    started, printed = time.monotonic(), (-1, "")
    while True:
        if (done, note) != printed and (done % 10 == 0 or done == total or job["status"] == "queued"):
            print(f"  {done}/{total} files read{f' {note}' if note else ''}, {(time.monotonic() - started) / 60:.1f} min",
                  flush=True)
            printed = (done, note)
        if job["status"] not in ACTIVE:
            return job
        time.sleep(POLL_S)
        job, done, total, note = fetch()


# --- ground truth and scoring -----------------------------------------------------------------------------------------


@dataclass
class Truth:
    path: str  # root-relative, as the website names the file
    record: dict

    @property
    def form(self) -> str:
        return self.record["meta"].get("form", "?")

    @property
    def name(self) -> str:
        return self.record["file_name"]

    def expected(self) -> dict[str, object]:
        r, meta = self.record, self.record["meta"]
        signed = r.get("has_employee_signature")
        return {
            "name": r.get("employee_name"),
            "nik": r.get("employee_nik"),
            "position": r.get("position"),
            "start": r.get("contract_start"),
            "end": r.get("contract_end"),
            "months": meta.get("duration_months"),
            "signature": None if signed is None else ("YES" if signed else "NO"),
            "vendor": safe_folder(meta["company"]),
            "entity": r.get("entitas"),
        }


def load_truth(records: list[dict], forms: list[str], kelompok: str | None, limit: int | None) -> list[Truth]:
    truths = []
    for record in records:
        meta = record["meta"]
        if forms and meta.get("form") not in forms:
            continue
        if kelompok and meta.get("kelompok") != kelompok:
            continue
        folder = f"{safe_folder(meta['company'])}/{safe_folder(period_folder(record))}"
        truths.append(Truth(f"{folder}/{record['file_name']}", record))
    return truths[:limit] if limit else truths


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _loose(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def verdict(key: str, expected: object, got: object) -> str:
    """Correct, wrong, or not found; a signature left blank is undecided. An empty value where the contract has none is
    correct (no NIK printed)."""
    if got is None or got == "":
        if expected is None:
            return CORRECT
        return UNDECIDED if key == "signature" else MISSING
    if expected is None:
        return WRONG  # read a value the contract does not have
    if isinstance(expected, str) and isinstance(got, str):
        return CORRECT if _same_text(expected, got) else WRONG
    return CORRECT if expected == got else WRONG


@dataclass
class Scored:
    truth: Truth
    file: dict | None
    nik: str | None
    cells: dict[str, dict] = field(default_factory=dict)  # field -> expected, got, verdict, flagged, note

    @property
    def row(self) -> dict | None:
        return (self.file or {}).get("row") if (self.file or {}).get("state") == "done" else None

    @property
    def error(self) -> str | None:
        if self.row is not None:
            return None
        return "not in job" if self.file is None else (self.file.get("error") or self.file.get("state"))

    @property
    def seconds(self) -> float | None:
        return (self.file or {}).get("seconds")

    @property
    def calls(self) -> list[dict]:
        return [c if isinstance(c, dict) else {"kind": c, "tokens": None} for c in (self.row or {}).get("claudeCalls") or []]

    @property
    def all_correct(self) -> bool:
        return self.row is not None and all(c["verdict"] == CORRECT for c in self.cells.values())

    @property
    def ready(self) -> bool:
        return self.row is not None and not any(c["flagged"] for c in self.cells.values())


def score(truth: Truth, file: dict | None, niks: dict[str, str]) -> Scored:
    scored = Scored(truth, file, niks.get(truth.path))
    if scored.row is None:
        return scored
    for key, expected in truth.expected().items():
        cell = scored.row["fields"][key]
        got = scored.nik if key == "nik" and cell["value"] else cell["value"]  # the row holds the masked NIK only
        scored.cells[key] = {"expected": expected, "got": got, "verdict": verdict(key, expected, got),
                             "flagged": cell["check"] is not None, "note": cell["check"] or cell["basis"] or ""}
    return scored


# --- the numbers ------------------------------------------------------------------------------------------------------


def tokens_of(used: dict | None) -> dict[str, int]:
    return {k: int((used or {}).get(k, 0)) for k in TOKEN_KEYS}


def local_time(iso: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone() if iso else None


def analyse(job: dict, workers: int, gt: Path, scored: list[Scored], extra: list[str]) -> dict:
    """Every number the reports show, as plain data (also written to summary.json)."""
    n = len(scored)
    read = [s for s in scored if s.row is not None]
    started, finished = local_time(job["startedAt"]), local_time(job.get("finishedAt"))
    wall = (finished - started).total_seconds() if finished else None

    fields = {}
    for key in FIELDS:
        c = Counter()
        for s in scored:
            cell = s.cells.get(key)
            if cell is None:
                c[MISSING] += 1  # a file that was not read misses every field
                continue
            c[cell["verdict"]] += 1
            c["flagged"] += cell["flagged"]
            c["silent"] += cell["verdict"] != CORRECT and not cell["flagged"]
        fields[key] = {"total": n, **{k: c[k] for k in (CORRECT, WRONG, MISSING, UNDECIDED, "flagged", "silent")}}

    by_form = {}
    for form in FORMS:
        group = [s for s in scored if s.truth.form == form]
        if group:
            by_form[form] = {"files": len(group), "all_correct": sum(s.all_correct for s in group),
                             **{key: sum(s.cells.get(key, {}).get("verdict") == CORRECT for s in group) for key in FIELDS}}

    issues = [
        {"file": s.truth.name, "path": s.truth.path, "form": s.truth.form, "field": key, "expected": cell["expected"],
         "got": cell["got"], "verdict": cell["verdict"], "flagged": cell["flagged"], "note": cell["note"]}
        for s in read for key, cell in s.cells.items() if cell["verdict"] != CORRECT
    ]
    issues.sort(key=lambda i: (i["flagged"], FIELDS.index(i["field"]), i["file"]))  # not marked first
    loose_names = sum(1 for i in issues if i["field"] == "name" and i["verdict"] == WRONG
                      and _loose(str(i["got"])) == _loose(str(i["expected"])))

    timed = [s for s in read if s.seconds is not None]
    reading = None
    if timed:
        secs = [s.seconds for s in timed]
        reading = {
            "files": len(timed), "average": statistics.mean(secs), "median": statistics.median(secs), "max": max(secs),
            "by_form": {form: statistics.mean(s.seconds for s in timed if s.truth.form == form)
                        for form in FORMS if any(s.truth.form == form for s in timed)},
            "by_calls": {k: statistics.mean(s.seconds for s in timed if len(s.calls) == k)
                         for k in sorted({len(s.calls) for s in timed})},
            "slowest": [{"file": s.truth.name, "form": s.truth.form, "seconds": s.seconds,
                         "calls": [c["kind"] for c in s.calls]} for s in sorted(timed, key=lambda s: -s.seconds)[:5]],
        }

    per_call = {k: {"calls": 0, "unreported": 0, **{t: 0 for t in TOKEN_KEYS}} for k in CALLS}
    per_form = {form: {"files": 0, "calls": 0, "total": 0} for form in FORMS}
    grand = {t: 0 for t in TOKEN_KEYS}
    file_totals = []
    for s in read:
        for call in s.calls:
            bucket = per_call.setdefault(call["kind"], {"calls": 0, "unreported": 0, **{t: 0 for t in TOKEN_KEYS}})
            bucket["calls"] += 1
            bucket["unreported"] += call.get("tokens") is None
            for t, v in tokens_of(call.get("tokens")).items():
                bucket[t] += v
        used = tokens_of(s.row.get("tokens"))
        for t, v in used.items():
            grand[t] += v
        if s.calls:
            group = per_form[s.truth.form]
            group["files"] += 1
            group["calls"] += len(s.calls)
            group["total"] += sum(used.values())
            file_totals.append({"file": s.truth.name, "form": s.truth.form, "total": sum(used.values()),
                                "calls": [c["kind"] for c in s.calls]})
    for bucket in per_call.values():
        bucket["total"] = sum(bucket[t] for t in TOKEN_KEYS)
    total = sum(grand.values())

    decisions = Counter(s.row["decision"] for s in read)
    return {
        "run": {
            "job": job["id"], "claude": job["claude"], "mode": job.get("mode", "ocr"), "ground_truth": str(gt),
            "started": started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds") if finished else None,
            "seconds": wall, "workers": workers, "files": n, "read": len(read),
            "not_read": [{"file": s.truth.name, "error": s.error} for s in scored if s.row is None],
            "extra_in_job": len(extra), "forms": {form: sum(s.truth.form == form for s in scored) for form in FORMS},
        },
        "headline": {
            "values": n * len(FIELDS), "values_correct": sum(f[CORRECT] for f in fields.values()),
            "files_all_correct": sum(s.all_correct for s in scored), "ready": sum(s.ready for s in scored),
            "ready_wrong": sum(s.ready and not s.all_correct for s in scored),
            "silent": sum(f["silent"] for f in fields.values()), "flagged_files": sum(not s.ready for s in read),
        },
        "fields": fields, "by_form": by_form, "loose_names": loose_names,
        "decisions": {d: decisions[d] for d in DECISIONS if decisions[d]},
        "auto_wrong": sum(1 for s in read if s.row["decision"] == "auto" and not s.all_correct),
        "issues": issues,
        "time": {"per_file": wall / n if wall and n else None, "reading": reading},
        "tokens": {"total": total, **grand, "calls": sum(b["calls"] for b in per_call.values()),
                   "files_with_claude": len(file_totals), "unreported": sum(b["unreported"] for b in per_call.values()),
                   "per_call": {k: b for k, b in per_call.items() if b["calls"]},
                   "per_form": {f: g for f, g in per_form.items() if g["files"]},
                   "top": sorted(file_totals, key=lambda x: -x["total"])[:5]},
    }


# --- formatting helpers -----------------------------------------------------------------------------------------------


def pct(part: int, whole: int, digits: int = 1) -> str:
    return f"{100 * part / whole:.{digits}f}%" if whole else "-"


def num(value: float | int) -> str:
    return f"{round(value):,}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else (f"{minutes} min {secs} s" if minutes else f"{secs} s")


def clock(iso: str | None) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M:%S") if iso else "still running"


def shown(value: object) -> str:
    return "(none)" if value is None or value == "" else str(value)


def grade(part: int, whole: int) -> str:
    """Terminal colour for an accuracy: green from 95%, yellow from 80%, red below."""
    share = part / whole if whole else 0
    return "green" if share >= 0.95 else ("yellow" if share >= 0.8 else "red")


def meter(part: int, whole: int, width: int = 12) -> Text:
    filled = round(width * part / whole) if whole else 0
    return Text.assemble(("█" * filled, grade(part, whole)), ("░" * (width - filled), "grey35"),
                         f" {pct(part, whole)}")


# --- console report ---------------------------------------------------------------------------------------------------


def print_report(console: Console, r: dict, out: Path) -> None:
    run, head, tok, tm = r["run"], r["headline"], r["tokens"], r["time"]
    n = run["files"]
    console.print()
    console.rule(f"[bold]Contract OCR accuracy check[/]  ·  {clock(run['started'])}")

    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold")
    info.add_column()
    info.add_row("Ground truth", run["ground_truth"])
    info.add_row("Website job", f"{run['job']}  ({reading_label(run)})")
    info.add_row("Started", clock(run["started"]))
    info.add_row("Finished", clock(run["finished"]))
    info.add_row("Total time", f"{duration(run['seconds'])} with {run['workers']} worker(s)"
                 + (f", {tm['per_file']:.1f} s per file on average" if tm["per_file"] else ""))
    info.add_row("Files", f"{n} ({', '.join(f'{FORM_LABEL[f]} {c}' for f, c in run['forms'].items() if c)}), "
                 f"{run['read']} read" + (f", [red]{n - run['read']} not read[/]" if run["read"] < n else ""))
    console.print(info)
    console.print()

    tiles = [
        ("Field values correct", f"{pct(head['values_correct'], head['values'])}",
         f"{head['values_correct']:,} of {head['values']:,} values", grade(head["values_correct"], head["values"])),
        ("Files fully correct", f"{head['files_all_correct']} / {n}", "every field right", grade(head["files_all_correct"], n)),
        ("Wrong, not marked", f"{head['silent']}", "HR would not check these", "green" if not head["silent"] else "red"),
        ("Claude tokens", num(tok["total"]), f"{tok['calls']} calls, {num(tok['total'] / max(n, 1))} per file", "cyan"),
    ]
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in tiles:
        grid.add_column(ratio=1)
    grid.add_row(*[Panel(Text.assemble((big, f"bold {style}"), "\n", (sub, "grey62")), title=title, title_align="left",
                         box=box.ROUNDED, padding=(0, 1)) for title, big, sub, style in tiles])
    console.print(grid)

    t = Table(title="Accuracy per field", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD,
              caption="Marked = yellow on the website, for HR to check.  Wrong, not marked = a mistake HR would miss.",
              caption_justify="left", caption_style="grey62")
    for header, justify in [("Field", "left"), ("Correct", "right"), ("Accuracy", "left"), ("Wrong value", "right"),
                            ("Not found", "right"), ("Undecided", "right"), ("Marked", "right"),
                            ("Wrong, not marked", "right")]:
        t.add_column(header, justify=justify)
    for key in FIELDS:
        f = r["fields"][key]
        t.add_row(FIELD_LABEL[key], f"{f[CORRECT]}/{f['total']}", meter(f[CORRECT], f["total"]),
                  str(f[WRONG] or "-"), str(f[MISSING] or "-"), str(f[UNDECIDED] or "-") if key == "signature" else "",
                  str(f["flagged"] or "-"), Text(str(f["silent"]), style="bold red") if f["silent"] else Text("-"))
    console.print(t, end="")
    if r["loose_names"]:
        console.print(f"  [grey62]{r['loose_names']} wrong name(s) differ only in capitals, spaces or punctuation.[/]")

    if len(r["by_form"]) > 1:
        t = Table(title="Accuracy per form", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD)
        t.add_column("Field")
        for form, g in r["by_form"].items():
            t.add_column(f"{FORM_LABEL[form]} ({g['files']})", justify="right")
        for key in [*FIELDS, "all_correct"]:
            label = "Every field correct" if key == "all_correct" else FIELD_LABEL[key]
            t.add_row(label, *[Text(f"{pct(g[key], g['files'], 0)}  {g[key]}/{g['files']}", style=grade(g[key], g["files"]))
                               for g in r["by_form"].values()], end_section=key == FIELDS[-1])
        console.print(t, end="")

    t = Table(title="Whole files", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD, show_header=False)
    t.add_column()
    t.add_column(justify="right")
    t.add_column(style="grey62")
    t.add_row("Every field correct", f"{head['files_all_correct']} / {n}", pct(head["files_all_correct"], n))
    t.add_row("Nothing marked for check", f"{head['ready']} / {n}",
              f"{head['ready_wrong']} of them hold a wrong value" if head["ready_wrong"] else "none of them holds a wrong value")
    for d, label in DECISIONS.items():
        if d in r["decisions"]:
            t.add_row(f"Decision: {label}", str(r["decisions"][d]),
                      (f"{r['auto_wrong']} with a wrong value" if r["auto_wrong"] else "all fully correct") if d == "auto" else "")
    for missed in r["run"]["not_read"][:10]:
        t.add_row(Text(f"Not read: {missed['file']}", style="red"), "", missed["error"] or "")
    console.print(t, end="")

    if r["issues"]:
        t = Table(title=f"Values to look at ({len(r['issues'])})", title_justify="left", title_style="bold",
                  box=box.SIMPLE_HEAD, caption="Not marked first. Every file and field is in report.html and detail.csv.",
                  caption_justify="left", caption_style="grey62")
        t.add_column("File", max_width=44, no_wrap=True, overflow="ellipsis")
        for header in ("Form", "Field", "Ground truth", "Read", "Result"):
            t.add_column(header, overflow="fold")
        for i in r["issues"][:25]:
            result = Text(VERDICT_LABEL[i["verdict"]] + (", marked" if i["flagged"] else ", NOT marked"),
                          style="yellow" if i["flagged"] else "bold red")
            t.add_row(i["file"], FORM_LABEL.get(i["form"], i["form"]), FIELD_LABEL[i["field"]], shown(i["expected"]),
                      shown(i["got"]), result)
        if len(r["issues"]) > 25:
            t.add_row(f"... {len(r['issues']) - 25} more", "", "", "", "", "")
        console.print(t, end="")

    reading = tm["reading"]
    t = Table(title="Time", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD, show_header=False)
    t.add_column()
    t.add_column(justify="right")
    t.add_column(style="grey62")
    t.add_row("Started", clock(run["started"]), "")
    t.add_row("Finished", clock(run["finished"]), "")
    t.add_row("Total", duration(run["seconds"]), f"{run['workers']} worker(s) reading at the same time")
    if tm["per_file"]:
        t.add_row("Per file, overall", f"{tm['per_file']:.1f} s", "total time / files")
    if reading:
        t.add_row("Per file, one worker", f"{reading['average']:.1f} s",
                  f"median {reading['median']:.1f} s, slowest {reading['max']:.1f} s")
        for form, secs in reading["by_form"].items():
            t.add_row(f"  {FORM_LABEL[form]}", f"{secs:.1f} s", "average")
        for calls, secs in reading["by_calls"].items():
            t.add_row(f"  {calls} Claude call(s)", f"{secs:.1f} s", "average")
    else:
        t.add_row("Per file, one worker", "-", "this backend does not report it yet; restart it")
    console.print(t, end="")
    if reading:
        t = Table(title="Slowest files", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD)
        for header, justify in (("Time", "right"), ("Form", "left"), ("Claude calls", "left"), ("File", "left")):
            t.add_column(header, justify=justify)
        for s in reading["slowest"]:
            t.add_row(f"{s['seconds']:.1f} s", FORM_LABEL[s["form"]], ", ".join(s["calls"]) or "none", s["file"])
        console.print(t, end="")

    if tok["per_call"]:
        t = Table(title="Claude tokens per call type", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD,
                  show_footer=True, caption="Cache read and write: the instructions Claude Code sends with every call, "
                  "mostly read back from its cache.", caption_justify="left", caption_style="grey62")
        footer = {"calls": str(tok["calls"]), "total": num(tok["total"]), **{k: num(tok[k]) for k in TOKEN_KEYS}}
        t.add_column("Sent to Claude", footer="All calls", footer_style="bold")
        t.add_column("Calls", justify="right", footer=footer["calls"])
        t.add_column("Total tokens", justify="right", style="bold", footer=footer["total"])
        t.add_column("Per call", justify="right", footer=num(tok["total"] / max(tok["calls"], 1)))
        for k in TOKEN_KEYS:
            t.add_column(TOKEN_LABEL[k], justify="right", footer=footer[k], style="grey62")
        for kind, b in tok["per_call"].items():
            reported = b["calls"] - b["unreported"]
            t.add_row(CALL_LABEL.get(kind, kind), str(b["calls"]), num(b["total"]),
                      num(b["total"] / reported) if reported else "-", *[num(b[k]) for k in TOKEN_KEYS])
        console.print(t, end="")
        if tok["unreported"]:
            console.print(f"  [yellow]{tok['unreported']} call(s) reported no tokens.[/]")

        t = Table(title="Claude tokens per form", title_justify="left", title_style="bold", box=box.SIMPLE_HEAD)
        for header, justify in (("Form", "left"), ("Files", "right"), ("Calls", "right"), ("Total tokens", "right"),
                                ("Per file", "right")):
            t.add_column(header, justify=justify)
        for form, g in tok["per_form"].items():
            t.add_row(FORM_LABEL[form], str(g["files"]), str(g["calls"]), num(g["total"]), num(g["total"] / g["files"]))
        console.print(t, end="")

    total = Text.assemble((f"{num(tok['total'])} tokens", "bold cyan"),
                          f"  over {tok['calls']} Claude call(s) for {tok['files_with_claude']} of {n} files\n",
                          ("  ·  ".join(f"{TOKEN_LABEL[k]} {num(tok[k])}" for k in TOKEN_KEYS), "grey62"), "\n",
                          f"Per file that used Claude: {num(tok['total'] / tok['files_with_claude']) if tok['files_with_claude'] else '-'}"
                          f"   ·   Per file overall: {num(tok['total'] / max(n, 1))}")
    console.print(Panel(total, title="[bold]Total Claude usage[/]", title_align="left", box=box.ROUNDED))
    console.print(f"\nOpen [bold]{out / 'report.html'}[/] to read or share the full report.")


# --- HTML report ------------------------------------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;--page:#f3f3f1;--surface:#fcfcfb;--line:#e3e2de;--text:#0b0b0b;--text-2:#52514e;--muted:#8a8984;
--meter:#2a78d6;--track:#e9e8e4;--good:#0ca30c;--good-ink:#006300;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;
--critical-ink:#b42323}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#111110;--surface:#1a1a19;
--line:#2e2e2b;--text:#f0efec;--text-2:#c3c2b7;--muted:#8f8e86;--meter:#3987e5;--track:#2e2e2b;--good-ink:#4cc94c;
--critical-ink:#ef7a7a}}
:root[data-theme="dark"]{color-scheme:dark;--page:#111110;--surface:#1a1a19;--line:#2e2e2b;--text:#f0efec;--text-2:#c3c2b7;
--muted:#8f8e86;--meter:#3987e5;--track:#2e2e2b;--good-ink:#4cc94c;--critical-ink:#ef7a7a}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 10px}
.sub{color:var(--text-2);margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.info{display:grid;grid-template-columns:max-content 1fr;gap:4px 18px;margin:0}.info dt{color:var(--text-2)}.info dd{margin:0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0 0}
.tile .label{color:var(--text-2);font-size:13px}.tile .big{font-size:28px;font-weight:650;margin:2px 0;font-variant-numeric:tabular-nums}
.tile .small{color:var(--muted);font-size:12.5px}
.tile.alert .big{color:var(--critical-ink)}.tile.ok .big{color:var(--good-ink)}
.scroll{overflow-x:auto}
table.kv{width:auto;min-width:min(100%,560px)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--text-2);font-weight:600;font-size:12.5px;white-space:nowrap}
td.n,th.n{text-align:right}tfoot td{font-weight:650;border-bottom:0}
.meter{display:flex;align-items:center;gap:8px;min-width:170px}
.meter .bar{flex:1;height:8px;border-radius:4px;background:var(--track);overflow:hidden}
.meter .bar i{display:block;height:100%;border-radius:4px;background:var(--meter)}
.meter span{width:52px;text-align:right}
.muted{color:var(--muted)}.dim{color:var(--text-2)}
.flag{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.flag b{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;font-size:11px;color:#fff}
.s-correct b{background:var(--good)}.s-wrong b{background:var(--critical)}.s-missing b{background:var(--serious)}
.s-undecided b{background:var(--warning);color:#1a1a19}.s-marked b{background:var(--warning);color:#1a1a19}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--text-2);font-size:13px;margin:8px 0 12px}
.grid td.cell{text-align:center;padding:6px 4px}.grid td.cell b{margin:0 auto}
.grid td.cell.marked{box-shadow:inset 0 0 0 2px var(--warning);border-radius:4px}
.grid th.f{writing-mode:vertical-rl;transform:rotate(180deg);height:120px;text-align:left;padding:8px 2px}
details summary{cursor:pointer;color:var(--meter);font-weight:600;margin:6px 0}
.note{color:var(--text-2);font-size:13px;margin:8px 0 0}
code{font-size:12.5px}
"""

ICON = {CORRECT: "✓", WRONG: "✕", MISSING: "–", UNDECIDED: "?"}


def e(value: object) -> str:
    return html.escape(shown(value))


def html_meter(part: int, whole: int) -> str:
    share = 100 * part / whole if whole else 0
    return f'<div class="meter"><div class="bar"><i style="width:{share:.1f}%"></i></div><span>{pct(part, whole)}</span></div>'


def html_flag(state: str, label: str) -> str:
    return f'<span class="flag s-{state}"><b aria-hidden="true">{ICON.get(state, "!")}</b>{html.escape(label)}</span>'


def html_report(r: dict, scored: list[Scored]) -> str:
    run, head, tok, tm = r["run"], r["headline"], r["tokens"], r["time"]
    n = run["files"]
    forms = ", ".join(f"{FORM_LABEL[f]} {c}" for f, c in run["forms"].items() if c)
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Contract OCR accuracy</title><style>{CSS}</style></head>
<body><main>
<h1>Contract OCR accuracy check</h1>
<p class="sub">{e(clock(run['started']))} · {n} synthetic contracts read by the contract-OCR website and compared with the
generator's ground truth</p>
<div class="card"><dl class="info">
<dt>Started</dt><dd>{e(clock(run['started']))}</dd><dt>Finished</dt><dd>{e(clock(run['finished']))}</dd>
<dt>Total time</dt><dd>{e(duration(run['seconds']))} with {run['workers']} worker(s) reading at the same time</dd>
<dt>Files</dt><dd>{n} ({e(forms)}), {run['read']} read{f", <b>{n - run['read']} not read</b>" if run['read'] < n else ""}</dd>
<dt>Reading</dt><dd>{e(reading_label(run))}</dd>
<dt>Website job</dt><dd><code>{e(run['job'])}</code></dd><dt>Ground truth</dt><dd><code>{e(run['ground_truth'])}</code></dd>
</dl></div>
<div class="tiles">"""]
    tiles = [
        ("Field values correct", pct(head["values_correct"], head["values"]), f"{head['values_correct']:,} of {head['values']:,} values", ""),
        ("Files fully correct", f"{head['files_all_correct']} / {n}", "every field right", ""),
        ("Wrong, not marked", str(head["silent"]), "mistakes HR would not check", "alert" if head["silent"] else "ok"),
        ("Nothing to check", f"{head['ready']} / {n}", f"{head['ready_wrong']} of them hold a wrong value", "alert" if head["ready_wrong"] else ""),
        ("Total time", duration(run["seconds"]), f"{tm['per_file']:.1f} s per file" if tm["per_file"] else "", ""),
        ("Claude tokens", num(tok["total"]), f"{tok['calls']} calls · {num(tok['total'] / max(n, 1))} per file", ""),
    ]
    parts += [f'<div class="card tile {cls}"><div class="label">{label}</div><div class="big">{html.escape(big)}</div>'
              f'<div class="small">{html.escape(small)}</div></div>' for label, big, small, cls in tiles]
    parts.append("</div>")

    parts.append("""<h2>Accuracy per field</h2><div class="card scroll"><table><thead><tr><th>Field</th><th class="n">Correct</th>
<th>Accuracy</th><th class="n">Wrong value</th><th class="n">Not found</th><th class="n">Undecided</th>
<th class="n">Marked for check</th><th class="n">Wrong, not marked</th></tr></thead><tbody>""")
    for key in FIELDS:
        f = r["fields"][key]
        silent = f'<b style="color:var(--critical-ink)">{f["silent"]}</b>' if f["silent"] else '<span class="muted">0</span>'
        undecided = (f[UNDECIDED] or "-") if key == "signature" else ""
        parts.append(f'<tr><td>{FIELD_LABEL[key]}</td><td class="n">{f[CORRECT]}/{f["total"]}</td>'
                     f'<td>{html_meter(f[CORRECT], f["total"])}</td><td class="n">{f[WRONG] or "-"}</td>'
                     f'<td class="n">{f[MISSING] or "-"}</td><td class="n">{undecided}</td>'
                     f'<td class="n">{f["flagged"] or "-"}</td><td class="n">{silent}</td></tr>')
    parts.append('</tbody></table><p class="note"><b>Marked for check</b>: yellow on the website, so HR looks at it. '
                 '<b>Wrong, not marked</b>: a mistake HR would not see, the number to keep at zero.</p></div>')

    if len(r["by_form"]) > 1:
        parts.append('<h2>Accuracy per form</h2><div class="card scroll"><table><thead><tr><th>Field</th>')
        parts += [f'<th>{FORM_LABEL[form]} ({g["files"]} files)</th>' for form, g in r["by_form"].items()]
        parts.append("</tr></thead><tbody>")
        for key in [*FIELDS, "all_correct"]:
            label = "<b>Every field correct</b>" if key == "all_correct" else FIELD_LABEL[key]
            parts.append(f"<tr><td>{label}</td>" + "".join(
                f'<td>{html_meter(g[key], g["files"])}<span class="muted">{g[key]} of {g["files"]}</span></td>'
                for g in r["by_form"].values()) + "</tr>")
        parts.append("</tbody></table></div>")

    parts.append('<h2>Whole files</h2><div class="card scroll"><table class="kv"><tbody>')
    rows = [("Every field correct", f"{head['files_all_correct']} / {n}", pct(head["files_all_correct"], n)),
            ("Nothing marked for check", f"{head['ready']} / {n}",
             f"{head['ready_wrong']} of them hold a wrong value" if head["ready_wrong"] else "none of them holds a wrong value")]
    for d, label in DECISIONS.items():
        if d in r["decisions"]:
            note = (f"{r['auto_wrong']} with a wrong value" if r["auto_wrong"] else "all fully correct") if d == "auto" else ""
            rows.append((f"Decision: {label}", str(r["decisions"][d]), note))
    rows += [(f"Not read: {m['file']}", "", m["error"] or "") for m in run["not_read"]]
    parts += [f'<tr><td>{html.escape(a)}</td><td class="n">{html.escape(b)}</td><td class="dim">{html.escape(c)}</td></tr>'
              for a, b, c in rows]
    parts.append("</tbody></table></div>")

    parts.append(f'<h2>Values to look at ({len(r["issues"])})</h2><div class="card scroll">')
    if r["issues"]:
        parts.append('<table><thead><tr><th>File</th><th>Form</th><th>Field</th><th>Ground truth</th><th>Read</th>'
                     '<th>Result</th><th>Marked?</th></tr></thead><tbody>')
        for i in r["issues"]:
            marked = html_flag("marked", "Marked") if i["flagged"] else '<b style="color:var(--critical-ink)">Not marked</b>'
            parts.append(f'<tr><td>{e(i["file"])}</td><td>{FORM_LABEL.get(i["form"], i["form"])}</td>'
                         f'<td>{FIELD_LABEL[i["field"]]}</td><td>{e(i["expected"])}</td><td>{e(i["got"])}</td>'
                         f'<td>{html_flag(i["verdict"], VERDICT_LABEL[i["verdict"]])}</td><td>{marked}</td></tr>')
        parts.append('</tbody></table><p class="note">Not marked first.</p>')
    else:
        parts.append("<p>Every value matches the ground truth.</p>")
    parts.append("</div>")

    reading = tm["reading"]
    parts.append('<h2>Time</h2><div class="card scroll"><table class="kv"><tbody>')
    rows = [("Started", clock(run["started"]), ""), ("Finished", clock(run["finished"]), ""),
            ("Total", duration(run["seconds"]), f"{run['workers']} worker(s) reading at the same time")]
    if tm["per_file"]:
        rows.append(("Per file, overall", f"{tm['per_file']:.1f} s", "total time divided by files"))
    if reading:
        rows.append(("Per file, one worker", f"{reading['average']:.1f} s",
                     f"median {reading['median']:.1f} s, slowest {reading['max']:.1f} s"))
        rows += [(f"· {FORM_LABEL[form]}", f"{secs:.1f} s", "average") for form, secs in reading["by_form"].items()]
        rows += [(f"· {calls} Claude call(s)", f"{secs:.1f} s", "average") for calls, secs in reading["by_calls"].items()]
    else:
        rows.append(("Per file, one worker", "-", "this backend does not report it yet; restart it"))
    parts += [f'<tr><td>{html.escape(a)}</td><td class="n">{html.escape(b)}</td><td class="dim">{html.escape(c)}</td></tr>'
              for a, b, c in rows]
    parts.append("</tbody></table>")
    if reading:
        parts.append('<h3 style="font-size:14px;margin:18px 0 6px">Slowest files</h3><table><thead><tr><th class="n">Time</th>'
                     '<th>Form</th><th>Claude calls</th><th>File</th></tr></thead><tbody>')
        parts += [f'<tr><td class="n">{s["seconds"]:.1f} s</td><td>{FORM_LABEL[s["form"]]}</td>'
                  f'<td>{e(", ".join(s["calls"]) or "none")}</td><td>{e(s["file"])}</td></tr>' for s in reading["slowest"]]
        parts.append("</tbody></table>")
    parts.append("</div>")

    parts.append('<h2>Claude tokens</h2><div class="card scroll">')
    if tok["per_call"]:
        parts.append('<table><thead><tr><th>Sent to Claude</th><th class="n">Calls</th><th class="n">Total tokens</th>'
                     '<th class="n">Per call</th>' + "".join(f'<th class="n">{TOKEN_LABEL[k]}</th>' for k in TOKEN_KEYS)
                     + "</tr></thead><tbody>")
        for kind, b in tok["per_call"].items():
            reported = b["calls"] - b["unreported"]
            parts.append(f'<tr><td>{CALL_LABEL.get(kind, e(kind))}</td><td class="n">{b["calls"]}</td>'
                         f'<td class="n"><b>{num(b["total"])}</b></td><td class="n">{num(b["total"] / reported) if reported else "-"}</td>'
                         + "".join(f'<td class="n dim">{num(b[k])}</td>' for k in TOKEN_KEYS) + "</tr>")
        parts.append(f'</tbody><tfoot><tr><td>All calls</td><td class="n">{tok["calls"]}</td><td class="n">{num(tok["total"])}</td>'
                     f'<td class="n">{num(tok["total"] / max(tok["calls"], 1))}</td>'
                     + "".join(f'<td class="n">{num(tok[k])}</td>' for k in TOKEN_KEYS) + "</tr></tfoot></table>")
        parts.append('<p class="note">Cache write and cache read are the instructions Claude Code sends with every call; '
                     "most of them come back from its cache.</p>")
        parts.append('<h3 style="font-size:14px;margin:18px 0 6px">Per form</h3><table><thead><tr><th>Form</th>'
                     '<th class="n">Files</th><th class="n">Calls</th><th class="n">Total tokens</th><th class="n">Per file</th>'
                     "</tr></thead><tbody>")
        parts += [f'<tr><td>{FORM_LABEL[form]}</td><td class="n">{g["files"]}</td><td class="n">{g["calls"]}</td>'
                  f'<td class="n">{num(g["total"])}</td><td class="n">{num(g["total"] / g["files"])}</td></tr>'
                  for form, g in tok["per_form"].items()]
        parts.append('</tbody></table><h3 style="font-size:14px;margin:18px 0 6px">Most tokens for one file</h3><table>'
                     '<thead><tr><th class="n">Tokens</th><th>Calls</th><th>Form</th><th>File</th></tr></thead><tbody>')
        parts += [f'<tr><td class="n">{num(t["total"])}</td><td>{e(", ".join(t["calls"]))}</td>'
                  f'<td>{FORM_LABEL[t["form"]]}</td><td>{e(t["file"])}</td></tr>' for t in tok["top"]]
        parts.append("</tbody></table>")
    per_used = num(tok["total"] / tok["files_with_claude"]) if tok["files_with_claude"] else "-"
    parts.append(f'<p style="margin:16px 0 0;font-size:15px"><b>Total: {num(tok["total"])} tokens</b> over {tok["calls"]} '
                 f'call(s) for {tok["files_with_claude"]} of {n} files · {per_used} per file that used Claude · '
                 f'{num(tok["total"] / max(n, 1))} per file overall</p></div>')

    parts.append('<h2>Every file</h2><div class="card"><div class="legend">'
                 + "".join(html_flag(v, VERDICT_LABEL[v]) for v in (CORRECT, WRONG, MISSING, UNDECIDED))
                 + '<span>Yellow outline: marked for check on the website. Hover a cell for the values.</span></div>'
                 '<details><summary>Show all files</summary><div class="scroll"><table class="grid"><thead><tr><th>File</th>'
                 '<th>Form</th><th class="n">Time</th><th class="n">Tokens</th>'
                 + "".join(f'<th class="f">{FIELD_LABEL[k]}</th>' for k in FIELDS) + "</tr></thead><tbody>")
    for s in scored:
        tokens = sum(tokens_of((s.row or {}).get("tokens")).values())
        head_cells = (f'<td>{e(s.truth.name)}</td><td>{FORM_LABEL.get(s.truth.form, s.truth.form)}</td>'
                      f'<td class="n">{f"{s.seconds:.0f} s" if s.seconds is not None else "-"}</td>'
                      f'<td class="n">{num(tokens) if tokens else "-"}</td>')
        if s.row is None:
            parts.append(f'<tr>{head_cells}<td colspan="{len(FIELDS)}">{html_flag(WRONG, f"Not read: {s.error}")}</td></tr>')
            continue
        cells = []
        for key in FIELDS:
            c = s.cells[key]
            tip = (f"{FIELD_LABEL[key]}: {VERDICT_LABEL[c['verdict']]}\nGround truth: {shown(c['expected'])}\n"
                   f"Read: {shown(c['got'])}" + (f"\nMarked: {c['note']}" if c["flagged"] else ""))
            cells.append(f'<td class="cell{" marked" if c["flagged"] else ""}" title="{html.escape(tip)}">'
                         f'<span class="flag s-{c["verdict"]}"><b>{ICON[c["verdict"]]}</b></span></td>')
        parts.append(f"<tr>{head_cells}{''.join(cells)}</tr>")
    parts.append("</tbody></table></div></details></div>")
    parts.append('<p class="note">Synthetic contracts only: the names and NIKs are generated. '
                 "The same data is in detail.csv, tokens.csv and summary.json next to this file.</p></main></body></html>")
    return "\n".join(parts)


# --- files ------------------------------------------------------------------------------------------------------------


def write_csvs(out: Path, scored: list[Scored]) -> None:
    with (out / "detail.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "form", "kelompok", "decision", "seconds", "field", "ground_truth", "extracted",
                         "verdict", "marked", "note"])
        for s in scored:
            meta = s.truth.record["meta"]
            if s.row is None:
                writer.writerow([s.truth.path, s.truth.form, meta.get("kelompok"), "", s.seconds, "", "", "",
                                 f"not read: {s.error}", "", ""])
                continue
            for key, cell in s.cells.items():
                writer.writerow([s.truth.path, s.truth.form, meta.get("kelompok"), s.row["decision"], s.seconds, key,
                                 cell["expected"], cell["got"], cell["verdict"], "yes" if cell["flagged"] else "",
                                 cell["note"]])
    with (out / "tokens.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "form", "call", "kind", *TOKEN_KEYS, "total"])
        for s in scored:
            for n, call in enumerate(s.calls, 1):
                used = tokens_of(call.get("tokens"))
                writer.writerow([s.truth.path, s.truth.form, n, call["kind"], *used.values(), sum(used.values())])


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare the website's reading of generated contracts with the ground truth.")
    parser.add_argument("--gt", type=Path, help="ground truth from contract_generator.py (default: output/ground_truth.json)")
    parser.add_argument("--api", default="http://127.0.0.1:3000", help="the website backend (default: %(default)s)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--job", help="score this job (finished or running) instead of starting a new one")
    source.add_argument("--from", dest="saved", type=Path, help="redo the report in this folder from its job.json")
    parser.add_argument("--form", action="append", choices=FORMS, default=[], help="only this form; repeat for more")
    parser.add_argument("--kelompok", choices=["kalibrasi", "uat"], help="only this group of the ground truth")
    parser.add_argument("--limit", type=int, help="only the first N files, for a quick trial")
    parser.add_argument("--out", type=Path, help="report folder (default: accuracy/<time> next to this script, or the --from folder)")
    parser.add_argument("--mode", choices=MODES, default="ocr",
                        help="how a new job reads: with OCR (default), or full_page, which sends the whole first and last "
                             "page of every PDF unmasked to Claude (test only; use generated contracts)")
    parser.add_argument("--allow-without-claude", action="store_true",
                        help="start a new job even when the website has Claude switched on but cannot reach it (it then "
                             "reads with the text patterns only); with CLAUDE_MODE=off no flag is needed")
    args = parser.parse_args()
    if not sys.stdout.isatty():  # piped or redirected on Windows would be cp1252, which has no bars or check marks
        sys.stdout.reconfigure(encoding="utf-8")
    console = Console(highlight=False, record=True, width=None if sys.stdout.isatty() else 130)

    saved = None
    if args.saved:
        try:
            saved = json.loads((args.saved / "job.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            sys.exit(f"No job.json in {args.saved}")
    if saved and "truth" in saved and not args.gt:
        # the ground truth as it was when the job ran: the generator may have replaced output/ since
        gt, records = Path(saved["ground_truth"]), saved["truth"]
    else:
        gt = args.gt or (Path(saved["ground_truth"]) if saved else SCRIPT_DIR / "output" / "ground_truth.json")
        gt = gt if gt.is_absolute() or gt.exists() else SCRIPT_DIR / gt
        if not gt.is_file():
            sys.exit(f"No ground truth at {gt}")
        records = json.loads(gt.read_text(encoding="utf-8"))
    truths = load_truth(records, args.form, args.kelompok, args.limit)
    if not truths:
        sys.exit("No file in the ground truth matches the filters.")

    if saved:
        job, niks, workers = saved["job"], saved["niks"], saved["workers"]
        if not (args.form or args.kelompok or args.limit):  # score what the job read, not the whole ground truth
            in_job = {f["path"] for f in job["files"]}
            truths = [t for t in truths if t.path in in_job]
    else:
        site = Website(args.api)
        health = wait_until_ready(site)
        workers = health["extractor"]["workers"]
        console.print(f"Website ready: {workers} worker(s), Claude {health['claude']}, folder '{health['rootName']}', "
                      f"signed in as {BENCH_NAME}")
        watch = None
        if args.job:
            job_id = args.job
        else:
            problem = claude_problem(health["claude"], args.mode, args.allow_without_claude)
            if problem:
                sys.exit(problem)
            if health["claude"] != "on":
                console.print(f"[yellow]Claude is {health['claude']}: the run reads with the text patterns only.[/]")
            try:
                started = site.call("POST", "/api/jobs", {"files": [t.path for t in truths], "mode": args.mode})
            except ApiError as error:
                if error.code in ("user_has_job", "job_running"):
                    sys.exit(f"The benchmark user already has a run queued or running ({error.body.get('jobId')}). "
                             "Wait for it, cancel it on the website, or score it with --job.")
                if error.code in ("file_not_found", "not_found", "bad_path"):
                    sys.exit("Some PDFs are not in the website's folder. Run copy_to_onedrive.py for this ground truth's "
                             "output folder, and check FOLDER_ROOT in ocr-analysis/apps/backend/.env.")
                sys.exit(f"The website refused the job: {error}")
            job_id = started["id"]
            console.print(f"Started job {job_id} ({args.mode}) for {len(truths)} file(s) at {dt.datetime.now():%H:%M:%S}; "
                          "it is also on the website under 'Hasil OCR terakhir'.")
            if started["claude"]:
                watch = ClaudeWatch()
            elif health["claude"] == "on" and not args.allow_without_claude:  # the workers lost Claude in between
                site.call("POST", f"/api/jobs/{job_id}/cancel", {})
                sys.exit(f"Claude dropped out as job {job_id} started, so it was cancelled; check the Claude sign-in and rerun.")
        try:
            job = wait_for_job(site, job_id, console, watch)
            niks = site.call("POST", f"/api/jobs/{job_id}/niks", {}).get("niks", {})
        except ClaudeStopped as stop:
            site.call("POST", f"/api/jobs/{job_id}/cancel", {})
            sys.exit(f"\nStopped: {stop} ({watch.detail() if watch else ''}). Job {job_id} is cancelled on the website "
                     "(the files being read finish first). Check the Claude sign-in (`claude auth status`), restart the "
                     "website, and run again.")
        except KeyboardInterrupt:
            sys.exit(f"\nStopped waiting; the job keeps running on the website. Score it later with --job {job_id}")
        except ApiError as error:
            sys.exit(f"Cannot read job {job_id}: {error} (the backend forgets jobs when it restarts)")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            sys.exit(f"\nLost the website backend while waiting for job {job_id}: it was stopped or restarted, which "
                     "drops a running job. Start it again and rerun.")

    by_path = {f["path"]: f for f in job["files"]}
    scored = [score(t, by_path.get(t.path), niks) for t in truths]
    known = {t.path for t in truths}
    extra = [path for path in by_path if path not in known]
    results = analyse(job, workers, gt, scored, extra)

    out = args.out or args.saved or SCRIPT_DIR / "accuracy" / local_time(job["startedAt"]).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    if not saved:
        (out / "job.json").write_text(json.dumps({"ground_truth": str(gt), "workers": workers, "job": job, "niks": niks,
                                                  "truth": [t.record for t in truths]}, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "report.html").write_text(html_report(results, scored), encoding="utf-8")
    write_csvs(out, scored)
    console.export_text()  # drop the progress lines; the saved report starts at its title
    print_report(console, results, out)
    console.save_text(str(out / "report.txt"), styles=False)


if __name__ == "__main__":
    main()
