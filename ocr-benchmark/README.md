# OCR engine benchmark

Compares local OCR engines on the 800 synthetic PKWT contracts (`../contract-pdf-generator/output`), through the
production extractor pipeline with Claude off. Only the OCR engine changes; scoring and yellow marks use the same code
as the website accuracy reports. Synthetic data only: `runs/` holds full NIKs and names from the generator.

## Status (2026-11-07, done)

Results: `results/summary.md` (tables for sharing, key findings first), `results/engines.csv`, `results/fields.csv`,
and `runs/<engine>/scored.csv` per contract and field.

- **All 800:** `tesseract_fast` (production today) and `rapid_v6_small` (Baidu PP-OCRv6 small via RapidOCR). A tie
  on contracts fully correct (622 against 623); RapidOCR costs about 4x the CPU. As a second reader it agreed with
  Tesseract on 269 of 317 yellow text cells, with 0 wrong.
- **60-contract samples (seed 7):** `tesseract_fast-screen`, `tesseract_best-screen`, `rapid_v6_small-screen2`,
  `rapid_v6_tiny-screen2`, `doctr_crnn-screen2`, `easyocr-screen2`.
- **Old settings, kept but not reported:** `rapid_v6_small-screen`, `rapid_v6_small-screen-gray`,
  `rapid_v6_tiny-screen` were run before the two RapidOCR fixes below (32 of 60 against 43 after).
- **Fixes applied before the reported RapidOCR runs:** `Global.max_side_len` 2000 (its default), and `tighten()` in
  `bench/engines.py` shrinks every word box to its ink, as Tesseract's are, because the signature check measures in
  word heights.
- **Not run:** `rapid_v6_medium`, `rapid_v5_latin`, `rapid_v4_en`, `doctr_parseq`; Baidu's PaddleOCR-VL and
  Unlimited-OCR (need a GPU, no word boxes).

Run conditions: 3 workers x 2 threads at below-normal priority, no new contract below 3 GB free memory, so the laptop
stays usable. Accuracy does not depend on these settings; wall-clock time does.

## Layout

- `bench/engines.py`: the engines, each behind the extractor's `OCRProvider` interface.
- `bench/run.py`: reads contracts with one engine and records the result and its cost.
  - Settings: 3 workers × 2 threads at below-normal priority (`--workers`, `--threads`), default config,
    automatic decisions allowed. `--stop-at HH:MM` starts no new contract after that time; `--min-free-gb` (3)
    pauses new contracts while memory is short.
  - Records per file: time, CPU-seconds (Tesseract's own program included), OCR calls, memory. Records once a
    second: machine CPU and the pool's memory.
- `bench/to_rows.ts`: turns the worker replies into website rows with `apps/backend` `rows.ts`, so the yellow marks
  match the website.
- `bench/score.py`: scores each run against the ground truth, with `compare_accuracy.py`'s own scoring.
- `bench/report.py`: writes `results/summary.md` and `engines.csv`.
- `tools/queue.sh`: runs steps one after another, each `"<engine> <bench.run args>"`, then `to_rows`. A stopped
  run resumes from its `files.jsonl`.
- `results/findings.md`: the hand-written findings, placed at the top of `summary.md` by `report.py`.
- `tools/check_packages.py`: OSV advisories and licenses for packages. `results/packages.txt` holds the result for
  everything installed.
- `tools/install_size.py`: what each engine adds to the installation.
- `stubs/bidi`: a stand-in for python-bidi, an LGPL package EasyOCR imports but only uses for right-to-left scripts.

## Run

From this folder (Git Bash). The environment is `.venv`, Python 3.11; nothing is installed in `ocr-analysis`.

```bash
./tools/queue.sh "rapid_v6_medium --form scan --form photo --sample 60 --tag screen2" "tesseract_fast --stop-at 12:00"
.venv/Scripts/python -m bench.score tesseract_fast rapid_v6_small tesseract_fast-screen rapid_v6_small-screen2
.venv/Scripts/python -m bench.report
```

## Notes

- `python-doctr` asks for `opencv-python`, which replaced the production `opencv-python-headless` 4.14 with OpenCV
  5.0. It was removed and 4.14 reinstalled, so every engine gets the same preprocessing. Check `cv2.__version__`
  after any install.
- `pyasn1 0.6.4` has 4 HIGH denial-of-service advisories, fixed in 0.6.5. It is upgraded here, but the product's
  `ocr-analysis/apps/extractor/requirements.lock` still pins 0.6.4.
