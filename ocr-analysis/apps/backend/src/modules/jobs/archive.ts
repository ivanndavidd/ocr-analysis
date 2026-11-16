import { createHash } from 'node:crypto';
import { type FieldValue, type HistoryFilter, type Job, type JobFile, type JobHistory, type LatestResult, type ReadMark, noteText } from '@contract-ocr/shared-types';
import { eq, inArray, sql } from 'drizzle-orm';
import type { Database } from '../database/database.ts';
import { type OcrDetailInsert, ocrContractDetails, ocrHeaders } from '../database/schema.ts';
import { vendorOf } from './export.ts';
import { type StoredRun, latestMarks, latestReading, latestReadings, listRuns, loadRun } from './history.ts';

/**
 * Keeps every run of "Jalankan OCR" in the database: an OCRHeader row when it is queued, an OCRContractDetail row for
 * each PDF as soon as it has a result (read by this run, or taken from another run that read the same PDF) or fails (a
 * retry in the same run updates that row), and the header's status, outcome and token totals as the run moves on.
 * Writes run in the background, one after another per run, and never stop the OCR: a write that fails marks the run
 * `failed` (the results page says so) and is logged by its error code only, never its values.
 */

export type DetailStatus = 'ready' | 'needs_check' | 'failed' | 'cancelled';
export type DetailValues = Omit<OcrDetailInsert, 'id' | 'headerId' | 'reusedFromHeaderId'>;

/** One run's link to the database. */
export interface RunArchive {
  /** The run's OCRHeader id once that row is written; null when writing it failed. */
  headerId(): Promise<number | null>;
  /** The run waits for its turn (again, for a retry) or has started reading. */
  status(status: 'queued' | 'running'): void;
  /** A file has its result or has failed: save it now. `readIn` is the run that read it, when that was another run. */
  file(file: JobFile, nik: string | null, readIn: RunArchive | null): void;
  /** The run has stopped: save its cancelled files and its outcome. */
  finish(job: Job): void;
  /** True once any write for this run failed. */
  readonly failed: boolean;
  /** Settles when every write asked for so far is done. */
  settled(): Promise<void>;
}

/** Where runs are kept, and read back for the shared history. */
export interface ResultArchive {
  /** A new run, written as it goes. */
  open(job: Job, userId: number): RunArchive;
  /** A saved run that is written to again (a retry of a run loaded from the database). */
  resume(jobId: string, headerId: number): RunArchive;
  /** A page of every user's runs that match the filters, newest first; `userId` is who `mine` means. */
  list(offset: number, limit: number, filter?: HistoryFilter, userId?: number | null): Promise<JobHistory>;
  /** A saved run as the results page shows it, with its full NIKs; null when there is none with this id. */
  load(jobId: string): Promise<StoredRun | null>;
  /** Marks the runs a stopped backend left queued or running as interrupted; how many there were. */
  interruptUnfinished(): Promise<number>;
  /** The newest reading of one PDF, by its root-relative path, in any user's run; null when none has read it. */
  latest(path: string): Promise<LatestResult | null>;
  /** How each of these PDFs came out in its newest reading, by path; PDFs no run has read are left out. */
  marks(paths: string[]): Promise<Record<string, ReadMark>>;
  /** The newest reading of each of these PDFs, by path; with `failures`, the newest failure of a PDF never read. */
  readings(paths: string[], failures?: boolean): Promise<Map<string, LatestResult>>;
}

const CHUNK = 200; // rows per insert: 19 parameters each, far below PostgreSQL's 65,535

/** The file's key across runs: SHA-256 of its root-relative path (vendor/period/file name). */
export const documentKey = (path: string) => createHash('sha256').update(path).digest('hex');

const text = (value: FieldValue['value'], max: number): string | null =>
  typeof value === 'string' && value.trim() ? value.slice(0, max) : null;
const isoDate = (value: FieldValue['value']): string | null =>
  typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
const whole = (value: FieldValue['value']): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null;
const json = (value: object): string | null => (Object.keys(value).length ? JSON.stringify(value) : null);

/** The OCRContractDetail values of a file that was read, failed or was cancelled; `nik` is the full NIK, if read. */
export function detailValues(file: JobFile, nik: string | null): DetailValues {
  const base = { documentKey: documentKey(file.path), filePath: file.path, seconds: file.seconds ?? null };
  const row = file.state === 'done' ? file.row : undefined;
  if (!row) {
    const failed = file.state === 'error';
    return {
      ...base,
      status: failed ? 'failed' : 'cancelled',
      vendor: text(vendorOf(file.path), 255),
      // a failure's cause is the first (here the only) reason; checks are notes on read values, so there are none
      reasons: failed ? JSON.stringify([file.error ?? 'internal_error']) : null,
    };
  }
  const { fields } = row;
  const checks = Object.fromEntries(
    Object.entries(fields)
      .filter(([, field]) => field.check !== null)
      .map(([key, field]) => [key, field.check && noteText(field.check, 'id')]), // as the Excel words it, for people reading the table
  );
  return {
    ...base,
    status: Object.keys(checks).length ? 'needs_check' : 'ready',
    vendor: text(fields.vendor.value, 255),
    entity: text(fields.entity.value, 255),
    employeeName: text(fields.name.value, 255),
    nik: nik !== null && /^\d{16}$/.test(nik) ? nik : null,
    position: text(fields.position.value, 100),
    startDate: isoDate(fields.start.value),
    endDate: isoDate(fields.end.value),
    months: whole(fields.months.value),
    signature: text(fields.signature.value, 10),
    checks: json(checks),
    reasons: json(row.reasons),
    result: JSON.stringify(row),
  };
}

/** The Claude tokens the run's own reads used, added up; results taken from another run count there, not here. */
export function tokenTotals(job: Job) {
  const totals = { inputTokens: 0, cacheWriteTokens: 0, cacheReadTokens: 0, outputTokens: 0 };
  for (const file of job.files) {
    const tokens = file.state === 'done' && !file.readIn ? file.row?.tokens : null;
    if (!tokens) continue;
    totals.inputTokens += tokens.input;
    totals.cacheWriteTokens += tokens.cacheWrite;
    totals.cacheReadTokens += tokens.cacheRead;
    totals.outputTokens += tokens.output;
  }
  return totals;
}

// every column a retry may change; documentKey and headerId are the row's identity
const UPDATED = [
  'filePath',
  'status',
  'seconds',
  'vendor',
  'entity',
  'employeeName',
  'nik',
  'position',
  'startDate',
  'endDate',
  'months',
  'signature',
  'checks',
  'reasons',
  'result',
  'reusedFromHeaderId',
] as const;
const FROM_INSERT = Object.fromEntries(UPDATED.map((column) => [column, sql.raw(`excluded."${column}"`)]));

/** The SQLSTATE or driver code of a failed query: never its message, which can carry the values written. */
function errorCode(error: unknown): string {
  for (let e: unknown = error, depth = 0; e && depth < 3; e = (e as { cause?: unknown }).cause, depth++) {
    const { errno, code } = e as { errno?: unknown; code?: unknown };
    const found = [errno, code].find((c) => typeof c === 'string' && /^[A-Z0-9_]{2,40}$/.test(c));
    if (found) return found as string;
  }
  return error instanceof Error ? error.name : 'unknown';
}

type Row = DetailValues & { reusedFromHeaderId: number | null };

class DatabaseRun implements RunArchive {
  #chain: Promise<void>;
  #header: Promise<void>; // settles once the header row is known (written, or found when resuming)
  #headerId: number | null = null;
  #failed = false;

  constructor(
    private readonly db: Database,
    private readonly jobId: string,
    header: { insert: typeof ocrHeaders.$inferInsert } | { id: number },
  ) {
    this.#chain = Promise.resolve();
    if ('id' in header) this.#headerId = header.id;
    else {
      this.#then('run', async () => {
        const [row] = await this.db.insert(ocrHeaders).values(header.insert).returning();
        this.#headerId = row?.id ?? null;
      });
    }
    this.#header = this.#chain;
  }

  get failed(): boolean {
    return this.#failed;
  }

  settled(): Promise<void> {
    return this.#chain;
  }

  async headerId(): Promise<number | null> {
    await this.#header;
    return this.#headerId;
  }

  status(status: 'queued' | 'running'): void {
    this.#withHeader('run', (id) => this.db.update(ocrHeaders).set({ status, finishedAt: null }).where(eq(ocrHeaders.id, id)));
  }

  file(file: JobFile, nik: string | null, readIn: RunArchive | null): void {
    const values = detailValues(file, nik); // now: the file object changes again if it is retried
    this.#withHeader('file', async (headerId) => {
      const reusedFromHeaderId = readIn ? await readIn.headerId() : null;
      await this.#upsert(headerId, [{ ...values, reusedFromHeaderId }]);
    });
  }

  finish(job: Job): void {
    const cancelled = job.files
      .filter((file) => file.state === 'cancelled')
      .map((file) => ({ ...detailValues(file, null), reusedFromHeaderId: null }));
    const outcome = {
      status: job.status,
      finishedAt: new Date(job.finishedAt ?? Date.now()),
      totalFiles: job.files.length + (job.missing ?? 0), // an interrupted run's unread files are not listed
      ...tokenTotals(job),
    };
    if (cancelled.length) this.#withHeader('file', (headerId) => this.#upsert(headerId, cancelled));
    this.#withHeader('run', (id) => this.db.update(ocrHeaders).set(outcome).where(eq(ocrHeaders.id, id)));
  }

  async #upsert(headerId: number, rows: Row[]): Promise<void> {
    for (let at = 0; at < rows.length; at += CHUNK) {
      await this.db
        .insert(ocrContractDetails)
        .values(rows.slice(at, at + CHUNK).map((row) => ({ ...row, headerId })))
        .onConflictDoUpdate({ target: [ocrContractDetails.headerId, ocrContractDetails.documentKey], set: FROM_INSERT });
    }
  }

  #withHeader(what: string, write: (headerId: number) => Promise<unknown>): void {
    // no header row (its insert failed): that failure is already counted and logged
    this.#then(what, async () => {
      if (this.#headerId !== null) await write(this.#headerId);
    });
  }

  #then(what: string, step: () => Promise<unknown>): void {
    this.#chain = this.#chain.then(step).then(
      () => undefined,
      (error: unknown) => {
        this.#failed = true;
        console.error(`job ${this.jobId}: could not save the ${what} to the database (${errorCode(error)})`);
      },
    );
  }
}

/** Runs and their files in the website's PostgreSQL database. */
export class DatabaseArchive implements ResultArchive {
  constructor(private readonly db: Database) {}

  open(job: Job, userId: number): RunArchive {
    return new DatabaseRun(this.db, job.id, {
      insert: {
        jobId: job.id,
        status: job.status,
        mode: job.mode,
        claude: job.claude,
        folders: JSON.stringify(job.folders),
        totalFiles: job.files.length,
        startedAt: new Date(job.startedAt),
        userId,
      },
    });
  }

  resume(jobId: string, headerId: number): RunArchive {
    return new DatabaseRun(this.db, jobId, { id: headerId });
  }

  list(offset: number, limit: number, filter: HistoryFilter = {}, userId: number | null = null): Promise<JobHistory> {
    return listRuns(this.db, offset, limit, filter, userId);
  }

  load(jobId: string): Promise<StoredRun | null> {
    return loadRun(this.db, jobId);
  }

  latest(path: string): Promise<LatestResult | null> {
    return latestReading(this.db, documentKey(path), path);
  }

  marks(paths: string[]): Promise<Record<string, ReadMark>> {
    return latestMarks(this.db, new Map(paths.map((path) => [documentKey(path), path])));
  }

  readings(paths: string[], failures = false): Promise<Map<string, LatestResult>> {
    return latestReadings(this.db, new Map(paths.map((path) => [documentKey(path), path])), failures);
  }

  async interruptUnfinished(): Promise<number> {
    // token totals are written when a run stops, so they come from the readings this run saved (not ones it took)
    const spent = (key: 'input' | 'cacheWrite' | 'cacheRead' | 'output') => sql<number>`coalesce((
      select sum(((d."result"::jsonb) -> 'tokens' ->> ${key})::bigint) from ${ocrContractDetails} d
      where d."headerId" = ${ocrHeaders.id} and d."reusedFromHeaderId" is null and d."result" is not null), 0)`;
    const rows = await this.db
      .update(ocrHeaders)
      .set({
        status: 'interrupted',
        finishedAt: new Date(),
        inputTokens: spent('input'),
        cacheWriteTokens: spent('cacheWrite'),
        cacheReadTokens: spent('cacheRead'),
        outputTokens: spent('output'),
      })
      .where(inArray(ocrHeaders.status, ['queued', 'running']))
      .returning(); // a column map here trips the two drivers' union type
    return rows.length;
  }
}
