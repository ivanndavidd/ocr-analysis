# Synthetic contract generator

This folder makes fake PKWT contracts (Indonesian fixed-term employment contracts) as PDFs, each with its answer key.
It also sends them through the contract-OCR website and scores what the website read. Every name, NIK and date is
generated (Faker `id_ID`), so the contracts are safe to read, share and send to Claude.

> **Never read, commit or share `Contract Example/`.** It holds real contracts with personal data. It is
> gitignored, and none of these scripts use it.

## What a contract looks like

Each PDF carries the fields the HR Excel row needs:

| Field | Where it is printed |
| --- | --- |
| Employee name | Identity block on the first page, and the signature page |
| NIK (16 digits, KTP layout) | Identity block; matches the printed birth date and gender |
| Position | Identity block, placement or duties clause, or the period clause |
| Contract start and end | One clause on the first page |
| Employee signature (yes or no) | The employee's block on the signature page |
| Vendor | The outsourcing company: letterhead and the folder it is copied into |
| ENTITAS | The filename only: `<name> - <period> - <ENTITAS>.pdf` |

Every contract varies:
- layout, wording, fonts and date formats;
- distractors: HR name and signature, company stamp, meterai, paraf, a signing date, attachments that repeat the
  name;
- form: a digital PDF, a scan (colour, grey or 1-bit, 150 to 300 dpi, slight skew), or a phone photo (distance,
  perspective, desk clutter, a little blur).

## The two sets

| | First set | v2 set |
| --- | --- | --- |
| Folder | `output/` | `output-v2/` |
| Seed | 2026 | 2027 |
| Contract pages | 2 | 2 to 6, plus attachments (2 to 10 pages per PDF) |
| Job titles | 24 | 90 |
| Forms | 327 digital, 256 scan, 217 photo | 300 digital, 255 scan, 245 photo |
| Signed by the employee | 574 | 565 |
| With a meterai | 196 | 211 |
| Made by | the 2-page version (git commit `c1fb955`) | the multi-page version (`-n 800 --seed 2027`) |

Both sets have 800 contracts from 6 vendors. In every PDF the first page is the identity page and the last page is
the signature page. Attachments sit between the clauses and the signature page. Both folders are gitignored.

## Scripts

| Script | What it does |
| --- | --- |
| `contract_generator.py` | Makes a set: `pdf/`, `ground_truth.json`, one JSON per contract, and `labels.csv` |
| `copy_to_onedrive.py` | Copies a set into the website's OneDrive folder, laid out as `<vendor>/<period>/<pdf>`, like the contract SharePoint |
| `compare_accuracy.py` | Runs a set through the website as one OCR job, scores every cell, and writes a report |
| `CLAUDE_BENCHMARK.md` | Checklist for the run with Claude on |
| `local_ab.py`, `local_ab_compare.py` | One-off before-and-after check of the 2026-11-04 fixes, kept for reference |

Run them with the `contract-ocr` venv, from the `contract-ocr` folder. Make a set:

```bash
.venv/Scripts/python contract-pdf-generator/contract_generator.py -n 50 --seed 7 --out contract-pdf-generator/output-try
```

Generating replaces whatever set is already in the `--out` folder, so give a new set its own `output-<name>` folder.
The v2 set was made with `-n 800 --seed 2027`.

Copy it into the website's folder:

```bash
.venv/Scripts/python contract-pdf-generator/copy_to_onedrive.py --src contract-pdf-generator/output-try
```

`--replace` moves PDFs that aren't in this set to the Recycle Bin, so leave it off to keep both sets in the folder.
Both are there now (1,600 PDFs).

Score it through the running website (Ctrl+Shift+B starts it):

```bash
.venv/Scripts/python contract-pdf-generator/compare_accuracy.py --gt output-try/ground_truth.json --limit 10
```

## The accuracy report

`compare_accuracy.py` scores each of the 9 fields of every file as one of four results:
- correct;
- wrong value;
- not found;
- undecided (signature only).

For each field it also records whether the website marked it yellow. A wrong value without a yellow mark is the
number to keep at zero, because HR would not check it.

It writes to `accuracy/<time>/` (gitignored):
- `report.html`, to open or share;
- `report.txt`;
- `detail.csv`, one row per file and field;
- `tokens.csv`, one row per Claude call;
- `summary.json`;
- `job.json`, so `--from <folder>` can redo the report later.

It signs in to the website as a made-up user, "Accuracy check" (`accuracy-check@example.com`). If the website has
Claude switched on but can't reach it, the script won't start a run. It also cancels a Claude run when Claude stops
answering. Details are in `CLAUDE_BENCHMARK.md`.

## What we got from it

| Date | Run | Result |
| --- | --- | --- |
| 2026-11-03 | 50 files, with Claude | 34 fully correct, in 10 minutes |
| 2026-11-04 | 800 files, first set, with Claude, reader of that day | 772 fully correct; 131 minutes; 11.2 M tokens |
| 2026-11-04 | 800 files, no-OCR test mode | 781 fully correct; 47 minutes; 12.5 M tokens |
| 2026-11-10 | 10 files per set, Claude off, current reader | 20 of 20 fully correct; all 180 cells match the benchmark |

The no-OCR test mode sends the whole first and last page to Claude without masking. It is a test only and not
approved for real contracts.

The sets also feed `../ocr-benchmark`, which measures every reader change on all 800 contracts with Claude off. With
the current reader:
- first set: 738 of 800 fully correct;
- v2 set: 735 of 800 fully correct.

**Still to do:**
- the Claude-on run with the current reader, waiting for the Claude login;
- a check on real contracts, which needs approval first.
