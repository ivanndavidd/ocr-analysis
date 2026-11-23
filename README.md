# contract-ocr

HR receives employee contracts (PKWT) from outsourcing vendors as PDFs: digital files, scans and phone photos. Each
one has to be typed into an Excel row: employee name, NIK, position, contract dates, whether the employee signed,
the vendor and ENTITAS.

This repository holds a tool that reads those PDFs and fills the row. Each cell is sure or marked yellow for HR to
check. The repository also holds the tools that test it on synthetic contracts.

## Folders

| Folder | What it is | Read |
| --- | --- | --- |
| `ocr-analysis/` | The product: the Python reader (`contract-extractor`) and the local website (Bun + Elysia API, Svelte frontend) | [ocr-analysis/README.md](ocr-analysis/README.md) |
| `contract-pdf-generator/` | Makes synthetic contracts with an answer key, copies them into the website's folder, and scores the website's reading | [contract-pdf-generator/README.md](contract-pdf-generator/README.md) |
| `ocr-benchmark/` | Reads both synthetic datasets with every reader setup, Claude off or on, to compare accuracy, Claude tokens and CPU | [ocr-benchmark/README.md](ocr-benchmark/README.md) |
| `Database Design/` | The database ERD (Visual Paradigm file, PNG, and `erd.py` that draws it) | |
| `requirements.txt` | One Python 3.11 environment for the reader and the generator | |

## Where it stands (2026-11-23)

The local OCR is RapidOCR first, with Tesseract reading a page again when RapidOCR is unsure (owner decision
2026-11-16). A native (digital) PDF is read from its text layer instead. Three ways of adding Claude (Haiku 5.5) were
compared:

| Approach | How Claude is used | Where |
| --- | --- | --- |
| 1. RapidOCR → Tesseract → Claude | Only for the fields the local reading leaves unsure: masked text, plus a crop of an unsure NIK and of the signature | Website, "OCR" mode |
| 2. Claude baca halaman | Reads the name, dates, position and employer from the identity page, as an image with the NIK blacked out (a native PDF's masked text instead); the NIK and signature stay local | Website, "Claude baca halaman" mode |
| 3. Full Claude | Reads every field from the whole first and last page, no OCR | Benchmark only |

All results are on synthetic contracts: the scans and photos of the first and second datasets (973 contracts).

| Setup | Fully correct | Wrong cells, not marked | Claude tokens | Claude cost per contract |
| --- | ---: | ---: | ---: | ---: |
| RapidOCR → Tesseract, no Claude | 894 / 973 | 11 | 0 | $0 |
| Approach 1 | 923 / 973 | 10 | 3.02 M | ±$0.0004 |
| Approach 2 | **938 / 973** | **3** | 13.05 M | ±$0.0023 |
| Approach 3 | 927 / 973 | 4 | 15.58 M | ±$0.0026 |

"Fully correct" means all 9 fields are right. "Wrong cells, not marked" are wrong values without a yellow mark, which
HR would miss. The cost uses the Claude API price ($0.10 per million input tokens, $0.50 per million output tokens)
and is the larger of scans/photos and native PDFs.

What the benchmark showed:
- **Approach 2 is the most accurate**, and leaves the fewest wrong values unmarked, for about 4 times the tokens of
  approach 1. It is the one recommended so far.
- **Native PDFs are easy for every setup:** 623 of 627 fully correct without Claude (the 4 misses are undecided
  signatures), 627 with approaches 1 and 2, and 621 with approach 3.
- **No GPU is needed.** The local OCR takes about 31 to 36 CPU seconds per scanned contract; Claude runs at Anthropic.
- The full tables are in [ocr-benchmark/README.md](ocr-benchmark/README.md),
  [ocr-benchmark/results/comparison.txt](ocr-benchmark/results/comparison.txt) and the cost table
  [ocr-benchmark/results/cost-comparison.png](ocr-benchmark/results/cost-comparison.png).

Next:
1. Choose the approach for the website.
2. Check on real contracts, after approval.
3. Before anyone else uses it:
   - complete the go-live dossier, `ocr-analysis/PROJECT.md`, including GRC approval for sending the masked identity
     page to Claude;
   - replace the mock sign-in with Apps Gateway;
   - encrypt NIKs at rest.

## Set up

Tools you need:
- Python 3.11;
- Tesseract 5 with the `ind` and `eng` languages;
- the two RapidOCR model files (`PP-OCRv6_det_small.onnx`, `PP-OCRv6_rec_small.onnx`) in
  `ocr-analysis/apps/extractor/models/rapidocr`, which the website needs to start (see `ocr-analysis/AGENTS.md`);
- Bun 1.4.

From this folder, create the environment:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m pip install --no-deps -e ocr-analysis/apps/extractor
```

Then follow `ocr-analysis/README.md` to start the website. `ocr-benchmark/` uses its own `.venv`, because it holds
the extra OCR engines.

## Data rules

- **Real contracts never enter the repository.** `Contract Example/` and `sample/` hold real contracts with personal
  data. They are gitignored and never read by the tests, the benchmark or Claude.
- **Generated data stays out of git too.** The generated sets (`output*/`), accuracy reports and benchmark runs
  contain synthetic names and NIKs, and are gitignored.
- **Secrets stay in `.env` files,** which are never committed. Only `.env.example` with placeholders is.
