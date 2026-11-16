// Shapes shared by the backend API and the Svelte frontend.

import type { Note } from './notes.ts';

export * from './notes.ts';

export interface Crumb {
  name: string;
  path: string; // relative to the folder root, '/'-separated; '' is the root itself
}

export interface FolderEntry {
  name: string;
  path: string;
  pdfCount: number; // PDFs inside, subfolders included
}

export interface FolderListing {
  path: string;
  crumbs: Crumb[];
  folders: FolderEntry[];
  pdfs: string[]; // file names directly inside this folder
}

export interface DateRange {
  start: string; // ISO yyyy-mm-dd
  end: string;
}

export type SignatureStatus = 'signed' | 'unsigned' | 'uncertain';

/** What the extractor's local regex made of the period in the document text. */
export type DocumentPeriodStatus =
  | 'found' // start and end
  | 'conflict' // several different dates or lengths for the same field
  | 'partial' // only a start, an end or a length
  | 'missing'; // nothing anchored to a period word

export interface DocumentPeriod {
  status: DocumentPeriodStatus;
  start: string | null;
  end: string | null;
  durationMonths: number | null;
}
export type Decision = 'auto' | 'review' | 'review_priority';

/** What the extractor's local NIK reader found on the identity page. */
export type NikStatus =
  | 'found'
  | 'conflict' // two different valid NIKs; neither is reported
  | 'invalid' // a number after an NIK label that is not a valid NIK
  | 'missing';

/** Whether the birth date or gender printed in the contract agrees with the one coded in the NIK. */
export type NikCheck = 'match' | 'mismatch' | 'absent';

export interface NikInfo {
  status: NikStatus;
  masked: string | null; // last four digits only; the full number comes from POST /api/jobs/:id/nik
  birthDate: NikCheck;
  gender: NikCheck;
  fromOcr: boolean; // read from a scan, so a person checks it even when every check agrees
  // Claude's reading of a crop of the NIK line, sent when the local reading was unsure: the same digits ('same'), used
  // in place of the local reading ('used'), other digits that were not used ('different'), or none ('unreadable').
  claude: 'same' | 'used' | 'different' | 'unreadable' | null;
  // The second local OCR (RapidOCR) reading the same scan again, when the local reading was unsure: the same digits
  // ('same'), other digits ('different'), or none ('unreadable'). It only ever confirms; it never replaces the NIK.
  secondReader?: 'same' | 'different' | 'unreadable' | null;
}

/** The employee's job title as the extractor read it from the identity page (Jabatan/Posisi row or placement
 *  sentence); never the company signatory's title. */
export interface PositionInfo {
  status: 'found' | 'conflict' | 'missing'; // conflict: two different titles, neither reported
  value: string | null;
  confidence: number | null; // pattern: lowest OCR word confidence over the title (1 from a text layer); Claude: its own
  agreement: 'same' | 'different' | null; // Claude's title against the local pattern reading; null when not compared
  // The local reading Claude was compared with came from an unclear scan. Claude reads the same OCR text, so its
  // agreement does not confirm that title.
  fromUnclearScan?: boolean;
  fixedByClaude?: boolean; // Claude fixed an OCR misread in the title, so it is not printed letter for letter
  source: 'claude' | 'pattern' | null; // who read the title shown; null when there is none
  // A low-confidence title from a scan, read again by the second local OCR (RapidOCR): the same title, another, or none
  secondReader?: 'same' | 'different' | 'unreadable' | null;
}

/** Who read the name and the period: the local patterns, or Claude (verified against the text it was sent) for a
 *  file the patterns could not confirm (`readByFallback`). */
export type Reader = 'claude' | 'pattern';

/** The data-output columns read from a contract, in the Excel's order. */
export type FieldKey = 'nik' | 'vendor' | 'entity' | 'name' | 'position' | 'start' | 'end' | 'months' | 'signature';

/** One value as it goes into the Excel. When `check` is set a person compares it with the PDF: the cell is yellow, on
 *  the page and in the Excel, with this note. `basis` says why a value without a check can be trusted. Both are keys
 *  the page renders in the reader's language (see notes.ts). */
export interface FieldValue {
  value: string | number | null; // NIK masked (the Excel gets the full one); dates ISO; signature 'YES' or 'NO'
  check: Note | null;
  basis: Note | null;
}

export type ContractFields = Record<FieldKey, FieldValue>;

/** The tokens Claude used for one file, summed over its calls, as the provider reports them. */
export interface TokenUsage {
  input: number; // read fresh
  cacheWrite: number; // written to the prompt cache
  cacheRead: number; // read from the prompt cache
  output: number; // thinking included
}

/** What one Claude call for a file was sent: the masked identity-page text, a crop of the NIK line, a crop of the
 *  signature area, or (the no-OCR mode) the first and last page whole. */
export type ClaudeCallKind = 'text' | 'nik' | 'signature' | 'pages';

/** How a job reads its PDFs: 'ocr' (Tesseract on this machine, the patterns, Claude helping with masked text and
 *  crops) or 'full_page' (no OCR: the first and last page go to Claude whole and unmasked; for testing only). */
export type ReadMode = 'ocr' | 'full_page';

/** One Claude call for a file and the tokens it used; null when the provider reported none. */
export interface ClaudeCall {
  kind: ClaudeCallKind;
  tokens: TokenUsage | null;
}

export interface ContractRow {
  file: string;
  fields: ContractFields; // what the Excel row holds, and what is doubtful in it
  reader: Reader;
  readByFallback: boolean; // Claude read the name and period, since the patterns could not confirm them
  claudeCalls: ClaudeCall[]; // in order, each with its tokens; empty when nothing was sent
  tokens: TokenUsage | null; // what those calls used together, failed ones included; null when nothing was reported
  vendorFromFolder: string | null; // the vendor folder the file sits in: `root/<vendor>/<masa kontrak>/<pdf>`
  vendorInDocument: boolean | null; // the folder's vendor is printed in the document (or is the employer Claude read)
  vendorBasis: 'text' | 'claude' | null; // what decided vendorInDocument: the page text, or Claude's employer
  vendorFromDocument: string | null; // the employer company Claude read, after verification; null otherwise
  entityFromFilename: string | null; // ENTITAS, the part after the period in `Nama - Masa Kontrak - ENTITAS`
  nameFromFilename: string | null;
  nameFromDocument: string | null; // Claude's reading, as printed; null with the patterns or when it was not verified
  nameInDocument: boolean | null; // the filename's name is printed on the identity page; null when not checked
  // Not on the identity page, but in the employee's column of the signature page (with the patterns only)
  nameOnSignaturePage?: boolean;
  nameMatch: 'same' | 'similar' | 'different' | null; // Claude's name against the filename's; null when not compared
  // Claude's own, after verification; null otherwise. `nik` only in the no-OCR mode, where Claude reads the NIK whole
  readConfidence: { name: number | null; period: number | null; nik: number | null };
  nik: NikInfo;
  position: PositionInfo;
  filenameStandard: boolean; // `Nama - Masa Kontrak` parsed into a name and a period
  periodFromFilename: DateRange | null;
  // The second OCR (RapidOCR) read the dates Tesseract's text missed, and they are the filename's (with the patterns)
  periodFromSecondReader?: boolean;
  periodFromDocument: DateRange | null; // Claude's verified dates, or the local regex's, when both were found
  documentPeriod: DocumentPeriod;
  periodMatches: boolean | null; // null when either period is missing
  signature: SignatureStatus | null;
  // Claude's look at a crop of the signature area, sent when the local check was unsure or read a text-layer PDF as
  // unsigned; null when no crop was sent or the call failed.
  signatureByClaude: { status: 'signed' | 'unsigned' | 'unclear'; confidence: number } | null;
  decision: Decision;
  reasons: string[]; // extractor reason codes and flags; the UI translates them
  detail: {
    identityPages: number[];
    signaturePage: number | null;
    pageKinds: Record<string, string>;
    ocrConfidence: Record<string, number>;
    signatureSource: string | null;
    vendorNote: string | null;
  };
}

export type FileState = 'waiting' | 'reading' | 'done' | 'error' | 'cancelled';
/** `queued`: waiting for the runs ahead of it (one run reads at a time, first come first served); `interrupted`: the
 *  backend stopped while the run was queued or running, so the files it had not read yet are not in it. */
export type JobStatus = 'queued' | 'running' | 'done' | 'cancelled' | 'interrupted';

/** Who started a run. */
export interface RunUser {
  id: number;
  name: string;
  email: string;
}

/** Another run, as a file of this run points at it: its id and who started it. */
export interface RunRef {
  jobId: string;
  by: string | null; // the starter's name; null for a run started without a signed-in user
}

export interface JobFile {
  path: string; // relative to the folder root: what /api/files and retry take
  folder: string; // the folder holding the file, relative to the root
  name: string;
  state: FileState;
  row?: ContractRow;
  error?: string; // a fixed code such as 'timeout' or 'worker_crashed'
  seconds?: number; // how long reading it took, once it was read or failed
  // An earlier run, still queued or running when this one started, has the same PDF: its result is taken instead of
  // reading the PDF twice. `waitingFor` while that run has not read it yet; `readIn` once this file has its result.
  // If that run fails the file or is cancelled first, this run reads the PDF itself.
  waitingFor?: RunRef;
  readIn?: RunRef;
}

export interface Job {
  id: string;
  folders: string[]; // the folders that were chosen (subfolders included), then those holding chosen single PDFs
  claude: boolean; // Claude was helping the patterns when the job started
  mode: ReadMode;
  status: JobStatus;
  startedAt: string;
  finishedAt: string | null;
  files: JobFile[];
  startedBy: RunUser | null; // null for a run started without a signed-in user
  queueAhead?: number; // while queued: how many runs are ahead of this one
  missing?: number; // an interrupted run: files it had not read when the backend stopped (they are not listed)
  saveFailed?: true; // saving this run to the database failed; its results live in memory only
}

/** A run in the shared history (every user's runs, newest first): what it covered, who started it, and how its files
 *  came out. */
export interface JobSummary {
  id: string;
  folders: string[];
  claude: boolean;
  mode: ReadMode;
  status: JobStatus;
  startedAt: string;
  finishedAt: string | null;
  startedBy: RunUser | null;
  total: number;
  ready: number; // read, with no value to check
  check: number; // read, with at least one value to check
  failed: number;
}

/** The latest reading of one PDF in any user's run, shown when the PDF is opened from the folder page. */
export interface LatestResult {
  file: JobFile; // as read: `state` 'done' with its `row`
  nik: string | null; // the full NIK, when one was read; the row carries the last four digits only
  run: RunRef & Pick<JobSummary, 'mode' | 'status' | 'startedAt' | 'finishedAt'>; // the run that read the PDF
}

/** A PDF's newest reading in brief, for the mark next to its name in the folder list. */
export interface ReadMark {
  outcome: 'ready' | 'needs_check'; // no value to check, or at least one
  run: LatestResult['run'];
}

/** How far the runs have read a folder's PDFs (subfolders included, as its PDF count), for the mark in the folder
 *  list: `read` of `total` have a reading in some run, `check` of those need a check in their newest one. */
export interface FolderMark {
  total: number;
  read: number;
  check: number;
}

export type ExtractorState = 'starting' | 'ready' | 'unavailable';

/** Whether Claude helps the patterns: 'off' when the backend runs with CLAUDE_MODE=off (nothing leaves the machine),
 *  'starting' until a worker is up, 'on', or 'unavailable' when the workers could not reach it (no login, a wrong
 *  key) and read with the patterns alone. */
export type ClaudeState = 'off' | 'starting' | 'on' | 'unavailable';

export interface Health {
  extractor: { state: ExtractorState; error: string | null; workers: number };
  claude: ClaudeState;
  rootName: string;
}

/** What a user may do. Both read contracts and run OCR; an admin also manages users on the User page. */
export type Role = 'reviewer' | 'admin';

/** A row of the `User` table: whoever signed in with that email. A first sign-in adds an inactive reviewer. */
export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  active: boolean; // false: signed in, but every route except /api/auth/* answers 403 `account_inactive`
  createdAt: string; // ISO date-time of the first sign-in
  lastLoginAt: string | null; // ISO date-time of the latest sign-in; null if none since the column was added
}

/** `GET /api/auth/me` and `POST /api/auth/login`. Every other route answers 401 `unauthenticated` without a session,
 *  and 403 `account_inactive` while the user is inactive; a refused login answers 400 with `bad_email` or `bad_name`.
 *  Any email domain is accepted: Apps Gateway, which replaces the mock sign-in, lets only company accounts through. */
export interface AuthSession {
  user: User;
}

/** A user on the User page. `adminFromConfig`: the email is in the backend's ADMIN_EMAILS, so every sign-in makes
 *  it an active admin again; the page cannot change it (409 `admin_from_config`). */
export interface ManagedUser extends User {
  adminFromConfig: boolean;
}

/** `GET /api/users` (admins only, else 403 `admin_only`): every user, latest sign-in first. */
export interface UserList {
  users: ManagedUser[];
}

/** `PATCH /api/users/:id` (admins only): the new role, active state, or both. Answers the changed `ManagedUser`; an
 *  admin's own row answers 403 `cannot_change_self`, an unknown id 404 `user_not_found`. */
export interface UserAccessChange {
  role?: Role;
  active?: boolean;
}

export type UserAccessErrorCode = 'admin_only' | 'cannot_change_self' | 'admin_from_config' | 'user_not_found';

/** `POST /api/auth/login`: mock sign-in (no password) until the website sits behind Apps Gateway. */
export interface LoginRequest {
  email: string;
  name: string;
}

/** `GET /api/jobs?offset=&limit=` plus the filters below: a page of the shared run history, newest first. */
export interface JobHistory {
  jobs: JobSummary[];
  more: boolean; // older runs that match follow this page
  total: number; // runs that match, on every page
}

/** A run's state as the history filters it: `active` is queued or running. */
export type HistoryStatus = 'active' | 'done' | 'cancelled' | 'interrupted';
/** Runs with at least one file to check, or at least one failed file. */
export type HistoryOutcome = 'check' | 'failed';

/** The history's filters, as `GET /api/jobs` query parameters; a run is listed when every filter given holds. */
export interface HistoryFilter {
  q?: string; // part of a chosen folder's path, or of the starter's name or email, in any case
  status?: HistoryStatus;
  outcome?: HistoryOutcome;
  mode?: ReadMode;
  mine?: boolean; // only the signed-in user's runs
  from?: string; // ISO date-time: started at or after
  to?: string; // ISO date-time: started before
}

/** `GET /api/jobs/current`: the signed-in user's own run while it is queued or running (one at a time per user), and
 *  how many runs are queued or running in all, so a new run knows how many are ahead of it. */
export interface CurrentRun {
  job: Job | null;
  active: number;
}

/** `POST /api/jobs/:id/export` answers with an .xlsx; these are the refusals besides the usual job codes. */
export type ExportErrorCode = 'job_not_finished' | 'export_busy' | 'export_failed';

export interface ApiError {
  error: string; // a fixed code; the UI owns the wording
  jobId?: string;
  // `not_all_read` (the folder page's download): PDFs of the selection no run has read yet, of how many in all
  unread?: number;
  total?: number;
}
