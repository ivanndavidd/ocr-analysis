"""Where the benchmark reads from and writes to."""

from __future__ import annotations

from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]  # contract-ocr/ocr-benchmark
ROOT = BENCH.parent  # contract-ocr
GENERATOR = ROOT / "contract-pdf-generator"
PDF_DIR = GENERATOR / "output" / "pdf"  # the 800 synthetic contracts
GROUND_TRUTH = GENERATOR / "output" / "ground_truth.json"
BACKEND = ROOT / "ocr-analysis" / "apps" / "backend"
MODELS = BENCH / "models"  # every engine's model files, so their size can be measured
RUNS = BENCH / "runs"  # raw results per engine (synthetic data: full NIKs and names)
RESULTS = BENCH / "results"  # the summary to share
TESSERACT_CMD = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
TESSDATA_FAST = Path("C:/Program Files/Tesseract-OCR/tessdata")  # what production uses today
TESSDATA_BEST = MODELS / "tessdata_best"
