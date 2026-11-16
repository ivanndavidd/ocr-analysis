# Claude benchmark checklist

What to do on the day the Claude login is back, to read the synthetic contracts through the website with Claude
helping and score them. It needs the website, so it does not use `ocr-benchmark/`. Only the generated contracts are
read; never point it at `Contract Example/`.

## Before

1. Sign in to Claude Code on this PC with `claude auth login`, and check it with `claude auth status`. The website's
   workers use this login, unless `ANTHROPIC_API_KEY` is set in `ocr-analysis/apps/backend/.env`.
2. Update Claude Code with `claude update`. Your organization sets a minimum version, and an older one refuses every
   call. The website then only shows `extractor_error:cli:exit_1` and reads with the text patterns. On 2026-11-11,
   version 2.1.299 was refused because the minimum was 2.1.317.
3. In `ocr-analysis/apps/backend/.env`, `CLAUDE_MODE` is empty or `on`, and `FOLDER_ROOT` points at
   `C:/Users/ASUS/OneDrive/kontrak-testing`.
4. Both sets are already in that folder, 800 PDFs each (copied 2026-11-10). If a set was generated again, copy it
   again first:
   - `.venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py` for the first set (`output/`);
   - `.venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --src contract-pdf-generator/output-v2` for v2.

   Don't add `--replace`: it would move the other set's PDFs to the Recycle Bin.
5. Start the website with Ctrl+Shift+B in VS Code. Wait until the backend terminal shows the workers ready.
6. Once only: the script signs in as "Accuracy check" (`accuracy-check@example.com`), which, like every new user,
   starts inactive. If the 10-file check below stops with "not active yet", sign in to the website as an admin (an
   email in `ADMIN_EMAILS` in `ocr-analysis/apps/backend/.env`), open User, set the Status of Accuracy check to
   Aktif, and run the check again. It stays active after that.

## Run

Run every command from the `contract-ocr` folder, one at a time. The website reads one run at a time per user.

1. A 10-file check, about 2 minutes:

   ```bash
   .venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output-v2/ground_truth.json --limit 10
   ```

   Check two things:
   - The first line says `Claude on`.
   - The report does not end in red with "files had a failed Claude call". If it does, a full run would be mostly
     text patterns.
2. The v2 set, then the first set. Each takes about an hour with 4 workers: the v2 set took 59 min on 2026-11-11.
   Most of that time is the signature crop, sent for every scan and photo.

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
- **During the run.** Claude is only a fallback (owner 2026-11-11), so a file the laptop settles makes no call. If
  none of the first 10 files that call Claude get an answer, or 10 such files in a row don't (the sign-in expired
  midway), the script cancels the run on the website. It then prints which kind of call failed.
- **If you stop waiting.** Ctrl+C leaves the run going on the website. Score it later with `--job <id>`.

## Results

Each run writes `contract-pdf-generator/accuracy/<time>/`:

- `report.html`, the report to open in a browser;
- `report.txt`, `detail.csv`, `tokens.csv` and `summary.json`;
- `job.json`, so `--from <folder>` can redo the report later.

Runs started by the script show in the website's history as **Accuracy check**.

Compare with the earlier results:

| Set | Flow | Fully correct | Wrong, not marked | Claude tokens |
| --- | --- | ---: | ---: | ---: |
| v2 | Claude off | 735 / 800 | 2 | 0 |
| v2 | Claude as fallback (2026-11-11) | 767 / 800 | 3 | 2.0 M |
| v2 | Claude on every file (2026-11-11) | 762 / 800 | 6 | 11.7 M |
| First set | Claude off | 738 / 800 | 1 | 0 |
| First set | Claude as fallback (2026-11-11) | 772 / 800 | 2 | 1.9 M |

The Claude-off rows come from the `ocr-benchmark` runs `tesseract_second-v2-stamp` and `tesseract_second-stamp`,
with the same reader code. The Claude rows are `accuracy/20261111-125049` and `accuracy/20261111-141325` (fallback) and
`accuracy/20261111-104134` (every file, the flow before the owner's 2026-11-11 decision). The important number is **Wrong, not marked**: a
wrong value HR would not see.
