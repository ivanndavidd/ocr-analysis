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
4. **Claude (optional), only as a fallback** (owner decision 2026-11-11).
   - The identity page's text, with every NIK masked, goes to Claude only when the local reading leaves the name,
     period, position or vendor unsure, and Claude's answer fills only those fields. A scan read too poorly to mask
     safely stays on the laptop. On the v2 set, 31 of 800 files needed it.
   - Claude may fix an OCR misread in the job title, such as `Adrnin Gudang` to `Admin Gudang`. The fix may change
     only a few letters of words in its quote, never swap in another word. A fixed title is always yellow, so HR
     checks it against the document. A rerun of the 47 files that sent text in the two fallback runs: titles
     right 39 to 42, unmarked mistakes 2 to 1, yellow titles 12 to 18. The one left, `Kurlr`, was read locally
     at confidence 0.91, so Claude never judged it.
   - It gets a crop of the NIK line or the signature area only when the local check is unsure.
   - It never gets a whole page, except in the no-OCR test mode, which isn't approved.
   - With `CLAUDE_MODE=off`, nothing leaves the laptop.
5. **Signature.** An ink check on the employee's block of the last page. A stroke check decides faint or partly
   hidden signatures. A signature on a meterai always crosses the stamp's edge, so no pen outside the stamp reads as
   unsigned, marked yellow. When the ink is close to the check's threshold the cell is yellow ("Kurang jelas"),
   unless Claude sees the same in the signature crop at 0.85 or more (owner decision 2026-11-16). On the two datasets
   that lets about 350 more contracts through with nothing to check, for 1 more wrong value left unmarked.
6. **Decision.** Each field's level (sure, check, failed) gives the file's decision. Pages with salary details never
   leave the machine.

## Parts

| Folder | What it is |
| --- | --- |
| `apps/extractor` | Python 3.11 package `contract-extractor`: the reader. It is a batch CLI (`run`, `evaluate`) and also runs as the website's workers (`serve-stdio`). |
| `apps/backend` | Bun + Elysia API on 127.0.0.1:3000: folders, the OCR run queue, shared run history, Excel export, mock sign-in, users and roles, PostgreSQL through Drizzle. |
| `apps/frontend` | Svelte 5 + Vite on 127.0.0.1:5173: pick folders or PDFs, run OCR, check each contract beside its PDF, download the Excel. In Indonesian or English. |
| `packages/shared-types` | Types shared by the API and the frontend, and the cell notes in both languages. |

## Results so far

All on synthetic contracts from `../contract-pdf-generator`. Nothing has been tried on real contracts yet; that
needs approval.

| Reader | Set | Fully correct | Wrong, not marked |
| --- | --- | ---: | ---: |
| Current, Claude off | First set (2-page) | 738 / 800 | 1 cell |
| Current, Claude off | v2 set (multi-page) | 735 / 800 | 2 cells |
| Current, Claude as fallback (the flow now) | v2 set | 767 / 800 | 3 cells |
| Current, Claude on every file (before 2026-11-11) | v2 set | 762 / 800 | 6 cells |
| Current, Claude as fallback | First set | 772 / 800 | 2 cells |
| 2026-11-04 reader, with Claude | First set | 772 / 800 | |

- **Fully correct:** all 9 fields right.
- **Wrong, not marked:** a wrong value with no yellow mark, so HR would not check it.
- **Speed:** with Claude off, about 9 s per file per worker: all 800 v2 contracts take 42 minutes with 3 workers.
  With Claude as fallback, 59 minutes with 4 workers; with Claude on every file, 2 hours.
- **Claude as fallback** (2026-11-11, website with 4 workers):
  - The identity text went to Claude for 26 of 800 files, against 796 when every file sent it.
  - It used 2.0 M Claude tokens against 11.7 M. The signature crop for every scan and photo is 1.66 M of the 2.0 M.
  - It keeps all of Claude's gains: 17 positions, 18 signatures, 1 NIK and 1 date that were yellow with Claude off.
  - Claude on every file also turned 4 right titles wrong without a mark. Claude agreed with a Tesseract misread
    (`Staff QC` read as `Staff OC`), so RapidOCR never reread them. The fallback runs RapidOCR first.
  - Of its 3 unmarked mistakes, 1 is also there with Claude off.
  - The second is a title Claude only repeated from a poor scan (`Cleaning Service 1`). Since 2026-11-11 such a
    title stays yellow, since Claude reads the same OCR text. On this run that marks 5 titles (4 of them right) and
    brings the unmarked mistakes to 2. This was computed from the saved results, not a new run.
  - The third is a signed contract whose signature crop Claude saw as empty. On the always-on run, Claude left
    that same crop undecided.
  - The reports are `../contract-pdf-generator/accuracy/20261111-125049` (fallback) and `20261111-104134`
    (every file). How to run them is in `../contract-pdf-generator/CLAUDE_BENCHMARK.md`.

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
venv's Python and `FOLDER_ROOT` to the contract folder, and put your email in `ADMIN_EMAILS`. Open
http://127.0.0.1:5173 and sign in with any email and name (mock sign-in, no password).

Users and roles: a first sign-in adds the user as an inactive reviewer, who only sees a "waiting for an admin" page
until an admin activates them. Admins (the emails in `ADMIN_EMAILS`, and anyone an admin promotes) open the User
page to activate, deactivate, and switch users between reviewer and admin, but never their own account. The backend
enforces all of it. The table shows each user's last sign-in.

Languages: the website speaks Indonesian (the default, worded as HR already knows it) and English. The ID | EN switch
sits in the top bar of every page, and on the sign-in and waiting pages; each browser remembers its own choice. Dates
and numbers follow it (`1 Jan 2045`, `12.475` or `12,475`). The backend sends each yellow-cell note as a key with its
values, and the page words it in the reader's language. The Excel export always stays Indonesian, notes included, as
HR's template is (owner decision 2026-11-16). Texts live in `apps/frontend/src/utils/messages/` (`id.ts`, `en.ts`)
and, for the cell notes the Excel shares, in `packages/shared-types/src/notes.ts`. A test fails when a key is missing
in either language or a text is written straight into a component.

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
- The mock sign-in is a stand-in for Apps Gateway (the company SSO). It is not access control: anyone can sign in
  as any email, an admin's included, so the roles only protect anything once the gateway supplies the identity.
