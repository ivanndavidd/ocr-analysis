import type {
  ContractRow,
  FieldKey,
  FieldValue,
  HistoryFilter,
  Job,
  JobFile,
  JobHistory,
  JobStatus,
  JobSummary,
  LatestResult,
  ReadMark,
  ReadMode,
  RunRef,
  RunUser,
} from '@contract-ocr/shared-types';
import { type SQL, and, asc, count, desc, eq, exists, gte, ilike, inArray, isNull, lt, or, sql } from 'drizzle-orm';
import type { Database } from '../database/database.ts';
import { type OcrDetailRow, ocrContractDetails, ocrHeaders, users } from '../database/schema.ts';
import { vendorOf } from './export.ts';
import { type RowFacts, type SavedFields, keyedFields, legacyNote } from './fields.ts';

/** Reads saved runs back: the shared history list, and one run as the results page shows it. */

export interface StoredRun {
  job: Job;
  niks: Map<string, string>; // full NIK by file path
  headerId: number;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STATUSES = new Set<JobStatus>(['queued', 'running', 'done', 'cancelled', 'interrupted']);

// 'full_page' is the old no-OCR trial: its saved runs still load, though none can start any more
const readMode = (mode: string): ReadMode => (mode === 'claude_page' || mode === 'full_page' ? mode : 'ocr');
const runStatus = (status: string): JobStatus => (STATUSES.has(status as JobStatus) ? (status as JobStatus) : 'interrupted');
const iso = (date: Date | null) => (date ? date.toISOString() : null);
const runUser = (user: { id: number; name: string; email: string }): RunUser => ({ id: user.id, name: user.name, email: user.email });

function parsed<T>(json: string | null, fallback: T): T {
  if (!json) return fallback;
  try {
    return JSON.parse(json) as T;
  } catch {
    return fallback;
  }
}

const ACTIVE = ['queued', 'running'];
const OUTCOME_STATUS = { check: 'needs_check', failed: 'failed' } as const;

/** `%text%` for ILIKE, the text's own `%`, `_` and `\` taken literally. */
const containing = (text: string) => `%${text.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;

/** The history's filters as one SQL condition; undefined when there is none. Parameterised throughout. */
function historyWhere(db: Database, filter: HistoryFilter, userId: number | null): SQL | undefined {
  const conditions: (SQL | undefined)[] = [];
  const q = filter.q?.trim();
  if (q) {
    const pattern = containing(q);
    conditions.push(or(ilike(ocrHeaders.folders, pattern), ilike(users.name, pattern), ilike(users.email, pattern)));
  }
  if (filter.status) {
    conditions.push(filter.status === 'active' ? inArray(ocrHeaders.status, ACTIVE) : eq(ocrHeaders.status, filter.status));
  }
  if (filter.outcome) {
    const files = db
      .select({ one: sql`1` })
      .from(ocrContractDetails)
      .where(and(eq(ocrContractDetails.headerId, ocrHeaders.id), eq(ocrContractDetails.status, OUTCOME_STATUS[filter.outcome])));
    conditions.push(exists(files));
  }
  if (filter.mode) conditions.push(eq(ocrHeaders.mode, filter.mode));
  if (filter.mine) conditions.push(userId === null ? sql`false` : eq(ocrHeaders.userId, userId));
  if (filter.from) conditions.push(gte(ocrHeaders.startedAt, new Date(filter.from)));
  if (filter.to) conditions.push(lt(ocrHeaders.startedAt, new Date(filter.to)));
  return and(...conditions);
}

/** The same filters over a run kept in memory (a backend without a database). */
export function matchesHistory(run: JobSummary, filter: HistoryFilter, userId: number | null): boolean {
  const q = filter.q?.trim().toLowerCase();
  if (q && ![...run.folders, run.startedBy?.name ?? '', run.startedBy?.email ?? ''].some((text) => text.toLowerCase().includes(q))) {
    return false;
  }
  if (filter.status && !(filter.status === 'active' ? ACTIVE.includes(run.status) : run.status === filter.status)) return false;
  if (filter.outcome && (filter.outcome === 'check' ? run.check : run.failed) === 0) return false;
  if (filter.mode && run.mode !== filter.mode) return false;
  if (filter.mine && (userId === null || run.startedBy?.id !== userId)) return false;
  if (filter.from && run.startedAt < new Date(filter.from).toISOString()) return false;
  if (filter.to && run.startedAt >= new Date(filter.to).toISOString()) return false;
  return true;
}

/** A page of every user's runs that match the filters, newest first, with how their files came out and how many match
 *  in all. Only the page's runs are read, and their files only counted. */
export async function listRuns(
  db: Database,
  offset: number,
  limit: number,
  filter: HistoryFilter = {},
  userId: number | null = null,
): Promise<JobHistory> {
  const where = historyWhere(db, filter, userId);
  const [matching] = await db
    .select({ n: count() })
    .from(ocrHeaders)
    .innerJoin(users, eq(users.id, ocrHeaders.userId))
    .where(where);
  const total = matching?.n ?? 0;
  const page =
    offset < total
      ? await db
          .select({ header: ocrHeaders, user: users })
          .from(ocrHeaders)
          .innerJoin(users, eq(users.id, ocrHeaders.userId))
          .where(where)
          .orderBy(desc(ocrHeaders.id))
          .limit(limit)
          .offset(offset)
      : [];
  const ids = page.map((row) => row.header.id);
  const counts = ids.length
    ? await db
        .select({ headerId: ocrContractDetails.headerId, status: ocrContractDetails.status, n: count() })
        .from(ocrContractDetails)
        .where(inArray(ocrContractDetails.headerId, ids))
        .groupBy(ocrContractDetails.headerId, ocrContractDetails.status)
    : [];
  const of = (headerId: number, status: string) =>
    counts.find((c) => c.headerId === headerId && c.status === status)?.n ?? 0;
  return {
    more: offset + page.length < total,
    total,
    jobs: page.map(({ header, user }) => ({
      id: header.jobId,
      folders: parsed<string[]>(header.folders, []),
      claude: header.claude,
      mode: readMode(header.mode),
      status: runStatus(header.status),
      startedAt: header.startedAt.toISOString(),
      finishedAt: iso(header.finishedAt),
      startedBy: runUser(user),
      total: header.totalFiles,
      ready: of(header.id, 'ready'),
      check: of(header.id, 'needs_check'),
      failed: of(header.id, 'failed'),
    })),
  };
}

/** A saved run as the results page shows it; null when there is none with this id. A run the database still calls
 *  queued or running is not in this backend's memory, so the backend that ran it stopped: it is interrupted. */
export async function loadRun(db: Database, jobId: string): Promise<StoredRun | null> {
  if (!UUID.test(jobId)) return null;
  const [found] = await db
    .select({ header: ocrHeaders, user: users })
    .from(ocrHeaders)
    .innerJoin(users, eq(users.id, ocrHeaders.userId))
    .where(eq(ocrHeaders.jobId, jobId))
    .limit(1);
  if (!found) return null;
  const { header, user } = found;
  const details = await db
    .select()
    .from(ocrContractDetails)
    .where(eq(ocrContractDetails.headerId, header.id))
    .orderBy(asc(ocrContractDetails.filePath));
  const sourceIds = [...new Set(details.flatMap((d) => (d.reusedFromHeaderId === null ? [] : [d.reusedFromHeaderId])))];
  const sources = sourceIds.length
    ? await db
        .select({ id: ocrHeaders.id, jobId: ocrHeaders.jobId, by: users.name })
        .from(ocrHeaders)
        .innerJoin(users, eq(users.id, ocrHeaders.userId))
        .where(inArray(ocrHeaders.id, sourceIds))
    : [];
  const sourceOf = (id: number | null): RunRef | undefined => {
    const source = sources.find((s) => s.id === id);
    return source ? { jobId: source.jobId, by: source.by } : undefined;
  };

  const niks = new Map<string, string>();
  const files = details.map((detail): JobFile => {
    if (detail.nik) niks.set(detail.filePath, detail.nik);
    return savedFile(detail, sourceOf(detail.reusedFromHeaderId));
  });
  const status = runStatus(header.status);
  const missing = header.totalFiles - files.length;
  const job: Job = {
    id: header.jobId,
    folders: parsed<string[]>(header.folders, []),
    claude: header.claude,
    mode: readMode(header.mode),
    status: status === 'queued' || status === 'running' ? 'interrupted' : status,
    startedAt: header.startedAt.toISOString(),
    finishedAt: iso(header.finishedAt),
    files,
    startedBy: runUser(user),
    ...(missing > 0 ? { missing } : {}),
  };
  return { job, niks, headerId: header.id };
}

/** The newest reading of one PDF (by its root-relative path and that path's documentKey) in any user's run: the run
 *  that read it itself, not one that took the result from another run. Failed and cancelled files are no reading. */
export async function latestReading(db: Database, key: string, path: string): Promise<LatestResult | null> {
  return (await latestReadings(db, new Map([[key, path]]))).get(path) ?? null;
}

/** `latestReading` for many PDFs (root-relative path by its documentKey) at once. With `failures`, a PDF no run has
 *  read gets its newest failure instead (`file.state` 'error'), so an export can say why it is empty. */
export async function latestReadings(
  db: Database,
  paths: ReadonlyMap<string, string>,
  failures = false,
): Promise<Map<string, LatestResult>> {
  const found = new Map<string, LatestResult>();
  if (paths.size === 0) return found;
  const rows = await db
    .select({ detail: ocrContractDetails, header: ocrHeaders, by: users.name })
    .from(ocrContractDetails)
    .innerJoin(ocrHeaders, eq(ocrHeaders.id, ocrContractDetails.headerId))
    .innerJoin(users, eq(users.id, ocrHeaders.userId))
    .where(
      and(
        inArray(ocrContractDetails.documentKey, [...paths.keys()]),
        inArray(ocrContractDetails.status, failures ? ['ready', 'needs_check', 'failed'] : ['ready', 'needs_check']),
        isNull(ocrContractDetails.reusedFromHeaderId),
      ),
    )
    .orderBy(desc(ocrHeaders.id));
  for (const { detail, header, by } of rows) {
    if (detail.filePath !== paths.get(detail.documentKey)) continue;
    const known = found.get(detail.filePath);
    // newest first: the first reading wins, and any reading beats a newer failure
    if (known && (known.file.state === 'done' || detail.status === 'failed')) continue;
    found.set(detail.filePath, { file: savedFile(detail, undefined), nik: detail.nik, run: runOf(header, by) });
  }
  return found;
}

/** How each PDF (root-relative path by its documentKey) came out in its newest reading, as `latestReading` picks it;
 *  PDFs no run has read are left out. */
export async function latestMarks(db: Database, paths: ReadonlyMap<string, string>): Promise<Record<string, ReadMark>> {
  if (paths.size === 0) return {};
  const rows = await db
    .select({ key: ocrContractDetails.documentKey, path: ocrContractDetails.filePath, status: ocrContractDetails.status, header: ocrHeaders, by: users.name })
    .from(ocrContractDetails)
    .innerJoin(ocrHeaders, eq(ocrHeaders.id, ocrContractDetails.headerId))
    .innerJoin(users, eq(users.id, ocrHeaders.userId))
    .where(
      and(
        inArray(ocrContractDetails.documentKey, [...paths.keys()]),
        inArray(ocrContractDetails.status, ['ready', 'needs_check']),
        isNull(ocrContractDetails.reusedFromHeaderId),
      ),
    )
    .orderBy(desc(ocrHeaders.id));
  const marks: Record<string, ReadMark> = {};
  for (const row of rows) {
    if (row.path !== paths.get(row.key) || row.path in marks) continue; // newest first: the first row of a PDF wins
    marks[row.path] = { outcome: row.status === 'ready' ? 'ready' : 'needs_check', run: runOf(row.header, row.by) };
  }
  return marks;
}

const runOf = (header: typeof ocrHeaders.$inferSelect, by: string): LatestResult['run'] => ({
  jobId: header.jobId,
  by,
  mode: readMode(header.mode),
  status: runStatus(header.status),
  startedAt: header.startedAt.toISOString(),
  finishedAt: iso(header.finishedAt),
});

function savedFile(detail: OcrDetailRow, readIn: RunRef | undefined): JobFile {
  const at = detail.filePath.lastIndexOf('/');
  const file: JobFile = {
    path: detail.filePath,
    folder: at < 0 ? '' : detail.filePath.slice(0, at),
    name: detail.filePath.slice(at + 1),
    state: 'cancelled',
    ...(detail.seconds === null ? {} : { seconds: detail.seconds }),
  };
  if (detail.status === 'failed') {
    return { ...file, state: 'error', error: parsed<string[]>(detail.reasons, [])[0] ?? 'internal_error' };
  }
  if (detail.status !== 'ready' && detail.status !== 'needs_check') return file;
  return { ...file, state: 'done', row: savedRow(detail), ...(readIn ? { readIn } : {}) };
}

/** The reading a row keeps; rows saved before the whole reading was kept (2026-11-05) are rebuilt from their
 *  columns, so their values and yellow notes still show, without the details of how they were read. */
function savedRow(detail: OcrDetailRow): ContractRow {
  const kept = parsed<(RowFacts & { fields: SavedFields }) | null>(detail.result, null);
  if (kept) return { ...kept, fields: keyedFields(kept) };
  const checks = parsed<Partial<Record<FieldKey, string>>>(detail.checks, {});
  const field = (key: FieldKey, value: string | number | null): FieldValue => ({ value, check: legacyNote(checks[key]), basis: null });
  const masked = detail.nik ? `${'*'.repeat(12)}${detail.nik.slice(-4)}` : null;
  const { startDate: start, endDate: end } = detail;
  const period = start && end ? { start, end } : null;
  return {
    file: detail.filePath.slice(detail.filePath.lastIndexOf('/') + 1),
    fields: {
      nik: field('nik', masked),
      vendor: field('vendor', detail.vendor),
      entity: field('entity', detail.entity),
      name: field('name', detail.employeeName),
      position: field('position', detail.position),
      start: field('start', start),
      end: field('end', end),
      months: field('months', detail.months),
      signature: field('signature', detail.signature),
    },
    reader: 'pattern',
    readByFallback: false,
    claudeCalls: [],
    tokens: null,
    vendorFromFolder: vendorOf(detail.filePath) || null,
    vendorInDocument: null,
    vendorBasis: null,
    vendorFromDocument: null,
    entityFromFilename: detail.entity,
    nameFromFilename: detail.employeeName,
    nameFromDocument: null,
    nameInDocument: null,
    nameMatch: null,
    readConfidence: { name: null, period: null, nik: null },
    nik: { status: masked ? 'found' : 'missing', masked, birthDate: 'absent', gender: 'absent', fromOcr: false, claude: null },
    position: { status: detail.position ? 'found' : 'missing', value: detail.position, confidence: null, agreement: null, source: null },
    filenameStandard: true,
    periodFromFilename: null,
    periodFromDocument: period,
    documentPeriod: { status: period ? 'found' : 'missing', start, end, durationMonths: detail.months },
    periodMatches: null,
    signature: detail.signature === 'YES' ? 'signed' : detail.signature === 'NO' ? 'unsigned' : null,
    signatureByClaude: null,
    decision: Object.keys(checks).length ? 'review_priority' : 'review',
    reasons: parsed<string[]>(detail.reasons, []),
    detail: { identityPages: [], signaturePage: null, pageKinds: {}, ocrConfidence: {}, signatureSource: null, vendorNote: null },
  };
}

