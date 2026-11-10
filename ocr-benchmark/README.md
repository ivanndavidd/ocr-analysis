# OCR benchmark

This folder reads the 800 synthetic PKWT contracts with the product's own extractor, with Claude off. It then scores
every Excel cell against the generator's ground truth, using the same scoring and yellow-mark rules as the website.
It answers two questions:

1. **Which local OCR engine should the product use?** Answered on 2026-11-07, in [part 1](#1-ocr-engine-comparison-2026-11-07).
2. **Does a change to the reader help or hurt?** Every reader change since then was run on all 800 contracts of
   both sets before it was kept. See [part 2](#2-reader-changes-on-the-full-sets-claude-off).

Synthetic data only. `runs/` holds full NIKs and names from the generator, so it is gitignored.

## What we got from it

- **Keep Tesseract, with RapidOCR as a second reader.**
  - RapidOCR (Baidu PP-OCRv6 small) as the main reader ties Tesseract on accuracy but costs about 4x the CPU, uses
    twice the memory, and lets more wrong values through unmarked.
  - As a second reader it confirms the cells Tesseract marks yellow, so it is now built into the extractor.
- **Contracts fully correct went from 622 to 738 of 800** on the first set, with Claude off. On the harder v2 set
  the current reader gets 735 of 800.
- **The biggest gain came from the signature check, not from OCR.** Changing the engine could not fix it. The
  stroke check and the stamp rule cut undecided signatures from 153 to 31.
- **Wrong values that HR would not see stay rare:** 1 cell on the first set and 2 on v2, out of 7,200 each.
- **Reading only the first and last page made it faster without losing accuracy.** The v2 set went from 68 to
  42 minutes.

## 1. OCR engine comparison (2026-11-07)

All 800 contracts of the first set. Scans and photos are the 473 contracts that need OCR; digital PDFs are read from
their text layer and come out the same with every engine.

| Engine | Fully correct (800) | Scans and photos fully correct (473) | Wrong, not marked | CPU s per scan or photo | Memory per worker | Files per hour |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4, fast models (production) | 622 | 296 | 1 | 14 | 324 MB | 1,205 |
| RapidOCR, PP-OCRv6 small | 623 | 297 | 6 | 58 | 738 MB | 549 |

These engines were tried on a 60-contract sample:

| Engine | Fully correct (60) | Why it is out |
| --- | ---: | --- |
| Tesseract 5.4, fast models (baseline) | 39 | |
| Tesseract 5.4, best models | 38 | No better, half the speed, 2 wrong values left green |
| RapidOCR, PP-OCRv6 tiny | 39 | 2 wrong values left green |
| docTR (DBNet + CRNN) | 38 | 11x the CPU and 1.6 GB per worker |
| EasyOCR | 3 of 12 finished | 4.3 GB per worker; needs python-bidi, which is LGPL (forbidden licence) |

Baidu's vision-language models (PaddleOCR-VL, Unlimited-OCR) were not tried. They need a GPU and give no word
positions, which the signature check needs.

**The second reader.** On scans and photos, Tesseract left 317 text cells yellow. RapidOCR read the same value in 269
of them, and all 269 were right. Built into the extractor on 2026-11-08 (`ocr.second_reader`, website
`SECOND_READER=true`):
- scans and photos with nothing to check rose from 135 to 228 of 473;
- yellow NIKs fell from 194 to 8.

Full tables, per field and per form, are in `results/summary.md`. The hand-written findings are in
`results/findings.md`.

These numbers come from the reader as it was on 2026-11-07, before the changes in part 2.

## 2. Reader changes on the full sets (Claude off)

Every run uses Tesseract with the RapidOCR second reader (`tesseract_second`) on all 800 contracts. Each row adds one
change to the row above.

- **Fully correct:** all 9 Excel fields right.
- **Nothing to check:** no yellow cell.
- **Wrong, not marked:** a wrong value without a yellow mark, so HR would not look at it, out of 7,200 cells.

### First set (`output/`: 2-page contracts, 24 job titles)

| Run | Change | Fully correct | Scans and photos (473) | Nothing to check | Signature undecided | Signature wrong | Wrong, not marked |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `tesseract_fast` | Production reader, 2026-11-07 | 622 | 296 | 441 | 153 | 2 | 1 |
| `tesseract_second` | + RapidOCR second reader (11-08) | 622 | 296 | 534 | 153 | 2 | 1 |
| `-sigstroke` | + signature stroke check (11-08) | 712 | 386 | 537 | 53 | 2 | 1 |
| `-namefix` | + name on the signature page, strict name match (11-09) | 712 | 386 | 549 | 53 | 2 | 1 |
| `-dates` | + second reader for unread dates (11-09) | 712 | 386 | 549 | 53 | 2 | 1 |
| `-posfix` | + job title reader fix (11-10) | 721 | 395 | 553 | 53 | 2 | 1 |
| `-firstlast` | + read only the first and last page (11-10) | 721 | 395 | 554 | 53 | 2 | 1 |
| `-stamp` | + stamp (meterai) rule (11-10) | **738** | **412** | 554 | 31 | 6 | 1 |

### v2 set (`output-v2/`: 2 to 6 contract pages plus attachments, 90 job titles)

| Run | Change | Fully correct | Scans and photos (500) | Nothing to check | Signature undecided | Signature wrong | Wrong, not marked |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `tesseract_second-v2` | Reader as of 11-09 | 510 | 292 | 387 | 54 | 6 | 10 |
| `-v2-posfix` | + job title reader fix | 709 | 412 | 529 | 54 | 6 | 2 |
| `-v2-firstlast` | + read only the first and last page | 708 | 411 | 529 | 55 | 6 | 2 |
| `-v2-stamp` | + stamp (meterai) rule | **735** | **438** | 530 | 27 | 7 | 2 |

**Time.** Reading only the first and last page (`-posfix` to `-firstlast`) cut both sets:

| Set | All 800 contracts | Per file, one worker |
| --- | --- | --- |
| First set | 47 to 39 minutes | 10.4 to 8.5 s |
| v2 set | 68 to 42 minutes | 15.0 to 9.1 s |

**Signature wrong.** The stamp rule raised it from 2 to 6 (first set) and 6 to 7 (v2). The extra ones are signed
grey or black-and-white scans whose pen sits on the stamp, read as unsigned. They stay yellow, so HR checks them,
and that is the safe direction.

**Limits.**
- The contracts are synthetic. Real scans may be harder, and nothing here has been tried on real contracts yet;
  that needs approval.
- Claude was off in every run, so these are the local-only numbers. With Claude on, the reader runs through the
  website (`contract-pdf-generator/compare_accuracy.py`).
- The laptop was in normal use, so the minutes are approximate. The CPU-seconds are measured per process.

## Run

From this folder, in Git Bash. The environment is `.venv`, Python 3.11, separate from the product's, because it
holds the extra engines. The extractor is installed in it from `../ocr-analysis/apps/extractor`.

Read a set with one engine, then turn the replies into website rows:

```bash
.venv/Scripts/python -m bench.run tesseract_second --tag mytag
```

```bash
bun bench/to_rows.ts runs/tesseract_second-mytag/files.jsonl
```

Use the v2 set (its summary goes to `results-output-v2/`):

```bash
BENCH_DATASET=output-v2 .venv/Scripts/python -m bench.run tesseract_second --tag v2-mytag
```

Several steps one after another (a stopped run resumes from its `files.jsonl`):

```bash
./tools/queue.sh "rapid_v6_small --form scan --form photo --sample 60 --tag screen2" "tesseract_fast --stop-at 12:00"
```

Score runs and write the summary:

```bash
.venv/Scripts/python -m bench.score tesseract_fast rapid_v6_small
```

```bash
.venv/Scripts/python -m bench.report
```

Run settings: 3 workers x 2 threads at below-normal priority, and no new contract while less than 3 GB of memory is
free, so the laptop stays usable. `--stop-at HH:MM` starts no new contract after that time. Accuracy does not depend
on these settings; wall-clock time does.

## Layout

- `bench/engines.py`: the engines, each behind the extractor's `OCRProvider` interface. `tesseract_second` is the
  product's setup, Tesseract plus the RapidOCR second reader.
- `bench/paths.py`: where it reads and writes. `BENCH_DATASET` picks `output` (default) or `output-<name>`.
- `bench/run.py`: reads contracts with one engine and records the result and its cost.
  - Per file: time, CPU-seconds (including Tesseract's own process), OCR calls and memory.
  - Once a second: machine CPU and the pool's memory.
  - It refuses to add to a run made from another set.
- `bench/to_rows.ts`: turns the worker replies into website rows with `apps/backend` `rows.ts`, so the yellow marks
  match the website.
- `bench/score.py`: scores each run with `compare_accuracy.py`'s own scoring. It writes `results/summary.json`,
  `fields.csv`, `engines.csv`, and `runs/<key>/scored.csv` per contract and field.
- `bench/report.py`: writes `results/summary.md`, with `results/findings.md` at the top.
- `tools/queue.sh`: runs steps one after another, then `to_rows`.
- `tools/check_packages.py`: OSV advisories and licences. `results/packages.txt` holds the result for everything
  installed.
- `tools/install_size.py`: what each engine adds to the installation.
- `stubs/bidi`: a stand-in for python-bidi, an LGPL package that EasyOCR imports but uses only for right-to-left
  scripts.

## Notes

- **OpenCV version.** `python-doctr` asks for `opencv-python`, which replaced the production
  `opencv-python-headless` 4.14 with OpenCV 5.0. It was removed and 4.14 reinstalled, so every engine gets the same
  preprocessing. Check `cv2.__version__` after any install.
- **pyasn1.** Version 0.6.4 has 4 HIGH denial-of-service advisories, fixed in 0.6.5. It is upgraded here, but the
  product's `ocr-analysis/apps/extractor/requirements.lock` still pins 0.6.4.
- **RapidOCR fixes in the reported runs.** `Global.max_side_len` is 2000 (its default), and `tighten()` shrinks every
  word box to its ink, as Tesseract's are, because the signature check measures in word heights. The runs
  `rapid_v6_small-screen`, `-screen-gray` and `rapid_v6_tiny-screen` predate these fixes and are not reported.
