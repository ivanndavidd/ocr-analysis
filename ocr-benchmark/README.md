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
  - Rerun on 2026-11-11 with the current reader, RapidOCR as the main reader gets more scans and photos fully
    correct (430 vs 412, and 464 vs 438 on v2). But it lets more wrong values through unmarked (7 vs 1, 4 vs 2),
    leaves about 20 times as many NIKs yellow, and costs 1.5 to 1.8x the CPU. See
    [the rerun](#rerun-with-the-current-reader-2026-11-11).
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

### Rerun with the current reader (2026-11-11)

RapidOCR as the main reader again, now with every change from part 2 (first and last page only, stamp rule),
against `tesseract_second-stamp` and `tesseract_second-v2-stamp` on the same scans and photos. Runs
`rapid_v6_small-img` and `rapid_v6_small-v2-img`.

| | First set, RapidOCR | First set, Tesseract + second | v2, RapidOCR | v2, Tesseract + second |
| --- | ---: | ---: | ---: | ---: |
| Scans and photos | 473 | 473 | 500 | 500 |
| Fully correct | **430** | 412 | **464** | 438 |
| Nothing to check | 158 | **248** | 138 | **247** |
| Wrong, not marked (cells) | 7 | **1** | 4 | **2** |
| Yellow NIKs | 172 | **8** | 209 | **10** |
| Job titles wrong | 5 | 3 | **2** | 7 |
| Signatures wrong | 13 | **6** | **3** | 7 |
| CPU s per file | 32.0 | **17.5** | 27.4 | **17.9** |
| Worker peak memory | 744 MB | 747 MB | 754 MB | 749 MB |

- RapidOCR reads more values right, mostly job titles and signatures on v2.
- Its NIKs stay yellow because no second engine confirms them, so HR would check about 90 more rows per set.
- It lets more wrong values through unmarked, which is the number this product protects most.
- Memory is the same, since the product already loads RapidOCR as its second reader.

So Tesseract stays the main reader.

### Every engine order, with and without Claude (2026-11-13)

Six setups on the scans and photos of both datasets. Digital PDFs read the same in every setup.
- **Tesseract alone / RapidOCR alone:** one engine, no second reader.
- **Tesseract → RapidOCR:** the product's reader. RapidOCR rereads the identity page when Tesseract's NIK or job
  title is unsure, or a date is missing.
- **RapidOCR → Tesseract:** the same with the roles swapped (`rapid_second`, benchmark only).
- **… → Claude:** the same, with Claude as the website's fallback (`--claude`: same model, effort and rules as the
  website). Tesseract → RapidOCR → Claude was the flow until 2026-11-16, when the owner chose RapidOCR first.

| Setup | First dataset (473): fully correct | Nothing to check | Wrong, not marked | Second dataset (500): fully correct | Nothing to check | Wrong, not marked | Claude tokens (both) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract alone | 412 | 139 | 1 | 438 | 135 | 2 | 0 |
| RapidOCR alone | 430 | 158 | 7 | 464 | 138 | 4 | 0 |
| Tesseract → RapidOCR | 412 | 248 | 1 | 438 | 247 | 2 | 0 |
| RapidOCR → Tesseract | 430 | 241 | 7 | 464 | 238 | 4 | 0 |
| Tesseract → RapidOCR → Claude | **447** | **271** | **1** | 468 | **267** | **2** | 3.5 M |
| RapidOCR → Tesseract → Claude | 441 | 255 | 6 | **482** | 260 | 3 | 3.0 M |

- **The second reader changes no value**, either way round: Tesseract alone and Tesseract → RapidOCR read the same
  value in every cell of all 973 contracts, and so do RapidOCR alone and RapidOCR → Tesseract. It only turns cells
  green where both engines agree, so about 100 more contracts per dataset need no check, and NIKs left yellow drop
  from about 200 to about 10 per dataset. It roughly doubles Tesseract's CPU: 8.7 to 16.8 seconds per file.
- **Alone, RapidOCR reads more right** (894 against 850 fully correct; the contracts only one gets right split 82
  against 38, sign test p < 0.001). But it leaves more wrong values unmarked (11 against 3) and costs about 3.5 times
  the CPU (30.1 against 8.7 seconds per file).
- **With Claude, the two orders tie on fully correct:** 923 against 915. The contracts only one order gets right
  split 40 against 32 (p 0.41). RapidOCR first is ahead on the second dataset (24 against 10, p 0.02) and behind on
  the first (16 against 22).
- **RapidOCR first leaves more wrong values unmarked:** 9 against 3. Most are job titles RapidOCR misread with high
  confidence (`Operator Produksl`, `Custoimer Service`, `TelemarketIng`, `Staff Adminiştrasi`), so neither Tesseract
  nor Claude is asked. The flow now leaves `Kurlr`, `Sales Executi Cutive` and one signed contract read unsigned. It
  also leaves 23 more rows HR can skip than RapidOCR first, and uses 16.9 CPU seconds per file against 36.3.

- **Signature agreement rule (owner decision 2026-11-16):** a signature the ink check calls not clear ("Kurang
  jelas") turns green when Claude's look at the crop agrees at 0.85 or more. Replayed on the saved answers of the
  Claude runs above (`-agree` runs, no new calls): contracts with nothing to check 538 to 881 for the flow now and
  515 to 920 for RapidOCR first; wrong cells not marked 3 to 4 and 9 to 10. `tools/comparison.py` shows these.

So Tesseract stayed first until 2026-11-16. A local check that marks a title with an odd letter or an unknown word as
unsure would send those titles to the second reader and Claude, in either order; it is not built.

### "Claude baca halaman" on every scan and photo (2026-11-16)

The website mode the owner chose on 2026-11-16 (`bench.run rapid_second --claude --page-image`, 8 workers like the
other Claude runs; `rapid_second-img-claude-page`, `rapid_second-v2-img-claude-page`): RapidOCR first and Tesseract
second for the NIK and the signature, as in RapidOCR → Tesseract → Claude, and Claude reads the name, dates, job title
and employer from the identity page image with the NIK blacked out, for every file. Compared with the two Claude
flows above with the signature agreement rule applied (`-agree`), since the page runs had it live:

| Setup | First dataset (473): fully correct | Nothing to check | Wrong, not marked | Second dataset (500): fully correct | Nothing to check | Wrong, not marked | Claude tokens (both) | CPU s / file |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract → RapidOCR → Claude | 447 | 431 | 2 | 468 | 450 | 2 | 3.5 M | 16.9 |
| RapidOCR → Tesseract → Claude | 441 | 442 | 7 | 482 | **478** | 3 | 3.0 M | 36.3 |
| Claude baca halaman | **451** | **435** | **2** | **487** | 465 | **1** | 13.1 M | 34.7 |

- **Most fully correct of every setup:** 938 of 973, against 923 and 915; cells correct 99.54%. Wrong cells marked
  yellow drop to 7 (46 and 21).
- **Every job title and every date right** (973 of 973 each): Claude reads the title from the image, so RapidOCR's
  confident title typos (`Operator Produksl`, `Custoimer Service`) and Tesseract's (`Kurlr`) never reach the row.
- **3 wrong values not marked:** two signed contracts read unsigned (Jane Mandasari, also in RapidOCR → Tesseract →
  Claude; Radit Indra Unggul Mariadi, yellow in the other flows) and one NIK. RapidOCR reads `…0524` for `…0824`
  (SADINA NILAM RINI) in every RapidOCR-first setup: the digit is in the last four, which the birth date and gender
  cannot check, so it stays green. Tesseract first reads it right.
- **Cost:** about 4 times the Claude tokens of the fallback flows (13.4k per scan or photo), since every file sends
  the page. 90 minutes with 8 workers (42.7 and 47.7).
- **Timeouts:** under 8 workers, 10 of the 973 page calls ran past the 120-second extractor timeout
  (`extractor.timeout_s`); those files fell back to the masked-text flow, as designed, and were read again with 2
  workers for this table (`files-with-timeouts.jsonl` keeps the first answers).

### Full Claude on every scan and photo (2026-11-17, NIK prompt 2026-11-18)

The website's removed "Tanpa OCR" mode (2026-11-04 to 2026-11-16), restored for the benchmark only in
`bench/full_page.py` (`bench.run tesseract_fast --claude --full-page`; the engine only names the run folder, no OCR
runs): the first and last page go to Claude whole and unmasked, and Claude reads every field, the NIK and the
signature included; the laptop only checks its NIK against the birth date and gender Claude read, its name and period
against the filename, and its employer against the vendor folder. The table and `tools/comparison.py` show the run
with the NIK asked in four groups of four (`*-img-claude-fullpage-groups`, 8 workers, 29 minutes for all 973, no
failed call); the first run with the original wording is in the last bullet.

| Setup | First dataset (473): fully correct | Nothing to check | Wrong, not marked | Second dataset (500): fully correct | Nothing to check | Wrong, not marked | Claude tokens (both) | CPU s / file | Peak RAM / worker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Claude baca halaman | 451 | **435** | 2 | **487** | **465** | 1 | 13.1 M | 34.7 | 1,096 MB |
| Full Claude | **457** | 318 | **0** | 470 | 316 | 4 | 15.6 M | **2.6** | **502 MB** |

- **Fewer fully correct than Claude baca halaman** (927 against 938), behind on the second dataset (470 against
  487), where contracts run 2 to 6 pages: NIKs 489 against 496 and signatures 481 against 488.
- **Far more yellow:** 634 contracts with nothing to check against 900. A NIK only turns green when both the
  printed birth date and gender confirm it, and many pages print only one.
- **Its 4 unmarked mistakes are worse ones:** three unsigned contracts read as signed (Balangga, Kawaca Adriansyah,
  Tira Laila), the direction that lets an unsigned contract through, and one NIK digit in the region code (`6333…`
  for `6332…`), which the birth date and gender cannot check.
- **Cheapest on the laptop** (no OCR): 2.6 CPU seconds and about 500 MB per worker, measured on the full run;
  the most Claude tokens (16k per file), and the whole pages leave unmasked.
- The first dataset agrees with the website run of 2026-11-04 (457 fully correct, 0 unmarked on the same 473).
- **Why the NIK misses (probe 2026-11-18):** asked again, Claude explained its empty NIKs itself: it read 15 digits,
  dropping one of a pair or run of the same digit (`…1080 0075` read `…108 0075`), and the prompt forbids guessing.
  Its wrong NIKs change a repeated digit (`3331…` read `3333…`).
- **NIK in four groups of four (owner 2026-11-18, `*-img-claude-fullpage-groups`):** on the 18 NIKs it missed, read
  twice per prompt, right rose from 14 to 20 of 36, but on all 973 it changed almost nothing: fully correct 927
  (926), NIKs 956 (955; 4 fixed, 3 newly wrong, 14 wrong in both), wrong not marked 4 (3: Claude's signature answer
  for Tira Laila went from unsure to a wrong "signed"), 15.6 M tokens. Claude's NIK reading varies from run to run,
  so the targeted gain was mostly that variation.

The flow now was also run through the website on 2026-11-11 (446 and 469 fully correct, 2 and 2 unmarked); Claude
answers a little differently each run. Three signature calls timed out in RapidOCR → Tesseract → Claude and those
files were read again.

**CPU seconds per file** are measured for every setup on the same 100 contracts (`--form scan --form photo --sample 50`
on each dataset) with the laptop lightly loaded (machine CPU 38 to 56%). The full runs of RapidOCR → Tesseract and of
both Claude flows kept all 8 cores busy, which inflates CPU seconds, so those three were read again on the sample
(`*-cpu50` runs, 2026-11-16). On that sample, Tesseract alone measured 9.1 against 8.8 in its full run, so the sample
gives the same figure. Claude adds little CPU on the laptop: its work runs at Anthropic; it costs tokens and waiting.

| Setup | CPU s / file |
| --- | ---: |
| Tesseract alone | 8.7 |
| RapidOCR alone | 30.1 |
| Tesseract → RapidOCR | 16.8 |
| RapidOCR → Tesseract | 31.4 |
| Tesseract → RapidOCR → Claude (the flow until 2026-11-16) | 16.9 |
| RapidOCR → Tesseract → Claude | 36.3 |
| "Claude baca halaman" (RapidOCR → Tesseract, Claude reads the identity page image) | 34.7 |

The website mode "Claude baca halaman" (owner 2026-11-16) was measured on the same sample the same day
(`rapid_second-cpu50-page`, `rapid_second-v2-cpu50-page`: `bench.run rapid_second --claude --page-image`, 4 workers,
machine CPU 33 to 39%). Its local work is that of RapidOCR → Tesseract → Claude, so its CPU is the same within noise;
the cost of RapidOCR first is the jump from 16.9. On the 100 contracts:

| Setup | CPU s / file | Peak RAM / worker | Claude tokens / file | Fully correct | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tesseract → RapidOCR → Claude | 16.9 | 1,009 MB | 3,937 | 96 | 0 |
| RapidOCR → Tesseract → Claude | 36.3 | 1,025 MB | 2,982 | 98 | 0 |
| Claude baca halaman | 34.7 | 1,034 MB | 13,489 | 99 | 0 |

The two older rows were read before the signature agreement rule, so their "nothing to check" (58 and 55) is not
comparable with the page mode's 95, which ran with it. CPU includes the Claude Code process the local login starts per
call (bench.run counts every child process); with an API key (production) there is none.

All of it, per field and with every unmarked mistake: `.venv/Scripts/python tools/comparison.py`
(`results/comparison.txt`).

### Every setup on the digital PDFs (2026-11-18)

The 627 native PDFs of both datasets (327 and 300), all eight setups, 8 workers (`runs/digital-all.sh`; run keys
`<engine>-digital`, `-digital-claude`, `-digital-claude-page`, `-digital-claude-fullpage`, `v2-` before `digital` for
the second dataset; no failed call). A native PDF has a text layer, so no OCR engine reads it: the four local setups
are the same reader (text layer plus the patterns), and the engine only names the run. When Claude is in the flow the
identity page goes as text (owner 2026-11-18): Claude baca halaman sends its masked text layer for every file instead
of the page image; Full Claude sends the first page's text unmasked and the last page as an image, for the signature.

| Setup | Fully correct (627) | Cells correct | Nothing to check | Wrong, not marked | Claude tokens | CPU s / file | Peak RAM / worker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract alone, RapidOCR alone, either order | 623 | 99.93% | 589 | 0 | 0 | 1.1 to 1.2 | 195 to 212 MB |
| Tesseract → RapidOCR → Claude | **627** | **100%** | **596** | **0** | **0.53 M** | **1.8** | **490 MB** |
| RapidOCR → Tesseract → Claude | **627** | **100%** | **596** | **0** | **0.53 M** | 1.9 | 543 MB |
| Claude baca halaman | **627** | **100%** | **596** | **0** | 8.47 M | 3.9 | 590 MB |
| Full Claude | 621 | 99.89% | 451 | 1 | 8.35 M | 2.8 | 501 MB |

- **Every miss is a signature.** Name, NIK, title, dates, vendor and entity are right in every setup. The four
  local-only setups leave 4 signatures undecided ("Kurang jelas", yellow); the signature crop to Claude settles them
  in the three flows that use it.
- **Claude baca halaman adds nothing on digital PDFs:** the same 627 and 596 as the fallback flows, at 16 times the
  tokens (13.5k per file against 0.85k), because every file's identity text goes to Claude while the fallback flows
  send only the signature crops and unsure fields. The text layer is exact, so there is nothing for Claude to correct.
- **Full Claude is the only setup with a silent mistake:** an unsigned contract read as signed (Dian, first dataset),
  plus 5 signatures it was unsure of. It also leaves the most yellow: 163 NIKs (green only when both the printed birth
  date and gender confirm it), 16 job titles and 5 signatures, against 31 signatures in the Claude flows.
- CPU and RAM are from the full runs (8 workers), on the same 50 digital PDFs per dataset for every setup.

`PYTHONIOENCODING=utf-8 .venv/Scripts/python tools/comparison.py --digital` (`results/comparison-digital.txt`).

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

Claude as the website's fallback (`--claude`, through the Claude Code login set in `CLAUDE_CODE_CMD` of
`../ocr-analysis/apps/backend/.env`; it sends masked text and crops to Claude, synthetic contracts only):

```bash
./tools/queue.sh "rapid_second --claude --form scan --form photo --workers 5 --tag img-claude"
```

Several setups side by side on the same contracts, benchmark runs or website reports (`label=source`, the first one
picks the contracts; a report may add `+<job.json>` of a later rerun):

```bash
PYTHONPATH=. .venv/Scripts/python tools/compare_runs.py "Rapid first=rapid_second-img" "Tesseract first=tesseract_second-stamp"
```

Run settings: 3 workers x 2 threads at below-normal priority, and no new contract while less than 3 GB of memory is
free, so the laptop stays usable. `--stop-at HH:MM` starts no new contract after that time. Accuracy does not depend
on these settings; wall-clock time does.

## Layout

- `bench/engines.py`: the engines, each behind the extractor's `OCRProvider` interface. `tesseract_second` is the
  product's setup, Tesseract plus the RapidOCR second reader; `rapid_second` swaps the roles.
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
- `tools/compare_engines.py`, `tools/compare_runs.py`: two or more runs side by side, per field, with cost.
- `tools/comparison.py`: every setup tried on both datasets in one report (`SETUPS` lists them), with the rich
  library: all setups on both datasets together, correct values per field, and every wrong value left unmarked.
  It writes `results/comparison.txt`, with NIKs in full (synthetic data only).
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
