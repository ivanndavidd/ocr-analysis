# OCR engine benchmark: 800 synthetic PKWT contracts

Every engine reads the same contracts through the production extractor pipeline (same page selection, preprocessing, patterns, NIK checks and signature analysis); only the OCR engine changes. **Claude is off**: everything is read on the laptop, so these numbers are the local-only baseline, lower than the website with Claude helping. Scoring and yellow marks use the website's own code.

Machine: AMD Ryzen 7 7735HS with Radeon Graphics, 8 cores / 16 threads, 15.2 GB RAM, Windows-10-10.0.26200-SP0. Every engine runs on CPU only. Full runs: 3 worker processes x 2 threads each at below-normal priority, while the laptop was in normal use, so wall-clock times carry some noise; CPU-seconds are measured per process (Tesseract's own program included) and are not affected by other apps.

Digital PDFs (327 of 800) are read from their text layer, so they come out the same whatever the engine; the engines differ on the 473 scans and phone photos.

## Key findings

**Recommendation: keep Tesseract as the reader. Add RapidOCR (Baidu's PP-OCRv6 small) only as a second reader for the cells Tesseract marks yellow.**

1. **As a full replacement, RapidOCR is a tie that costs about four times as much.** Of the 473 scans and photos, 297 are fully correct with RapidOCR and 296 with Tesseract (623 and 622 of all 800). Each engine gets 26 or 27 contracts right that the other gets wrong (paired test p = 1.0, no difference). RapidOCR also needs:
   - 58 CPU-seconds per scan or photo, against 14 for Tesseract;
   - 738 MB per worker, against 324 MB;
   - more time: 549 files per hour against 1,205.

   It also shows **6 wrong values without a yellow mark**, against 1 for Tesseract. All 5 wrong positions are letter slips that look plausible ("Produksl", "Custoimer Service", one line run into the next) and that our checks do not catch.
2. **The two engines are good at different things.**
   - RapidOCR reads text better: NIK 471 against 465, position 466 against 447, and fewer yellow cells (172 yellow NIKs against 194, 2 yellow positions against 44).
   - Tesseract's word boxes suit the signature check better: 319 against 300 signatures correct.
3. **As a second reader, RapidOCR settles most yellow text cells and was never wrong when the two engines agreed.** On scans and photos, Tesseract leaves 317 text cells yellow (NIK, position, dates, months). RapidOCR read the same value in 269 of them, and all 269 were correct:
   - 186 of the 194 yellow NIKs;
   - 19 of 44 positions;
   - 14 of 18 start dates and 19 of 25 end dates.

   Turning agreed cells green would raise the scans and photos needing no check from **135 to 242 of 473**, with no Claude call and no wrong value let through.

   When the two engines disagree (48 cells, 8 of them NIKs), the cell stays yellow as today. In 46 of those 48 RapidOCR's value was the right one, Tesseract's never was, and in 2 neither was right. So RapidOCR's value is a good pre-filled suggestion for whoever checks the cell. It must not turn the cell green on its own: those 2 would then go through wrong. Today the website sends a crop of the NIK line to Claude for exactly this. The second reader only has to read the yellow lines, not whole pages, so its cost is a small share of the 58 CPU-seconds above.
4. **Changing the OCR engine does not fix the biggest gap: signatures.** Of the 177 scans and photos Tesseract does not get fully right, 154 fail on the signature, most left undecided (yellow). Any gain there must come from the signature check itself, not from the OCR engine.
5. **Other candidates:**
   - **Tesseract with the larger "best" models:** no better (38 against 39 of the 60-contract sample), with 2 wrong values on green, and about half the speed.
   - **PP-OCRv6 tiny:** matches Tesseract on the sample (39 of 60), but leaves 2 wrong values on green.
   - **docTR (DBNet + CRNN):** no better than Tesseract (38 against 39 of 60; NIK 58 against 60, position 49 against 54), with 2 wrong values on green. It takes about 160 CPU-seconds per scan (11× Tesseract) and 1.6 GB per worker.
   - **EasyOCR:** only 12 of the 60 finished before the time limit, at about 470 CPU-seconds per scan and 4.3 GB per worker. On those 12 it got 3 fully right against Tesseract's 8. It is out on speed, memory and licence alone.
   - **Baidu's newer vision-language models (PaddleOCR-VL 1.6, Unlimited-OCR 2):** not tested. They need a GPU, and they return page text without word positions, which the signature check needs.
6. **Packages and licences.**
   - RapidOCR adds 90 MB of packages (onnxruntime and others, MIT/Apache/BSD; tqdm is MPL-2.0) plus 30 MB of models. No known vulnerabilities (OSV).
   - docTR and EasyOCR each add about 800 MB, mostly PyTorch.
   - EasyOCR depends on python-bidi, which is LGPL (on our forbidden list), so it cannot be deployed as it is.

**Caveats**

- The contracts are synthetic. Real scans may be harder, and the agreement result should be re-checked on a small set of real, approved samples before it is relied on.
- "Never wrong" rests on 269 cells. With 0 errors in 269, the true error rate could still be up to about 1.1% (95% upper bound); for NIK alone, up to about 1.6% (0 in 186).
- The yellow-mark rules were tuned on Tesseract's mistakes, which favours Tesseract on "wrong, not marked". RapidOCR as the main reader would need its own checks, for example positions matched against a known list.
- Claude was off for every engine, so all figures are the local-only baseline.
- The laptop was in normal use during the runs, so wall-clock times are approximate. The CPU-seconds are measured per process and are reliable.
- 60-contract samples can only show large differences; a gap of 4 contracts in 60 is within chance.

## 1. Headline: all 800 contracts

| Engine | Files fully correct | Values correct | Wrong, not marked | Files with no yellow | s per scan/photo | CPU-s per scan/photo | Files per hour | Peak MB per worker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 622 / 800 (77.8%) | 97.2% | 1 | 441 (0 wrong) | 14.0 | 14.0 | 1,205 | 324 |
| RapidOCR, PP-OCRv6 small | 623 / 800 (77.9%) | 97.3% | 6 | 462 (0 wrong) | 32.1 | 58.3 | 549 | 738 |

## 2. Scans and photos only (473 contracts)

| Engine | Files fully correct | Values correct | Wrong, not marked | Files with no yellow | s per scan/photo | CPU-s per scan/photo | Files per hour | Peak MB per worker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 296 / 473 (62.6%) | 95.2% | 1 | 135 (0 wrong) | 14.0 | 14.0 | 1,205 | 324 |
| RapidOCR, PP-OCRv6 small | 297 / 473 (62.8%) | 95.5% | 6 | 156 (0 wrong) | 32.1 | 58.3 | 549 | 738 |

### Per field, scans and photos: correct (wrong and not marked)

| Engine | Name | NIK | Position | Start | End | Months | Signature |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 473 (0) | 465 (0) | 447 (1) | 469 (0) | 467 (0) | 468 (0) | 319 (0) |
| RapidOCR, PP-OCRv6 small | 473 (0) | 471 (0) | 466 (5) | 470 (0) | 470 (0) | 470 (0) | 300 (1) |

#### Name (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 473 / 473 | 0 | 0 | 0 | 0 | 0 |
| RapidOCR, PP-OCRv6 small | 473 / 473 | 0 | 0 | 0 | 1 | 0 |

#### NIK (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 465 / 473 | 1 | 7 | 0 | 194 | 0 |
| RapidOCR, PP-OCRv6 small | 471 / 473 | 2 | 0 | 0 | 172 | 0 |

#### Position (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 447 / 473 | 8 | 18 | 0 | 44 | 1 |
| RapidOCR, PP-OCRv6 small | 466 / 473 | 5 | 2 | 0 | 2 | 5 |

#### Start (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 469 / 473 | 0 | 4 | 0 | 18 | 0 |
| RapidOCR, PP-OCRv6 small | 470 / 473 | 0 | 3 | 0 | 13 | 0 |

#### End (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 467 / 473 | 2 | 4 | 0 | 25 | 0 |
| RapidOCR, PP-OCRv6 small | 470 / 473 | 0 | 3 | 0 | 10 | 0 |

#### Months (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 468 / 473 | 2 | 3 | 0 | 36 | 0 |
| RapidOCR, PP-OCRv6 small | 470 / 473 | 0 | 3 | 0 | 16 | 0 |

#### Signature (scans and photos)

| Engine | Correct | Wrong value | Not found | Undecided | Marked yellow | Wrong, not marked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 319 / 473 | 2 | 0 | 152 | 216 | 0 |
| RapidOCR, PP-OCRv6 small | 300 / 473 | 1 | 0 | 172 | 229 | 1 |

### Files fully correct by scan and photo type

| Engine | Photo, slightly blurry | Photo, clean | Photo, objects on the desk | Scan, black-and-white (1-bit) | Scan, colour | Scan, grey |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 3 / 6 | 77 / 117 | 66 / 94 | 35 / 59 | 58 / 96 | 57 / 101 |
| RapidOCR, PP-OCRv6 small | 2 / 6 | 73 / 117 | 64 / 94 | 38 / 59 | 60 / 96 | 60 / 101 |

## 3. A second local reader beside Tesseract

On the 473 scans and photos: how often the engine reads exactly the same value as today's Tesseract, and whether that shared value is ever wrong. The last three columns are the cells Tesseract leaves yellow (Perlu dicek) without Claude, and how many of them the second engine confirms with the same value: today the website sends a crop of the NIK line to Claude for this.

| Engine | Field | Scans and photos | Same value as Tesseract | Same but wrong | Tesseract yellow | Confirmed, right | Confirmed, wrong |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RapidOCR, PP-OCRv6 small | NIK | 473 | 464 (98.1%) | 0 | 194 | 186 | 0 |
| RapidOCR, PP-OCRv6 small | Position | 473 | 441 (93.2%) | 0 | 44 | 19 | 0 |
| RapidOCR, PP-OCRv6 small | Start | 473 | 466 (98.5%) | 0 | 18 | 14 | 0 |
| RapidOCR, PP-OCRv6 small | End | 473 | 464 (98.1%) | 0 | 25 | 19 | 0 |

## 4. Resources

| Engine | Wall time (800) | Files per hour | Mean CPU % | CPU-s per file | OCR calls per scan/photo | s per OCR call | Peak MB, Python worker | Peak MB, Tesseract program | Pool peak MB | Start-up s | Model MB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 40 min | 1,205 | 26 | 8.7 | 4.1 | 2.31 | 228 | 96 | 1,032 | 0.9 | 5.0 |
| RapidOCR, PP-OCRv6 small | 87 min | 549 | 43 | 34.9 | 4.0 | 6.94 | 738 | - | 1,639 | 1.2 | 29.8 |

### Installation size beyond today's extractor

| Engine family | Extra packages | MB |
| --- | --- | ---: |
| tesseract | Tesseract 5.4 program (without language data) | 223.5 |
| rapidocr | colorlog, flatbuffers, omegaconf, onnxruntime, protobuf, pyclipper, rapidocr, shapely, six, tqdm | 90.1 |
| doctr | anyascii, click, filelock, fsspec, h5py, hf-xet, huggingface-hub, jinja2, langdetect, markupsafe, ml-dtypes, mpmath, networkx, onnx, protobuf, pyclipper, python-doctr, scipy, setuptools, shapely, six, sympy, torch, torchvision, tqdm, validators | 820.1 |
| easyocr | easyocr, filelock, fsspec, imageio, jinja2, lazy-loader, markupsafe, mpmath, networkx, pyclipper, scikit-image, scipy, setuptools, shapely, sympy, tifffile, torch, torchvision | 804.4 |

## 5. Screening: every variant on the same 60 random scans and photos

Speed columns are only roughly comparable here: the runs had different worker counts and priorities (last column), and the earlier Tesseract samples count CPU machine-wide, not per process. Section 4 has the like-for-like numbers.

| Variant | Files fully correct | Values correct | Wrong, not marked | Files with no yellow | s per scan/photo | CPU-s per scan/photo | Files per hour | Peak MB per worker | Workers x threads, priority |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Tesseract 5.4 (fast models, production today) | 39 / 60 (65.0%) | 95.6% | 0 | 21 (0 wrong) | 12.7 | 13.6 | 1,095 | 210 | 4x2, normal |
| Tesseract 5.4 (best models) | 38 / 60 (63.3%) | 95.2% | 2 | 20 (1 wrong) | 27.2 | 27.6 | 515 | 208 | 4x2, normal |
| RapidOCR, PP-OCRv6 small | 43 / 60 (71.7%) | 96.9% | 0 | 21 (0 wrong) | 36.7 | 66.5 | 288 | 741 | 3x2, below normal |
| RapidOCR, PP-OCRv6 tiny | 39 / 60 (65.0%) | 95.9% | 2 | 18 (1 wrong) | 13.9 | 22.4 | 745 | 571 | 3x2, below normal |
| docTR 1.1 (DBNet ResNet-50 + CRNN VGG16) | 38 / 60 (63.3%) | 95.0% | 2 | 19 (0 wrong) | 79.3 | 161.1 | 132 | 1,623 | 3x2, below normal |
| EasyOCR 1.7 (Indonesian + English) | 3 / 12 (25.0%) | 90.7% | 0 | 1 (0 wrong) | 251.3 | 470.8 | 39 | 4,336 | 3x2, below normal |

Each sample run beside Tesseract on exactly the same contracts (runs stopped at the time limit cover fewer than 60):

| Variant | Contracts | Fully correct | Fully correct, Tesseract | Wrong, not marked | Wrong, not marked, Tesseract |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 60 | 39 | 39 | 0 | 0 |
| Tesseract 5.4 (best models) | 60 | 38 | 39 | 2 | 0 |
| RapidOCR, PP-OCRv6 small | 60 | 43 | 39 | 0 | 0 |
| RapidOCR, PP-OCRv6 tiny | 60 | 39 | 39 | 2 | 0 |
| docTR 1.1 (DBNet ResNet-50 + CRNN VGG16) | 60 | 38 | 39 | 2 | 0 |
| EasyOCR 1.7 (Indonesian + English) | 12 | 3 | 8 | 0 | 0 |

Per field: correct (wrong and not marked)

| Engine | Name | NIK | Position | Start | End | Months | Signature |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tesseract 5.4 (fast models, production today) | 60 (0) | 60 (0) | 54 (0) | 59 (0) | 59 (0) | 59 (0) | 45 (0) |
| Tesseract 5.4 (best models) | 60 (0) | 60 (0) | 52 (2) | 59 (0) | 59 (0) | 59 (0) | 45 (0) |
| RapidOCR, PP-OCRv6 small | 60 (0) | 60 (0) | 60 (0) | 60 (0) | 60 (0) | 60 (0) | 43 (0) |
| RapidOCR, PP-OCRv6 tiny | 60 (0) | 60 (0) | 57 (2) | 60 (0) | 60 (0) | 60 (0) | 41 (0) |
| docTR 1.1 (DBNet ResNet-50 + CRNN VGG16) | 60 (0) | 58 (1) | 49 (1) | 60 (0) | 60 (0) | 60 (0) | 46 (0) |
| EasyOCR 1.7 (Indonesian + English) | 12 (0) | 10 (0) | 8 (0) | 12 (0) | 12 (0) | 12 (0) | 8 (0) |
