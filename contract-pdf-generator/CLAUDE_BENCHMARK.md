# Claude benchmark checklist

What to do on the day the Claude login is back, to read the synthetic contracts through the website with Claude
helping and score them. It needs the website, so it does not use `ocr-benchmark/`. Only the generated contracts are
read; never point it at `Contract Example/`.

## Before

1. Sign in to Claude Code on this PC with `claude auth login`, and check it with `claude auth status`. The website's
   workers use this login, unless `ANTHROPIC_API_KEY` is set in `ocr-analysis/apps/backend/.env`.
2. In `ocr-analysis/apps/backend/.env`, `CLAUDE_MODE` is empty or `on`, and `FOLDER_ROOT` points at
   `C:/Users/ASUS/OneDrive/kontrak-testing`.
3. Both sets are already in that folder, 800 PDFs each (copied 2026-11-10). If a set was generated again, copy it
   again first:
   - `.venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py` for the first set (`output/`);
   - `.venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --src contract-pdf-generator/output-v2` for v2.

   Don't add `--replace`: it would move the other set's PDFs to the Recycle Bin.
4. Start the website with Ctrl+Shift+B in VS Code. Wait until the backend terminal shows the workers ready.

## Run

Run every command from the `contract-ocr` folder, one at a time. The website reads one run at a time per user.

1. A 10-file check, about 2 minutes:

   ```bash
   .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output-v2/ground_truth.json --limit 10
   ```

   The first line must say `Claude on`, and the report's "Claude tokens" must not be 0.
2. The v2 set, then the first set. Each takes about 1.5 to 2 hours. On 2026-11-04 the first set took 131 min with
   4 workers, before the reader dropped the middle pages.

   ```bash
   .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output-v2/ground_truth.json
   ```

   ```bash
   .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output/ground_truth.json
   ```

## If Claude is not working

The script checks this itself; you don't need to watch it.

- **Before the run.** If the website has Claude switched on but its workers can't reach it, the script won't start
  a run. Without this check the run would quietly read with the text patterns only and look like a Claude run. Fix
  the sign-in, restart the website, and run again.
- **During the run.** If none of the first 10 files reach Claude, or 10 files in a row don't (the sign-in expired
  midway), the script cancels the run on the website. It then prints which kind of call failed.
- **If you stop waiting.** Ctrl+C leaves the run going on the website. Score it later with `--job <id>`.

## Results

Each run writes `contract-pdf-generator/accuracy/<time>/`:

- `report.html`, the report to open in a browser;
- `report.txt`, `detail.csv`, `tokens.csv` and `summary.json`;
- `job.json`, so `--from <folder>` can redo the report later.

Runs started by the script show in the website's history as **Accuracy check**.

Compare with the Claude-off results:

| | Fully correct |
| --- | --- |
| v2 set | 735 / 800 |
| First set | 738 / 800 |

These come from the `ocr-benchmark` runs `tesseract_second-v2-stamp` and `tesseract_second-stamp`, with the same
reader code. The important number is **Wrong, not marked**: a wrong value HR would not see.
