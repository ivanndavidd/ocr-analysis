"""Where the benchmark reads from and writes to.

The synthetic set is contract-pdf-generator/output/ (the first 800, 2-page contracts). BENCH_DATASET names another
generated folder beside it, such as output-v2 (multi-page contracts with extra pages):

    BENCH_DATASET=output-v2 .venv/Scripts/python -m bench.run tesseract_second --tag v2

Its summary goes to results-<folder>/, so the first set's results/ stay as they are; give its runs their own --tag.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]  # contract-ocr/ocr-benchmark
ROOT = BENCH.parent  # contract-ocr
GENERATOR = ROOT / "contract-pdf-generator"
DATASET = os.environ.get("BENCH_DATASET", "output")
if not re.fullmatch(r"output(-[A-Za-z0-9_]+)?", DATASET):
    raise SystemExit(f"BENCH_DATASET must be output or output-<name>, a folder in {GENERATOR}")
PDF_DIR = GENERATOR / DATASET / "pdf"  # the 800 synthetic contracts
GROUND_TRUTH = GENERATOR / DATASET / "ground_truth.json"
BACKEND = ROOT / "ocr-analysis" / "apps" / "backend"
MODELS = BENCH / "models"  # every engine's model files, so their size can be measured
RUNS = BENCH / "runs"  # raw results per engine (synthetic data: full NIKs and names)
RESULTS = BENCH / ("results" if DATASET == "output" else f"results-{DATASET}")  # the summary to share
TESSERACT_CMD = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
TESSDATA_FAST = Path("C:/Program Files/Tesseract-OCR/tessdata")  # what production uses today
TESSDATA_BEST = MODELS / "tessdata_best"
