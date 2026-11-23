# PROJECT.md

Go-live review dossier for this tool. Reviewers and approvers read this file before the tool is released to others. Complete it via the `project-documenter` skill before pushing to GitHub or going live. Fields marked **(owner input)** must come from the project owner; never invent them. Keep this file updated when facts change: a material change to risk level, data used, or access scope may trigger re-review.

## Problem Being Solved

<!-- What problem this tool solves and for whom. One short paragraph. -->

## Project Owner

<!-- (owner input) The GitHub username of the person responsible for the tool — one login, no leading @, no URL.
Must be an existing GitHub account and a member of the gdncomm-ops organization. Not a team name, not an email.
/vibe-check asks "is the project owner you (<your-github-login>)?" and records the answer here. -->

## Support Contact

<!-- (owner input) A single email address contacted when issues occur.
No public webmail (gmail, yahoo, hotmail, outlook, ...) and no shared lab/group account (alpha.lab01@..., sandbox@...)
— those leave nobody accountable. A personal company mailbox is preferred; a role/queue mailbox (ops-helpdesk@...)
is accepted with a warning. -->

## Vibe Start

<!-- ISO date (YYYY-MM-DD) the vibe-coding of this project started — used to measure
project-start → staging/production duration. Stamped once by /start-vibe (today's date) for
a new project, or by /adopt-vibe (the first git commit date) for an existing one. Left blank
here; if it stays blank, /vibe-check falls back to the earliest git commit date. Do not
overwrite an existing value. -->

## Stack Exceptions

<!-- Deviations from the allowed stack (`allowedStack` in plugins/golive-gate/data/build-contract.json).
Every row needs approval from a tech architect (Tech Architecture Group). Legacy projects (Vibe Start
before 2026-10-07) may keep their existing stack without a row, but MAJOR IMPROVEMENTS always need an
approved row, even for legacy projects and even on the allowed stack: new module/service/workspace,
DB migrations or schema changes, new frontend app, runtime/framework/ORM/DB swap, major framework
upgrade. The `stack-allowed` check parses the header columns by name; keep them exactly. -->

| Scope | Deviation | Approved by | Date | Link (ADR/ticket) |
|-------|-----------|-------------|------|-------------------|

## Label

<!-- (owner input) GCP labels for cost and budget management. Provide all three below.
- Company: exactly one of gdn, gdpu, gdpa, gdv, gdr, gtn, gan, gdnus, gdni, rise, gem (owning sister company).
- Division: exactly one of operations, finance, marketing, commercial.
- Department: free text — the owning department name (non-empty), recorded in lowercase.
Asked during /start-vibe; /vibe-check will not pass while Company or Division is empty/invalid or Department is empty. -->

- Company:
- Division:
- Department:

## Design Overview

<!-- Simple design overview: main components, data flow, key integrations. Detail lives in docs/ARCHITECTURE.md; the full design-review document (diagrams, transaction flows, database schema, external connections, financial-risk analysis) lives in docs/DESIGN.md. Keep this reviewer-readable. -->

## Risk Level

<!-- (owner input, confirm) Low / Medium / High per the Non-Tech Change Management playbook.
Low: small personal tool, no sensitive data.
Medium: used by a team, connects to company systems, uses internal company data, automates regular work, or affects team decisions.
High: accessible outside the company or internet-facing, handles payments or money, uses sensitive personal or company data, or supports critical business processes.
An individual customer or seller's name, direct email, or phone is PII even alone. A legal-entity name or generic company channel is business data; a named partner representative remains identifiable unless GRC/Information Security documents an exception. Daily purchase/sales order items and values are internal operational data even in a database; money-moving or money-determining workflows are assessed separately. An internal access-control email alone and position coordinates alone do not automatically make a tool High.
A tool is not automatically Low just because it is small. When in doubt, consult Information Security. -->

## Access Status

<!-- Internal-only or external access. State which and why. -->

## Data Used

<!-- Classification: Public / Internal / Confidential. List the actual datasets and source systems. -->

**Classification: Confidential.** The input holds personal data (employee names, NIK, birth details, signatures) and salary.

**Dataset and source.** Fixed-term employment contracts (PKWT) as PDF, one file per employee, filed as `<vendor>/<contract period>/<Nama - Masa Kontrak - ENTITAS>.pdf`. Source system: a SharePoint document library read through Microsoft Graph (an Entra ID app with the application permission `Sites.Selected`, granted read access on that one site), or a local folder for testing. Development and every test so far use synthetic contracts only (generator output, OneDrive folder `kontrak-testing`); no real contract has been processed.

**Read on the local machine (never sent):** every page is opened locally only to pick the identity page and the signature page; other pages, including salary pages, are not OCR'd beyond that and never leave. From the identity page the tool reads the employee name, NIK (checked against the printed birth date and gender), position, and contract period; from the signature page whether the employee signed. The vendor comes from the folder name and ENTITAS from the filename.

**Sent to Anthropic (Claude), only when the backend runs with `CLAUDE_MODE=on`, the default since 2026-11-03.** Credential: an Anthropic API key from Vault in production, or the Claude Code login of the person running the backend for local testing only. Per file:

| What | When | Contains |
| --- | --- | --- |
| Masked text of the identity page | Every file whose identity-page OCR passed its quality check | Names (employee and company signatory), job titles, company names, dates. Removed before sending: whole lines on salary, wages, allowances, THR, bonus, address, phone, and place/date of birth; rupiah amounts; every run of 10+ digits; NIK, KTP, NPWP and bank-account values. Fails closed: if a digit run survives masking, nothing is sent |
| Crop of the NIK digits line | Only when the local NIK reading is missing, invalid, picked from a run of more than 16 digits, contradicted by the printed birth date or gender, or read by OCR without both checks matching | The full 16-digit NIK as an image; never the rest of the page |
| Crop of the employee signature area | For every scanned or photographed contract (since 2026-11-04, as a second reader after an unsigned black-and-white scan read signed locally), and for a text PDF only when the local check is uncertain or reads unsigned | The signature (or empty space) above the employee's printed name, usually the name itself, sometimes part of a meterai; never the rest of the page |

In this mode (the default, "Dengan OCR") no whole page is ever sent. Each file's log records every call (`panggilan_llm`), the tokens used, and which page went to which component (`komponen_halaman`: `<extractor>:masked`, `:potongan_nik`, `:potongan_ttd`), without any text or image.

**"Claude baca halaman" mode (`claude_page`, since 2026-11-16; approved by the owner's mentor, owner's report).** A switch on the website's folder page picks this mode per run; "Dengan OCR" stays the default. It replaces the no-OCR test mode of 2026-11-04 ("Tanpa OCR"), which sent the whole first and last page unmasked and has been removed (its old runs can still be opened in the history). In this mode the NIK, the signature and the vendor check stay with the local reading exactly as above (including the NIK and signature crops), and one more thing goes to Claude per file in place of the masked text:

| What | When | Contains |
| --- | --- | --- |
| Image of the identity page with the NIK blacked out | Every scanned or photographed file whose NIK local OCR could locate; otherwise nothing is sent and the masked text above is used | Everything printed on the identity page except the NIK and any other run of 10 or more digits, which are painted black: employee and company signatory names, job titles, company names, contract dates, and also the employee's address, place and date of birth, and gender. Never the signature page or other pages |
| Masked text of the identity page (since 2026-11-18, owner decision) | Every native (digital) PDF, whose identity page has a text layer: no image is sent | The same masked text as in the default mode, for every file instead of only for unsure fields |

Claude reads the name, contract dates, duration, job title and employer from it; the laptop checks the name and period against the filename (or the period against its own OCR text) and the employer against the vendor folder. The log records it as reason `read_identity_image` (`read_identity_text` for a native PDF's text), call `halaman_identitas` and `komponen_halaman` `<extractor>:halaman_identitas`; a file whose image was not sent gets `identity_image_not_sent`. **Owner/GRC input needed before any real contract is read this way:** written approval of sending the identity page with address and birth data visible (or masking those as well). With `CLAUDE_MODE=off` nothing leaves the machine. The owner's mentor allowed NIK, position and signature to be read by the LLM on 2026-11-03, on the condition that only crops are sent, and allowed every signature crop to be sent on 2026-11-04 (owner's report). **Still open (owner/GRC input):** written approval of this data flow, and the Anthropic terms the PRD requires for it (enterprise tier, no training, zero or limited retention, signed DPA).

**Website users (since 2026-11-05).** A database table `User` holds one row per email that has signed in: work email, name, role (reviewer or admin), whether the account is active, the time of the first sign-in and of the latest one (since 2026-11-16), kept until deleted. Admins see every user's row on the User page. On the developer's laptop it is a local PostgreSQL 15 server named in `DATABASE_URL` (database `contract_ocr`, reached by a role of the same name that owns only that database: no superuser or create-database rights); with `DATABASE_URL` empty it is PostgreSQL embedded in the backend (PGlite, files in `apps/backend/data/pglite`, gitignored). So far it holds fictitious test users only. **Owner/GRC input needed before staging:** whether staff email and name in this table need envelope encryption at rest.

**Saved OCR results (since 2026-11-05).** Every "Jalankan OCR" run is saved to the same database. `OCRHeader` gets one row per run: its job id, who started it (`User` id), status (queued, running, done, cancelled, interrupted), the read mode (with OCR or no-OCR) and whether Claude helped, the chosen folders, the number of files, Claude's token totals for the PDFs this run read itself, and the start and end time. `OCRContractDetail` gets one row per PDF in that run: its root-relative path (vendor/period/file name) and that path's SHA-256, the outcome HR acts on (`ready`, `needs_check`, `failed`, `cancelled`), the reading time, and the values the Excel holds: vendor, entity, **employee name, full NIK**, position, contract start and end, months, signature (YES/NO), plus the notes on values to check and the reason codes (a failed file's cause is its first code). Since 2026-11-06 the row also keeps the **whole reading as JSON** (`result`: the same values with the NIK masked to its last four digits, the name as Claude read it, the period read from the document, the signature verdicts, page numbers, Claude's calls and tokens) so an old run can be shown again as its full results page, and, when the PDF's result was taken from another run, that run's header id (`reusedFromHeaderId`). A file's row is written as soon as it is read; a retry in the same run updates that row; files left when a run is cancelled are saved as `cancelled`. A failed write never stops a run: the results page says the run was not fully saved, and the log carries the job id and the database error code only, never values. Rows are kept until deleted; there is no retention rule or delete function yet. So far the database holds synthetic contracts only. **This stores PII (employee names and full NIK) at rest in plaintext**, acceptable only on the developer's laptop with synthetic contracts. **Before staging, or before any real contract is read with the database on:** KEK/DEK envelope encryption of the NIK and name columns (KEK in the KMS; a blind index if NIK is ever looked up), and owner/GRC decisions on the retention period and deletion, who may read these tables, and whether the full NIK must be stored at all (the Excel export already carries it). Database backups are an SRE follow-up.

**Run queue and shared run history (since 2026-11-06).** Several users can start runs at once. Runs wait in one first-come-first-served queue and read one at a time with every worker; each user can have one queued or running run. A PDF that a run ahead in the queue (still queued or running, same read mode) also has is not read again: the later run waits for that reading and takes its result (so that PDF goes to Claude once), and reads it itself only if the earlier run fails it or is cancelled first. **Every signed-in user sees every run**: the history list on the folder page (who started each run, when, which folders, how many files are ready, need checking or failed) and each run's full results page, including **employee names and full NIK** (on screen and in the Excel export), and the name of the run a taken result came from. The folder list marks each PDF some run has read as "Sudah di-OCR" with that newest reading's date, its outcome (ready or to check) and the run's starter on hover; clicking the PDF opens it next to that reading (every value including the full NIK), or alone when no run has read it. **Unduh Excel** on the folder page downloads the Excel of the selection (employee names and full NIK, like the run export) from each PDF's newest reading in any user's run; PDFs no run has read are first read in a new run started by the user, which the page waits for before downloading. Only the user who started a run can cancel or retry it. Runs left queued or running when the backend stops are marked `interrupted` the next time it starts; their unread files are not listed. **Owner/GRC input needed:** whether every HR user of the tool may see every other user's runs and results, including full NIK, or whether access should be narrowed (for example to own runs, or by role).

**Outputs and retention.** The website keeps queued and running runs and the last 20 finished runs in backend memory, and saves every run to the database (see Saved OCR results above); older runs, and runs from before a restart, are read back from the database when opened. Rows show the NIK masked to its last four digits; the full NIK is fetched on request and goes into the Excel export (owner decision 2026-11-02). The CLI writes `results.json`, `results.csv` and `results.xlsx` with the NIK masked unless `output.nik: full`. PDFs, page images and OCR text stay in memory and are dropped after each file, except that Tesseract (through pytesseract) writes each page it reads to the system temp folder for a moment and deletes it right after; in the container that folder is a tmpfs. Logs carry file hashes, codes and counts only, never names, NIK or text.

## Data That Must Not Be Entered

<!-- e.g. KTP/NIK, NPWP, individual customer/seller names, direct emails and phones, home addresses, payroll data, personal financial accounts, passwords, access codes, confidential company information. Use synthetic data in development. Daily purchase/sales order items and values are internal operational data, not sensitive financial data merely because of monetary values or database storage. -->

## Login Method

<!-- Apps Gateway (approved company SSO, default) or Company Google OAuth login (external users without a company account, or fallback while Apps Gateway is not yet configured). No other method is permitted. -->

**Today (local testing only, since 2026-11-05): mock sign-in, not an approved method.** The website opens on a sign-in page that asks for an email (any domain: Apps Gateway, which replaces it, lets only company accounts through itself; owner decision 2026-11-05) and a name, and **no password**: nothing is checked against any identity provider, so it is not access control. It stands in for Apps Gateway so the website has a signed-in user and the `User` table fills the way it will behind the gateway. Since 2026-11-06 a development build (`bun run dev`) also offers "Masuk cepat dengan akun uji": one click signs in as one of four fictitious test accounts listed in the frontend source (`penguji.*@gdn-commerce.com`), so one person can test several users; a production build (`vite build`) leaves the button and the list out. It adds no access the form does not already give. The session is an 8-hour, HttpOnly, SameSite=Strict cookie held in backend memory. Since 2026-11-16 a first sign-in records the user as an inactive reviewer who can use nothing until an admin activates them, and every sign-in records the time as the user's last login (see Access Levels). **Planned:** Apps Gateway (company SSO); the backend will take the user from the gateway's forwarded identity and record it with the same code (`UserStore.signIn`, which applies the same inactive-reviewer rule), trusting the identity headers only once the gateway-only network boundary is verified. The mock sign-in must be removed before anyone other than the developer uses the website.

## Access Levels

<!-- Roles and permissions. Least privilege: users get only the access they need; not everyone is an administrator. Required detail for Medium and High risk. -->

**Since 2026-11-16 (mentor request): two roles and an active switch, enforced by the backend on every request.**

| Who | Can do |
| --- | --- |
| Inactive user (every user on their first sign-in, and anyone an admin deactivates) | Sign in, see a "waiting for an admin to activate your account" page, sign out. Every other API route answers 403 `account_inactive`, including for a session already open when the user is deactivated (the backend reads the user from the database on each request). A run the user started before being deactivated still finishes. |
| Reviewer (active) | Browse the contract folders, open PDFs (with their newest reading, whoever ran it), start a run, see every user's runs and results, including employee names and full NIK, and download their Excel. Only the user who started a run can cancel or retry it. |
| Admin (active) | Everything a reviewer can, plus the User page: list every user (name, work email, role, status, last sign-in, first sign-in), activate or deactivate them, and switch them between reviewer and admin. Never their own account (403 `cannot_change_self`), so nobody locks themselves out. |

The first admin comes from `ADMIN_EMAILS` (backend environment, Vault in production): those emails become active admins on every sign-in and the User page cannot change them; removing an email from the list is how such an admin is removed. Each change is logged with user ids only. Existing users became inactive reviewers when the change was deployed. While the sign-in is the mock, these roles are not access control (anyone can sign in as an admin's email); they become access control behind Apps Gateway. **Owner input still needed:** whether every reviewer may see every other user's runs and results, including full NIK, or whether that should be narrowed (for example to own runs, or to admins).

## Access Approval Process

<!-- (owner input) Who approves access requests and how. -->

## Go-Live Steps

<!-- Deployment route and the approvals required for this risk level. Low: owner check. Medium: independent review — the reviewer must be neither the Project Owner nor the Support Contact above. High: the same independent review plus Architecture, SRE, and Information Security approval. -->

## Review Evidence

<!-- (owner input) Who reviewed, when, and the sign-off record (approval email, review notes). Required before go-live for Medium and High risk. -->

## Security Notes

<!-- Key security measures and known gaps. Cross-reference docs/SECURITY.md rather than duplicating it. -->

## Hosting Location

<!-- The approved company platform where the tool runs. Personal or public platforms require formal approval. -->

## Recovery Procedures

<!-- Application rollback/restore steps where known. The backup pipeline or method is SRE-owned; record its status or "SRE follow-up" and a tracking reference if available. Missing backup details alone do not block go-live. -->
