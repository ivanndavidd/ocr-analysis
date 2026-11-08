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
