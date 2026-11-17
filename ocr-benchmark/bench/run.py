"""Read the 800 synthetic contracts with one OCR engine, through the production pipeline, and record what it read and
what it cost.

    .venv/Scripts/python -m bench.run <engine> [--workers 4] [--threads 2] [--limit N] [--form scan] [--fresh]

Everything is local: Claude is off (no extractor), so the numbers are what the laptop reads on its own. The pipeline
runs with the website's settings (default config, automatic decisions allowed); only the OCR provider changes.

Output, in runs/<engine>/ (synthetic data only: full NIKs and names):
    files.jsonl   one line per contract: the worker's reply (as the website receives it) and its cost
    run.json      the run: settings, machine, versions, wall time, CPU and memory samples, per-worker start-up
A stopped run resumes: contracts already in files.jsonl are skipped unless --fresh.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import sys
import threading
import time
from multiprocessing import Pool
from pathlib import Path

import psutil

from bench.engines import ENGINES, model_files
from bench.paths import DATASET, GENERATOR, GROUND_TRUTH, MODELS, PDF_DIR, RUNS

sys.path.insert(0, str(GENERATOR))
from copy_to_onedrive import safe_folder  # noqa: E402  (the vendor folder name, as the website sees it)

_state: dict = {}


def _init(engine_key: str, threads: int, binarize: bool, claude: bool = False) -> None:
    # below normal: the owner's own apps (Zoom, Word) always come first; Tesseract's processes inherit it
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    _count_child_processes()
    # every numeric library in the worker gets the same thread budget
    for var in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[var] = str(threads)
    import cv2

    cv2.setNumThreads(threads)
    from bench.engines import make_engine
    from contract_extractor.common.config import load_settings
    from contract_extractor.document.ocr.engine import OcrRunner

    if not binarize:
        _patch_gray_input()
    started = time.perf_counter()
    engine = make_engine(ENGINES[engine_key], threads)
    _state["init_s"] = time.perf_counter() - started
    _state["engine"] = _Timed(engine)
    settings = load_settings(None)
    settings.decision.allow_auto = True  # as the website runs (ALLOW_AUTO=true)
    _state["settings"] = settings
    _state["extractor"] = _claude_extractor(settings) if claude else None
    second = None
    second_kind = ENGINES[engine_key].options.get("second_reader")
    if second_kind == "tesseract":  # roles swapped: Tesseract confirms what RapidOCR read
        from bench.engines import TesseractEngine

        second = _Timed(TesseractEngine(ENGINES["tesseract_fast"], threads))
    elif second_kind:
        from contract_extractor.document.ocr.rapid import RapidOCRReader

        second = _Timed(RapidOCRReader(MODELS / "rapidocr", threads))  # the product's own reader and settings
    _state["second"] = second
    _state["runner"] = OcrRunner(providers=[_state["engine"]], scoring_dpi=settings.ocr.scoring_dpi,
                                 read_dpi=settings.ocr.read_dpi, min_mean_conf=settings.ocr.min_mean_conf,
                                 second=second)
    _state["first"] = True


# Claude as the website runs it (backend: `serve-stdio --claude --model claude-haiku-5-5 --effort max`): only the
# fallback for the fields the local reading leaves unsure, plus the NIK and signature crops.
CLAUDE_MODEL, CLAUDE_EFFORT = "claude-haiku-5-5", "max"
BACKEND_ENV = Path(__file__).resolve().parents[2] / "ocr-analysis" / "apps" / "backend" / ".env"


def _claude_extractor(settings):
    from contract_extractor.cli import _override_extractor
    from contract_extractor.extractor_method.llm.factory import build_extractor

    _override_extractor(settings, True, CLAUDE_MODEL, CLAUDE_EFFORT)
    return build_extractor(settings)


def _claude_code_cmd() -> None:
    """The Claude Code binary the website uses (CLAUDE_CODE_CMD in the backend's .env), for the workers to inherit.
    Only that one line is read."""
    if os.environ.get("CLAUDE_CODE_CMD") or not BACKEND_ENV.exists():
        return
    for line in BACKEND_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("CLAUDE_CODE_CMD="):
            os.environ["CLAUDE_CODE_CMD"] = line.split("=", 1)[1].strip()


_child = {"cpu_s": 0.0, "peak_mb": 0.0}


def _count_child_processes() -> None:
    """Tesseract runs as a separate program per page, so its CPU never shows in the worker's own times. Every child
    process's CPU time and peak memory are read from its handle once it has finished (Windows keeps them there)."""
    import ctypes
    import subprocess
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(COUNTERS), wintypes.DWORD]

    def seconds(t: FILETIME) -> float:
        return ((t.hi << 32) | t.lo) / 1e7

    class CountedPopen(subprocess.Popen):
        def communicate(self, *args, **kwargs):
            out = super().communicate(*args, **kwargs)
            handle = int(getattr(self, "_handle", 0) or 0)
            if handle:
                c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
                if kernel32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                    _child["cpu_s"] += seconds(k) + seconds(u)
                counters = COUNTERS()
                counters.cb = ctypes.sizeof(COUNTERS)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    _child["peak_mb"] = max(_child["peak_mb"], counters.PeakWorkingSetSize / 2**20)
            return out

    import pytesseract.pytesseract as pt

    pt.subprocess.Popen = CountedPopen


def _patch_gray_input() -> None:
    """The full read gets the straightened grayscale page instead of the thresholded one (deep-learning engines are
    trained on photos and scans, not on black-and-white pages). Word coordinates are unchanged: thresholding moves
    no pixel. The table-border retry still erases the borders on the thresholded page."""
    import contract_extractor.document.ocr.engine as engine_module
    from contract_extractor.document.ocr import preprocess as pre

    def gray_preprocess(gray, erase=False):
        straight, angle = pre.straighten(gray)
        return (pre.erase_rules(pre.binarize(straight)) if erase else straight), angle

    engine_module.preprocess = gray_preprocess


class _Timed:
    """The engine, with every OCR call counted and timed."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.name = engine.name
        self.is_local = engine.is_local
        self.calls = 0
        self.seconds = 0.0
        self.pixels = 0

    def read(self, image):
        started = time.perf_counter()
        try:
            return self._engine.read(image)
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - started
            self.pixels += int(image.shape[0]) * int(image.shape[1])


def _one(item: tuple[str, str]) -> dict:
    from contract_extractor.extractor_method.pattern.filename import parse_filename
    from contract_extractor.pipeline import process_file
    from contract_extractor.worker import parsed_filename_json

    name, vendor = item
    engine: _Timed = _state["engine"]
    me = psutil.Process()
    calls, ocr_s, pixels = engine.calls, engine.seconds, engine.pixels
    second: _Timed | None = _state.get("second")
    second_calls, second_s = (second.calls, second.seconds) if second else (0, 0.0)
    cpu = me.cpu_times()
    child_cpu = _child["cpu_s"]
    _child["peak_mb"] = 0.0
    started = time.perf_counter()
    error = None
    reply = None
    try:
        result = process_file(PDF_DIR / name, _state["settings"], _state["runner"], _state.get("extractor"), vendor)
        reply = {"result": result.model_dump(mode="json"), "filename": parsed_filename_json(parse_filename(name))}
    except Exception as exc:  # noqa: BLE001 - one contract failing is a result, not the end of the run
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    seconds = time.perf_counter() - started
    cpu_after = me.cpu_times()
    memory = me.memory_info()
    record = {
        "name": name,
        "vendor": vendor,
        "reply": reply,
        "error": error,
        "seconds": round(seconds, 3),
        "cpu_s": round((cpu_after.user - cpu.user) + (cpu_after.system - cpu.system), 3),
        "child_cpu_s": round(_child["cpu_s"] - child_cpu, 3),  # Tesseract's own processes
        "child_peak_mb": round(_child["peak_mb"], 1),
        "ocr_calls": engine.calls - calls,
        "ocr_s": round(engine.seconds - ocr_s, 3),
        "ocr_megapixels": round((engine.pixels - pixels) / 1e6, 2),
        "second_calls": (second.calls - second_calls) if second else 0,  # RapidOCR reads as the second reader
        "second_s": round(second.seconds - second_s, 3) if second else 0.0,
        "rss_mb": round(memory.rss / 2**20, 1),
        "peak_mb": round(getattr(memory, "peak_wset", memory.rss) / 2**20, 1),
        "pid": me.pid,
    }
    if _state["first"]:
        record["init_s"] = round(_state["init_s"], 3)
        _state["first"] = False
    return record


class _Sampler(threading.Thread):
    """Machine CPU and the pool's memory, once a second."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict] = []
        self.stop = threading.Event()

    def run(self) -> None:
        me = psutil.Process()
        psutil.cpu_percent(None)
        while not self.stop.wait(1.0):
            rss = 0
            for child in me.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except psutil.Error:
                    pass
            vm = psutil.virtual_memory()
            self.samples.append({"t": round(time.time(), 1), "cpu": psutil.cpu_percent(None),
                                 "pool_mb": round(rss / 2**20), "ram_used_pct": vm.percent})


def _versions() -> dict:
    from importlib import metadata

    names = ["contract-extractor", "rapidocr", "onnxruntime", "python-doctr", "easyocr", "torch", "pytesseract",
             "opencv-python-headless", "numpy"]
    found = {}
    for name in names:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=sorted(ENGINES))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--threads", type=int, default=2, help="threads per worker process")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample", type=int, help="a fixed random sample of this many contracts (seed 7)")
    parser.add_argument("--form", action="append", choices=["digital", "scan", "photo"])
    parser.add_argument("--gray", action="store_true", help="full read on grayscale, not thresholded, pages")
    parser.add_argument("--claude", action="store_true", help="Claude as the website's fallback (sends text and crops)")
    parser.add_argument("--tag", default="", help="suffix for the run folder")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--stop-at", help="HH:MM local time: start no new contract after it (the run can resume)")
    parser.add_argument("--min-free-gb", type=float, default=3.0, help="pause new contracts below this free RAM")
    args = parser.parse_args()

    if args.claude:  # fail here, not in every worker, when Claude cannot be reached
        from contract_extractor.common.config import load_settings
        from contract_extractor.extractor_method.llm.base import ExtractorUnavailable

        _claude_code_cmd()
        try:
            _claude_extractor(load_settings(None))
        except ExtractorUnavailable as exc:
            raise SystemExit(f"Claude unavailable: {exc}") from exc
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    truth.sort(key=lambda r: r["file_name"])
    if args.form:
        truth = [r for r in truth if r["meta"]["form"] in args.form]
    if args.limit:
        # an even spread over the forms, for a quick trial
        by_form: dict[str, list] = {}
        for r in truth:
            by_form.setdefault(r["meta"]["form"], []).append(r)
        truth = [r for rs in by_form.values() for r in rs[: max(1, args.limit // len(by_form))]]

    if args.sample:
        import random

        truth = sorted(random.Random(7).sample(truth, min(args.sample, len(truth))), key=lambda r: r["file_name"])

    key = args.engine + (f"-{args.tag}" if args.tag else "")
    out = RUNS / key
    out.mkdir(parents=True, exist_ok=True)
    files_path = out / "files.jsonl"
    if (out / "run.json").exists() and not args.fresh:
        before = json.loads((out / "run.json").read_text(encoding="utf-8"))
        before = before if isinstance(before, list) else [before]
        if {s.get("dataset", "output") for s in before} != {DATASET}:
            raise SystemExit(f"{key} was read from another dataset than {DATASET}: use another --tag, or --fresh")
    if args.fresh and files_path.exists():
        files_path.unlink()
    done = set()
    if files_path.exists():
        for line in files_path.read_text(encoding="utf-8").splitlines():
            done.add(json.loads(line)["name"])
    items = [(r["file_name"], safe_folder(r["meta"]["company"])) for r in truth if r["file_name"] not in done]
    print(f"{key}: {len(items)} to read ({len(done)} already done), {args.workers} workers x {args.threads} threads",
          flush=True)

    # the machine's own load before the run, subtracted from the run's CPU use
    psutil.cpu_percent(None)
    idle = [psutil.cpu_percent(interval=1.0) for _ in range(5)]
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    stop_at = None
    if args.stop_at:
        hh, mm = (int(x) for x in args.stop_at.split(":"))
        stop_at = dt.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
    sampler = _Sampler()
    sampler.start()
    started = time.time()
    count = 0
    paused_s = 0.0
    stopped_early = False
    with files_path.open("a", encoding="utf-8") as sink, Pool(
        args.workers, initializer=_init, initargs=(args.engine, args.threads, not args.gray, args.claude)
    ) as pool:
        pending: list = []
        queue = list(items)
        while queue or pending:
            # at most one contract per worker in flight, and none started while the machine is short of memory
            while queue and len(pending) < args.workers:
                if stop_at and time.time() >= stop_at:
                    stopped_early = True
                    queue.clear()
                    break
                if pending and psutil.virtual_memory().available < args.min_free_gb * 2**30:
                    break
                pending.append(pool.apply_async(_one, (queue.pop(0),)))
            done_now = [r for r in pending if r.ready()]
            if not done_now:
                if queue and len(pending) < args.workers:
                    paused_s += 0.5
                time.sleep(0.5)
                continue
            for r in done_now:
                pending.remove(r)
                record = r.get()
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                count += 1
                if count % 20 == 0 or (not queue and not pending):
                    rate = count / max(time.time() - started, 1e-9)
                    left = (len(queue) + len(pending)) / rate if rate else 0
                    pause = f"  (paused for memory {paused_s:.0f} s)" if paused_s else ""
                    print(f"{key}: {count}/{len(items)}  {rate * 3600:.0f} files/h  ~{left / 60:.0f} min left{pause}",
                          flush=True)
    sampler.stop.set()
    sampler.join()
    finished = time.time()

    spec = ENGINES[args.engine]
    run = {
        "engine": args.engine,
        "key": key,
        "dataset": DATASET,
        "label": spec.label + (" (grayscale input)" if args.gray else ""),
        "family": spec.family,
        "gray_input": args.gray,
        "claude": {"model": CLAUDE_MODEL, "effort": CLAUDE_EFFORT} if args.claude else None,
        "workers": args.workers,
        "threads_per_worker": args.threads,
        "files_this_session": count,
        "stopped_early": stopped_early,
        "paused_for_memory_s": round(paused_s, 1),
        "priority": "below normal",
        "started": dt.datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "finished": dt.datetime.fromtimestamp(finished).isoformat(timespec="seconds"),
        "wall_s": round(finished - started, 1),
        "idle_cpu_pct": round(sum(idle) / len(idle), 1),
        "samples": sampler.samples,
        "machine": {
            "cpu": platform.processor(),
            "cores": psutil.cpu_count(logical=False),
            "threads": psutil.cpu_count(logical=True),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "versions": _versions(),
        "model_files": {str(p.name): p.stat().st_size for p in model_files(spec)},
    }
    sessions_path = out / "run.json"
    sessions = json.loads(sessions_path.read_text(encoding="utf-8")) if sessions_path.exists() and not args.fresh else []
    if isinstance(sessions, dict):
        sessions = [sessions]
    sessions.append(run)
    sessions_path.write_text(json.dumps(sessions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{key}: done in {(finished - started) / 60:.1f} min -> {out}", flush=True)


if __name__ == "__main__":
    main()
