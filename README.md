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
| `ocr-benchmark/` | Reads all 800 synthetic contracts with the reader, Claude off, to compare OCR engines and to measure every reader change | [ocr-benchmark/README.md](ocr-benchmark/README.md) |
| `Database Design/` | The database ERD (Visual Paradigm file, PNG, and `erd.py` that draws it) | |
| `requirements.txt` | One Python 3.11 environment for the reader and the generator | |

## Where it stands (2026-11-10)

All results are on synthetic contracts, with Claude off. Nothing has been tried on real contracts yet.

| Set | Fully correct | Wrong, not marked | Time for 800 |
| --- | ---: | ---: | --- |
| First set: 2-page contracts | 738 / 800 | 1 cell | 39 min |
| v2 set: 2 to 6 pages plus attachments | 735 / 800 | 2 cells | 42 min |

"Fully correct" means all 9 fields are right. "Wrong, not marked" is a wrong value without a yellow mark, which HR
would miss.

What the benchmark showed:
- **OCR engine.** Tesseract stays the reader, and RapidOCR rereads only the cells Tesseract is unsure of. A
  stronger engine would not have helped much: the biggest gap was the signature check.
- **Changes since 2026-11-07.** On the first set, contracts fully correct rose from 622 to 738, through:
  - the second reader;
  - a signature stroke check;
  - a stamp (meterai) rule;
  - name and date fallbacks;
  - a job title fix;
  - reading only the first and last page.
- **With Claude.** Claude is now only a fallback (owner decision 2026-11-11). It gets the masked text only for the
  fields the local reading leaves unsure.
  - On the v2 set it got 767 / 800 fully correct, against 735 with Claude off and 762 with Claude on every file.
    On the first set it got 772 / 800, against 738 with Claude off.
  - It left 3 wrong values unmarked, against 2 with Claude off and 6 with Claude on every file. A rule added after the
    run keeps a title from a poor scan yellow when Claude only repeats it, which brings it to 2.
  - It used 2.0 M Claude tokens instead of 11.7 M, and took 59 minutes instead of 2 hours.
  - The steps are in [contract-pdf-generator/CLAUDE_BENCHMARK.md](contract-pdf-generator/CLAUDE_BENCHMARK.md).

Next:
1. Check on real contracts, after approval.
2. Before anyone else uses it:
   - complete the go-live dossier, `ocr-analysis/PROJECT.md`;
   - replace the mock sign-in with Apps Gateway;
   - encrypt NIKs at rest.

## Set up

Tools you need:
- Python 3.11;
- Tesseract 5 with the `ind` and `eng` languages;
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
