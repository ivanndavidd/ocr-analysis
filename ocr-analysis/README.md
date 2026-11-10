# Contract OCR

This tool reads PKWT employee contracts (PDF) from the vendors' contract folders and fills HR's Excel row for each
one:
- employee name and NIK;
- position;
- contract start, end and months;
- whether the employee signed;
- vendor and ENTITAS.

Every cell is either sure (white) or marked yellow for HR to check, and each file gets a decision: auto, review, or
review first.

It is a local trial: a website on this laptop, reading a OneDrive folder of synthetic contracts. It is not live, and
it has not been reviewed for go-live.

## How a contract is read

1. **Two pages.** The PDF's first page is the identity page and its last page the signature page (owner rule
   2026-11-10). Empty pages at either end are skipped, and the pages in between are never read.
2. **Text.** A digital page gives its text layer. A scan or photo is read on this machine by Tesseract. When
   Tesseract is unsure of the NIK, the position or a date, RapidOCR reads the page again, and the same value from
   both turns the cell green.
3. **Fields.** Text patterns read the name, period, NIK and position, then check them against the filename
   (`<name> - <period> - <ENTITAS>.pdf`) and the vendor folder. A NIK is also checked against the printed birth
   date and gender.
4. **Claude (optional).**
   - It gets the identity page's text with every NIK masked, to read the position and the employer company. A
     scan read too poorly to mask safely stays on the laptop.
   - It also reads the name and period when the patterns can't confirm them.
   - It gets a crop of the NIK line or the signature area only when the local check is unsure.
   - It never gets a whole page, except in the no-OCR test mode, which isn't approved.
   - With `CLAUDE_MODE=off`, nothing leaves the laptop.
5. **Signature.** An ink check on the employee's block of the last page. A stroke check decides faint or partly
   hidden signatures. A signature on a meterai always crosses the stamp's edge, so no pen outside the stamp reads as
   unsigned, marked yellow.
6. **Decision.** Each field's level (sure, check, failed) gives the file's decision. Pages with salary details never
   leave the machine.

## Parts

| Folder | What it is |
| --- | --- |
| `apps/extractor` | Python 3.11 package `contract-extractor`: the reader. It is a batch CLI (`run`, `evaluate`) and also runs as the website's workers (`serve-stdio`). |
| `apps/backend` | Bun + Elysia API on 127.0.0.1:3000: folders, the OCR run queue, shared run history, Excel export, mock sign-in, PostgreSQL through Drizzle. |
| `apps/frontend` | Svelte 5 + Vite on 127.0.0.1:5173: pick folders or PDFs, run OCR, check each contract beside its PDF, download the Excel. |
| `packages/shared-types` | Types shared by the API and the frontend. |

## Results so far

All on synthetic contracts from `../contract-pdf-generator`. Nothing has been tried on real contracts yet; that
needs approval.

| Reader | Set | Fully correct | Wrong, not marked |
| --- | --- | ---: | ---: |
| Current, Claude off | First set (2-page) | 738 / 800 | 1 cell |
| Current, Claude off | v2 set (multi-page) | 735 / 800 | 2 cells |
| 2026-11-04 reader, with Claude | First set | 772 / 800 | |

- **Fully correct:** all 9 fields right.
- **Wrong, not marked:** a wrong value with no yellow mark, so HR would not check it.
- **Speed:** the current reader takes about 9 s per file per worker with Claude off. All 800 v2 contracts take
  42 minutes with 3 workers.
- **Claude on with the current reader:** waiting for the Claude login. The steps are in
  `../contract-pdf-generator/CLAUDE_BENCHMARK.md`.

How each change was measured is in `../ocr-benchmark/README.md`.

## Run it

One Python venv at the `contract-ocr` root serves the extractor, the generator and the website workers. Tesseract 5
with the `ind` and `eng` languages must be installed. Bun 1.4 runs the website.

From `contract-ocr`, set up the Python venv:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m pip install --no-deps -e ocr-analysis/apps/extractor
```

From `ocr-analysis`, start the website:

```bash
bun install
```

```bash
bun run dev
```

Before `bun run dev`, copy `apps/backend/.env.example` to `apps/backend/.env`, then set `EXTRACTOR_PYTHON` to the
venv's Python and `FOLDER_ROOT` to the contract folder. Open http://127.0.0.1:5173 and sign in with any email and
name (mock sign-in, no password).

Tests: `bun run test` and `bun run typecheck` for the website. For the extractor, run
`../../../.venv/Scripts/python -m pytest` from `apps/extractor`.

Every command, setting and environment variable is in `AGENTS.md`, under "Build and run". That includes the
container image, `SECOND_READER` with its model files, and Claude through the API or a Claude Code login.

## Documents

| File | What it holds |
| --- | --- |
| `CONTEXT.md` | Current state: structure, decisions, progress (Indonesian) |
| `PRD & RFC Ekstraksi Kontrak Karyawan dari PDF.md` | Requirements and design (Indonesian) |
| `PROJECT.md` | Go-live review dossier, still a skeleton: owner, risk level and review evidence come from the owner |
| `AGENTS.md` (`CLAUDE.md` is a copy) | Rules for coding agents, and the build and run commands |
| `../Database Design/` | Database ERD |

## Data rules

- Development uses synthetic contracts only. Real contracts (`sample/`, `Contract Example/`) are gitignored and
  never used as test fixtures or sent to Claude.
- The local database stores full NIKs in plaintext. That is acceptable only for synthetic data: before staging or
  production, NIKs need envelope encryption (KEK in the KMS).
- The mock sign-in is a stand-in for Apps Gateway (the company SSO). It is not access control.
