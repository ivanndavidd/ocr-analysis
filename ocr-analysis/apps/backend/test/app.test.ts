import { afterAll, beforeAll, describe, expect, spyOn, test } from 'bun:test';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type {
  AuthSession,
  CurrentRun,
  FolderListing,
  FolderMark,
  Health,
  Job,
  JobHistory,
  LatestResult,
  ManagedUser,
  ReadMark,
  UserList,
} from '@contract-ocr/shared-types';
import { createApp } from '../src/app.ts';
import { SESSION_COOKIE, SessionStore } from '../src/modules/auth/sessions.ts';
import { asc, desc, eq } from 'drizzle-orm';
import { type OpenDatabase, openDatabase } from '../src/modules/database/database.ts';
import { ocrContractDetails, ocrHeaders } from '../src/modules/database/schema.ts';
import { DatabaseArchive, documentKey } from '../src/modules/jobs/archive.ts';
import { UserStore } from '../src/modules/users/store.ts';
import type { ExtractorClient } from '../src/modules/workers/protocol.ts';
import { WorkerPool } from '../src/modules/workers/worker-pool.ts';
import { LocalFolderSource } from '../src/modules/folders/source.ts';
import type { WorkbookSpec } from '../src/modules/jobs/export.ts';
import { JobService } from '../src/modules/jobs/service.ts';
import { WorkbookError } from '../src/modules/jobs/workbook.ts';

const HOST = 'localhost:3000';
const NIK = '3199990101900001'; // the fake worker's fictitious NIK
let pool: WorkerPool;
let app: ReturnType<typeof createApp>;
let exported: WorkbookSpec | null = null;
let writerFails = false;
let root = '';
let database: OpenDatabase;
let users: UserStore;
let cookie = ''; // a signed-in session, sent with every request unless a test says otherwise
// fictitious test users: ADMIN is in ADMIN_EMAILS (an active admin on every sign-in); everyone else starts inactive
const TESTER = { email: 'penguji.satu@gdn-commerce.com', name: 'Penguji Satu' };
const ADMIN = { email: 'penguji.admin@gdn-commerce.com', name: 'Penguji Admin' };
let adminCookie = '';

beforeAll(async () => {
  database = await openDatabase({ kind: 'embedded', dir: null });
  users = new UserStore(database.db, new Set([ADMIN.email]));
  root = mkdtempSync(join(tmpdir(), 'ocr-app-'));
  const add = (folder: string, names: string[]) => {
    mkdirSync(join(root, folder), { recursive: true });
    for (const name of names) writeFileSync(join(root, folder, name), '%PDF-1.4 fake');
  };
  add('PT A/Januari 2026 - Maret 2026', ['Budi Santoso - 1jan-31mar26.pdf', 'Siti - 1jan-31mar26.pdf', 'reject - x.pdf']);
  add('PT A/Lambat', ['slow - a.pdf']);
  add('PT A/Kosong', []);
  add('PT B/Feb 2026 - Apr 2026', ['Andi - 1feb-30apr26.pdf']);
  pool = new WorkerPool({
    cmd: [process.execPath, join(import.meta.dir, 'fixtures', 'fake-worker.ts')],
    size: 2,
    timeoutMs: 1500,
    recycleAfter: 100,
  });
  pool.start();
  const source = new LocalFolderSource(root);
  app = createApp({
    allowedHosts: new Set([HOST]),
    source,
    jobs: new JobService(source, pool, 1024 * 1024, 2, new DatabaseArchive(database.db)),
    extractor: pool,
    claude: false,
    workbook: async (spec) => {
      if (writerFails) throw new WorkbookError('failed');
      exported = spec;
      return new TextEncoder().encode('xlsx');
    },
    users,
    sessions: new SessionStore(),
  });
  adminCookie = sessionPair(await post('/api/auth/login', ADMIN, {}, null));
  cookie = await activeSession(TESTER);
});

afterAll(async () => {
  await pool.close();
  await database.close();
});

/** `name=value` of the session cookie a response set. */
function sessionPair(response: Response): string {
  const set = response.headers.get('set-cookie') ?? '';
  if (!set.startsWith(`${SESSION_COOKIE}=`)) throw new Error('no session cookie');
  return set.split(';')[0]!;
}

function call(path: string, init: RequestInit & { host?: string; cookie?: string | null } = {}) {
  const headers = new Headers(init.headers);
  headers.set('host', init.host ?? HOST);
  if (init.body) headers.set('content-type', 'application/json');
  const session = init.cookie === undefined ? cookie : init.cookie;
  if (session) headers.set('cookie', session);
  return app.handle(new Request(`http://${HOST}${path}`, { ...init, headers }));
}

const post = (path: string, body: unknown, headers: Record<string, string> = {}, session?: string | null) =>
  call(path, { method: 'POST', body: JSON.stringify(body), headers, cookie: session });

const patch = (path: string, body: unknown, session: string, headers: Record<string, string> = {}) =>
  call(path, { method: 'PATCH', body: JSON.stringify(body), headers, cookie: session });

/** Signs in (a new user is an inactive reviewer), then has the admin activate the user, as on the User page. */
async function activeSession(identity: { email: string; name: string }): Promise<string> {
  const response = await post('/api/auth/login', identity, {}, null);
  const { user } = (await response.clone().json()) as AuthSession;
  if (!user.active) {
    const activated = await patch(`/api/users/${user.id}`, { active: true }, adminCookie);
    if (activated.status !== 200) throw new Error(`activation answered ${activated.status}`);
  }
  return sessionPair(response);
}

async function finished(id: string, session: string = cookie): Promise<Job> {
  for (let i = 0; i < 200; i++) {
    const job = (await (await call(`/api/jobs/${id}`, { cookie: session })).json()) as Job;
    if (job.status !== 'running' && job.status !== 'queued') return job;
    await Bun.sleep(25);
  }
  throw new Error('job did not finish');
}

describe('API', () => {
  test('refuses other hosts and cross-origin writes', async () => {
    expect((await call('/api/health', { host: 'evil.example:3000' })).status).toBe(403);
    expect((await post('/api/jobs', { folders: ['PT A/Kosong'] }, { origin: 'https://evil.example' })).status).toBe(403);
    const ok = await call('/api/health');
    expect(ok.status).toBe(200);
    expect(ok.headers.get('cache-control')).toBe('no-store');
  });

  test('lists folders and rejects paths outside the root', async () => {
    const listing = (await (await call('/api/folders?path=PT%20A')).json()) as FolderListing;
    expect(listing.folders.map((f) => [f.name, f.pdfCount])).toEqual([
      ['Januari 2026 - Maret 2026', 3],
      ['Kosong', 0],
      ['Lambat', 1],
    ]);
    const root = (await (await call('/api/folders')).json()) as FolderListing;
    expect(root.folders.map((f) => [f.name, f.pdfCount])).toEqual([
      ['PT A', 4],
      ['PT B', 1],
    ]); // counts include subfolders
    expect((await call('/api/folders?path=..%2F..')).status).toBe(400);
    expect((await call('/api/folders?path=Nope')).status).toBe(404);
  });

  test('runs a folder: rows for good files, an error for the rejected one, then retry', async () => {
    const created = await post('/api/jobs', { folders: ['PT A/Januari 2026 - Maret 2026'] });
    expect(created.status).toBe(201);
    const job = await finished(((await created.json()) as Job).id);
    expect(job.status).toBe('done');
    expect(job.claude).toBe(false); // the workers run without Claude

    const byName = Object.fromEntries(job.files.map((f) => [f.name, f]));
    const budi = byName['Budi Santoso - 1jan-31mar26.pdf']!;
    expect(budi).toMatchObject({ state: 'done', folder: 'PT A/Januari 2026 - Maret 2026' });
    expect(budi.seconds).toBeGreaterThanOrEqual(0); // how long the worker took
    expect(budi.row).toMatchObject({
      nameFromFilename: 'Budi Santoso',
      periodFromDocument: { start: '2026-01-01', end: '2026-03-31' },
      periodMatches: true,
      signature: 'signed',
      decision: 'review_priority',
      reasons: ['extractor_disabled', 'mode:ocr'], // the fake worker echoes the read mode
      nik: { status: 'found', masked: '************0001', birthDate: 'match', gender: 'absent', fromOcr: false },
    });
    expect(byName['Siti - 1jan-31mar26.pdf']!.row?.nik).toMatchObject({ status: 'missing', masked: null });
    expect(byName['reject - x.pdf']).toMatchObject({ state: 'error', error: 'too_large' });

    // The full NIK never travels with the job, only through the NIK call: a POST, which other origins cannot make.
    expect(JSON.stringify(job)).not.toContain(NIK);
    expect(await (await post(`/api/jobs/${job.id}/niks`, {})).json()).toEqual({ niks: { [budi.path]: NIK } });
    expect((await post(`/api/jobs/${job.id}/niks`, {}, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await call(`/api/jobs/${job.id}/niks`)).status).toBe(404);
    expect((await post('/api/jobs/nope/niks', {})).status).toBe(404);

    const retried = await post(`/api/jobs/${job.id}/retry`, { file: 'PT A/Januari 2026 - Maret 2026/reject - x.pdf' });
    expect(retried.status).toBe(200);
    expect(((await retried.json()) as Job).files.find((f) => f.name === 'reject - x.pdf')?.seconds).toBeUndefined();
    expect((await finished(job.id)).files.find((f) => f.name === 'reject - x.pdf')?.state).toBe('error');
    expect((await post(`/api/jobs/${job.id}/retry`, { file: budi.path })).status).toBe(409);
    expect((await post(`/api/jobs/${job.id}/retry`, { file: 'reject - x.pdf' })).status).toBe(404); // a path, not a name
  });

  test('a job reads in the OCR mode unless told otherwise; the no-OCR mode needs Claude', async () => {
    const job = await finished(((await (await post('/api/jobs', { folders: ['PT B'] })).json()) as Job).id);
    expect(job.mode).toBe('ocr');
    expect(job.files[0]!.row?.reasons).toContain('mode:ocr');
    // these workers read without Claude, so the mode that is Claude's reading alone is refused
    const refused = await post('/api/jobs', { folders: ['PT B'], mode: 'full_page' });
    expect([refused.status, await refused.json()]).toEqual([503, { error: 'claude_unavailable' }]);
    expect((await post('/api/jobs', { folders: ['PT B'], mode: 'photo' })).status).toBe(400);
  });

  test('a no-OCR job sends its mode to every file, retries included', async () => {
    const withClaude = new WorkerPool({
      cmd: [process.execPath, join(import.meta.dir, 'fixtures', 'fake-worker.ts')],
      size: 1,
      timeoutMs: 1500,
      recycleAfter: 100,
      env: { FAKE_CLAUDE: '1' },
    });
    withClaude.start();
    try {
      for (let i = 0; withClaude.claude() !== true; i++) {
        if (i > 200) throw new Error('worker did not start');
        await Bun.sleep(25);
      }
      const service = new JobService(new LocalFolderSource(root), withClaude, 1024 * 1024, 1);
      const done = async (id: string) => {
        for (let i = 0; i < 200 && ['queued', 'running'].includes((await service.get(id)).status); i++) await Bun.sleep(25);
        return service.get(id);
      };
      const job = await done((await service.start({ folders: ['PT A/Januari 2026 - Maret 2026'], files: [], mode: 'full_page' })).id);
      expect(job.mode).toBe('full_page');
      expect((await service.history(0, 5)).jobs[0]).toMatchObject({ id: job.id, mode: 'full_page' });
      const modes = (j: Job) => j.files.flatMap((f) => f.row?.reasons.filter((r) => r.startsWith('mode:')) ?? []);
      expect(modes(job)).toEqual(['mode:full_page', 'mode:full_page']); // the rejected file has no row
      const retried = await done((await service.retryFailed(job.id, null)).id);
      expect(retried.files.find((f) => f.name === 'reject - x.pdf')?.state).toBe('error');
      expect(modes(retried)).toEqual(['mode:full_page', 'mode:full_page']);
    } finally {
      await withClaude.close();
    }
  });

  test('in the no-OCR mode a file Claude could not read fails and can be retried; the OCR mode keeps its row', async () => {
    let claudeFails = true;
    const stub: ExtractorClient = {
      status: () => ({ state: 'ready', error: null, workers: 1 }),
      claude: () => true,
      close: async () => {},
      extract: async (filename, _data, _vendor, mode) => ({
        ok: true,
        filename: { name: 'Budi Santoso', start: '2026-01-01', end: '2026-03-31', duration_months: null, flags: [] },
        result: {
          file: filename,
          nik: null,
          status_ttd: 'uncertain',
          keputusan: 'review_priority',
          flags: [],
          catatan_vendor: null,
          log: {
            p_id: [1],
            p_ttd: 2,
            jenis_halaman: {},
            ocr_conf: {},
            periode_regex: null,
            tanda_tangan: null,
            alasan: [...(mode === 'full_page' ? ['read_full_pages'] : []), ...(claudeFails ? ['extractor_error:timeout'] : [])],
          },
        },
      }),
    };
    const service = new JobService(new LocalFolderSource(root), stub, 1024 * 1024, 1);
    const done = async (id: string) => {
      for (let i = 0; i < 200 && ['queued', 'running'].includes((await service.get(id)).status); i++) await Bun.sleep(10);
      return service.get(id);
    };
    const failed = await done((await service.start({ folders: ['PT B'], files: [], mode: 'full_page' })).id);
    expect(failed.files[0]).toMatchObject({ state: 'error', error: 'claude_failed' });
    expect(failed.files[0]?.row).toBeUndefined();
    claudeFails = false;
    const retried = await done((await service.retryFailed(failed.id, null)).id);
    expect(retried.files[0]).toMatchObject({ state: 'done', row: { reasons: ['read_full_pages'] } });
    // with OCR the patterns still read the file when a Claude call fails
    claudeFails = true;
    const ocr = await done((await service.start({ folders: ['PT B'], files: [] })).id);
    expect(ocr.files[0]).toMatchObject({ state: 'done', row: { reasons: ['extractor_error:timeout'] } });
  });

  test('retries every failed file of a job at once, and only those', async () => {
    const created = await post('/api/jobs', { folders: ['PT A/Januari 2026 - Maret 2026'] });
    const job = await finished(((await created.json()) as Job).id);
    const doneBefore = job.files.filter((f) => f.state === 'done').map((f) => [f.path, f.seconds]);

    const retried = await post(`/api/jobs/${job.id}/retry-failed`, {});
    expect(retried.status).toBe(200);
    const queued = (await retried.json()) as Job;
    const reject = queued.files.find((f) => f.name === 'reject - x.pdf')!;
    expect(['waiting', 'reading']).toContain(reject.state); // a lane may claim it before the reply is built
    expect(reject.error).toBeUndefined();
    const after = await finished(job.id);
    expect(after.files.find((f) => f.name === 'reject - x.pdf')?.state).toBe('error');
    // the files that were read are not read again
    expect(after.files.filter((f) => f.state === 'done').map((f) => [f.path, f.seconds])).toEqual(doneBefore);

    expect((await post(`/api/jobs/${job.id}/retry-failed`, {}, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await post('/api/jobs/nope/retry-failed', {})).status).toBe(404);
    const clean = await finished(((await (await post('/api/jobs', { folders: ['PT B'] })).json()) as Job).id);
    expect(await (await post(`/api/jobs/${clean.id}/retry-failed`, {})).json()).toEqual({ error: 'not_retryable' });
  });

  test('runs several folders at once, subfolders included, each file once', async () => {
    const created = await post('/api/jobs', {
      folders: ['PT B', 'PT A/Januari 2026 - Maret 2026', 'PT B/Feb 2026 - Apr 2026'],
    });
    expect(created.status).toBe(201);
    const job = await finished(((await created.json()) as Job).id);
    expect(job.folders).toEqual(['PT B', 'PT A/Januari 2026 - Maret 2026', 'PT B/Feb 2026 - Apr 2026']);
    expect(job.files.map((f) => f.path)).toEqual([
      'PT B/Feb 2026 - Apr 2026/Andi - 1feb-30apr26.pdf',
      'PT A/Januari 2026 - Maret 2026/Budi Santoso - 1jan-31mar26.pdf',
      'PT A/Januari 2026 - Maret 2026/reject - x.pdf',
      'PT A/Januari 2026 - Maret 2026/Siti - 1jan-31mar26.pdf',
    ]);
    expect(job.files.filter((f) => f.state === 'done')).toHaveLength(3);

    // Every user's runs can be found again: newest first, with who started them and how their files came out.
    const { jobs } = (await (await call('/api/jobs')).json()) as JobHistory;
    expect(jobs[0]).toMatchObject({ id: job.id, status: 'done', total: 4, failed: 1, startedBy: { email: TESTER.email } });
    expect(jobs[0]!.ready + jobs[0]!.check).toBe(3);
    expect(JSON.stringify(jobs)).not.toContain('Budi'); // folders and counts only, no employee names
  });

  test('runs single PDFs, alone or next to folders', async () => {
    const period = 'PT A/Januari 2026 - Maret 2026';
    const siti = `${period}/Siti - 1jan-31mar26.pdf`;
    const mixed = await post('/api/jobs', { folders: ['PT B'], files: [siti, `${period}\\Siti - 1jan-31mar26.pdf`] });
    expect(mixed.status).toBe(201);
    const job = await finished(((await mixed.json()) as Job).id);
    expect(job.folders).toEqual(['PT B', period]);
    expect(job.files.map((f) => f.path)).toEqual(['PT B/Feb 2026 - Apr 2026/Andi - 1feb-30apr26.pdf', siti]);

    const alone = await finished(((await (await post('/api/jobs', { files: [siti] })).json()) as Job).id);
    expect([alone.folders, alone.files.map((f) => f.state)]).toEqual([[period], ['done']]);

    const refused = async (files: string[]) => (await (await post('/api/jobs', { files })).json()) as unknown;
    expect(await refused(['PT A/Lambat'])).toEqual({ error: 'not_pdf' }); // a folder is not a file
    expect(await refused([`${period}/Nobody - x.pdf`])).toEqual({ error: 'not_found' });
    expect(await refused(['../luar.pdf'])).toEqual({ error: 'bad_path' });
    expect((await post('/api/jobs', { folders: [], files: [] })).status).toBe(400);
  });

  test('one run per user at a time; a hung file times out', async () => {
    const created = (await (await post('/api/jobs', { folders: ['PT A/Lambat'] })).json()) as Job;
    expect(created).toMatchObject({ status: 'running', startedBy: { email: TESTER.email } }); // nothing was ahead of it
    const second = await post('/api/jobs', { folders: ['PT A/Januari 2026 - Maret 2026'] });
    expect(second.status).toBe(409);
    expect(await second.json()).toEqual({ error: 'user_has_job', jobId: created.id });
    expect((await (await call('/api/jobs/current')).json()) as CurrentRun).toMatchObject({ job: { id: created.id }, active: 1 });
    const early = await post(`/api/jobs/${created.id}/export`, {});
    expect([early.status, await early.json()]).toEqual([409, { error: 'job_not_finished' }]);

    const job = await finished(created.id);
    expect(job.files[0]).toMatchObject({ state: 'error', error: 'timeout' });
    expect((await (await call('/api/jobs/current')).json()) as CurrentRun).toEqual({ job: null, active: 0 });
  });

  test("another user's run waits in the queue and takes the results of PDFs the run ahead reads", async () => {
    const other = await activeSession({ email: 'penguji.dua@gdn-commerce.com', name: 'Penguji Dua' });
    const first = (await (await post('/api/jobs', { folders: ['PT A'] })).json()) as Job; // its slow file times out
    const queued = await post('/api/jobs', { folders: ['PT A/Januari 2026 - Maret 2026'] }, {}, other);
    expect(queued.status).toBe(201);
    const second = (await queued.json()) as Job;
    expect(second).toMatchObject({ status: 'queued', queueAhead: 1, startedBy: { name: 'Penguji Dua' } });
    // each PDF the first run reads (rather than has already failed) is either read there already or waits for it
    for (const file of second.files.filter((f) => !f.name.startsWith('reject'))) {
      expect(file.readIn?.jobId ?? file.waitingFor?.jobId).toBe(first.id);
    }
    expect((await (await call('/api/jobs/current', { cookie: other })).json()) as CurrentRun).toMatchObject({
      job: { id: second.id },
      active: 2,
    });
    // only whoever started a run may cancel it; anyone may open it
    expect([(await post(`/api/jobs/${second.id}/cancel`, {})).status, (await call(`/api/jobs/${second.id}`)).status]).toEqual([403, 200]);

    const done = await finished(second.id, other);
    expect(done.status).toBe('done');
    const byName = Object.fromEntries(done.files.map((f) => [f.name, f]));
    for (const name of ['Budi Santoso - 1jan-31mar26.pdf', 'Siti - 1jan-31mar26.pdf']) {
      expect(byName[name]).toMatchObject({ state: 'done', readIn: { jobId: first.id, by: TESTER.name } });
    }
    // the first run failed this one, so the second read it itself (and failed it too)
    expect(byName['reject - x.pdf']).toMatchObject({ state: 'error', error: 'too_large' });
    expect(byName['reject - x.pdf']!.readIn).toBeUndefined();
    // the full NIK came along with the result
    expect(await (await post(`/api/jobs/${second.id}/niks`, {}, {}, other)).json()).toEqual({ niks: { [byName['Budi Santoso - 1jan-31mar26.pdf']!.path]: NIK } });
    expect((await finished(first.id)).status).toBe('done');
  });

  test('exports a finished job as an Excel download: ALL, then one sheet per vendor', async () => {
    const created = await post('/api/jobs', { folders: ['PT B', 'PT A/Januari 2026 - Maret 2026'] });
    const job = await finished(((await created.json()) as Job).id);

    const response = await post(`/api/jobs/${job.id}/export`, {});
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toContain('spreadsheetml');
    const disposition = response.headers.get('content-disposition') ?? '';
    expect(disposition).toStartWith(`attachment; filename="Hasil OCR PKWT.xlsx"; filename*=UTF-8''`);
    expect(decodeURIComponent(disposition.split("''")[1] ?? '')).toMatch(
      /^Hasil OCR PKWT - 2 vendor - \d{1,2} \w{3} \d{4} \d{2}\.\d{2}\.xlsx$/,
    );
    expect(await response.text()).toBe('xlsx');
    expect(exported?.sheets.map((s) => [s.name, s.rows.length])).toEqual([
      ['ALL', 4],
      ['PT A', 3],
      ['PT B', 1],
    ]);
    const budi = exported?.sheets[0]?.rows.find((r) => String(r.at(-1)?.value).includes('Budi'));
    expect(budi?.[0]).toEqual({ value: NIK }); // the data output carries the full NIK (owner decision 2026-11-02)

    expect((await post(`/api/jobs/${job.id}/export`, {}, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await post('/api/jobs/nope/export', {})).status).toBe(404);
    writerFails = true;
    const failed = await post(`/api/jobs/${job.id}/export`, {});
    writerFails = false;
    expect([failed.status, await failed.json()]).toEqual([500, { error: 'export_failed' }]);
  });

  test('says Claude is off when the backend runs without it', async () => {
    const health = (await (await call('/api/health')).json()) as Health;
    expect(health.claude).toBe('off');
    expect(health.extractor.state).toBe('ready');
  });

  test('rejects empty folders and bad bodies', async () => {
    expect(await (await post('/api/jobs', { folders: ['PT A/Kosong'] })).json()).toEqual({ error: 'no_pdfs' });
    expect(await (await post('/api/jobs', { folders: ['PT A/Kosong', '../..'] })).json()).toEqual({ error: 'bad_path' });
    expect((await post('/api/jobs', { folders: [] })).status).toBe(400);
    expect((await post('/api/jobs', { path: 'PT A/Lambat' })).status).toBe(400);
    expect((await call('/api/jobs/nope')).status).toBe(404);
  });

  test('the history pages and filters on the server, and refuses bad filters', async () => {
    const history = async (query: string) => {
      const response = await call(`/api/jobs?${query}`);
      expect(response.status).toBe(200);
      return (await response.json()) as JobHistory;
    };
    const all = await history('limit=100');
    expect(all.total).toBe(all.jobs.length);
    const second = await history('offset=1&limit=1');
    expect(second).toMatchObject({ total: all.total, more: all.total > 2 });
    expect(second.jobs.map((j) => j.id)).toEqual([all.jobs[1]!.id]);

    const mine = await history('mine=true&limit=100');
    expect(mine.total).toBeGreaterThan(0);
    expect(mine.total).toBeLessThan(all.total); // another user ran too
    expect(mine.jobs.every((j) => j.startedBy?.email === TESTER.email)).toBe(true);
    const folder = all.jobs[0]!.folders[0]!;
    const found = await history(`q=${encodeURIComponent(folder.toUpperCase())}&limit=100`);
    expect(found.jobs.map((j) => j.id)).toContain(all.jobs[0]!.id);
    expect(found.jobs.every((j) => j.folders.some((f) => f.includes(folder)))).toBe(true);
    expect(await history('from=2999-01-01T00:00:00.000Z')).toEqual({ jobs: [], more: false, total: 0 });

    for (const bad of ['status=bogus', 'outcome=all', 'mode=pdf', 'mine=perhaps', `q=${'x'.repeat(101)}`, 'from=yesterday', 'to=2026-13-45T00:00:00Z', 'limit=0']) {
      expect([bad, (await call(`/api/jobs?${bad}`)).status]).toEqual([bad, 400]);
    }
  });

  test('streams a PDF but nothing else', async () => {
    const pdf = await call(`/api/files?path=${encodeURIComponent('PT A/Januari 2026 - Maret 2026/Budi Santoso - 1jan-31mar26.pdf')}`);
    expect(pdf.status).toBe(200);
    expect(pdf.headers.get('content-type')).toBe('application/pdf');
    expect(await pdf.text()).toBe('%PDF-1.4 fake');
    expect((await call(`/api/files?path=${encodeURIComponent('../x.pdf')}`)).status).toBe(400);
  });
});

describe('saving runs to the database', () => {
  type Header = typeof ocrHeaders.$inferSelect;
  const latestHeader = async (): Promise<Header> =>
    (await database.db.select().from(ocrHeaders).orderBy(desc(ocrHeaders.id)).limit(1))[0]!;
  /** The latest run's header, once the end of `job` has been written to it. */
  async function saved(job: Job): Promise<Header> {
    for (let i = 0; i < 200; i++) {
      const header = await latestHeader();
      if (header.finishedAt?.toISOString() === job.finishedAt) return header;
      await Bun.sleep(25);
    }
    throw new Error('the run was not saved');
  }
  const rowsOf = (headerId: number) =>
    database.db
      .select()
      .from(ocrContractDetails)
      .where(eq(ocrContractDetails.headerId, headerId))
      .orderBy(asc(ocrContractDetails.filePath));
  const PERIOD = 'PT A/Januari 2026 - Maret 2026';

  test('Jalankan OCR saves the run under whoever started it, a row per file, and a retry updates its row', async () => {
    const { user } = (await (await call('/api/auth/me')).json()) as AuthSession;
    const job = await finished(((await (await post('/api/jobs', { folders: [PERIOD] })).json()) as Job).id);
    const header = await saved(job);
    expect(header).toMatchObject({ status: 'done', userId: user.id, totalFiles: 3, folders: JSON.stringify([PERIOD]) });
    expect(header.startedAt.toISOString()).toBe(job.startedAt);
    expect(job.saveFailed).toBeUndefined();

    const rows = await rowsOf(header.id);
    const row = (name: string) => rows.find((r) => r.filePath === `${PERIOD}/${name}`)!;
    expect(rows).toHaveLength(3);
    for (const file of job.files) {
      const saved = row(file.name);
      expect(saved.documentKey).toBe(documentKey(file.path));
      if (!file.row) continue;
      // a value the page marks yellow is a value to check
      const toCheck = Object.values(file.row.fields).some((field) => field.check !== null);
      expect([file.name, saved.status]).toEqual([file.name, toCheck ? 'needs_check' : 'ready']);
      expect(saved).toMatchObject({
        employeeName: file.row.fields.name.value,
        startDate: '2026-01-01',
        endDate: '2026-03-31',
        signature: file.row.fields.signature.value,
      });
    }
    expect(row('Budi Santoso - 1jan-31mar26.pdf')).toMatchObject({ employeeName: 'Budi Santoso', nik: NIK, vendor: 'PT A' });
    expect(row('Siti - 1jan-31mar26.pdf').nik).toBeNull();
    expect(row('reject - x.pdf')).toMatchObject({ status: 'failed', reasons: '["too_large"]', checks: null, employeeName: null });

    // a retry in the same run updates the file's row and the run's end; it adds no row and no run
    await post(`/api/jobs/${job.id}/retry`, { file: `${PERIOD}/reject - x.pdf` });
    const again = await finished(job.id);
    expect(again.finishedAt).not.toBe(job.finishedAt);
    expect((await saved(again)).id).toBe(header.id);
    const after = await rowsOf(header.id);
    expect(after).toHaveLength(3);
    expect(after.find((r) => r.filePath.endsWith('reject - x.pdf'))).toMatchObject({ status: 'failed', reasons: '["too_large"]' });
  });

  test('after a restart a run opens from the database as it was, and its starter can retry it', async () => {
    const { user } = (await (await call('/api/auth/me')).json()) as AuthSession;
    const before = await finished(((await (await post('/api/jobs', { folders: [PERIOD] })).json()) as Job).id);
    await saved(before);
    // a new backend: nothing in memory, the same database
    const restarted = new JobService(new LocalFolderSource(root), pool, 1024 * 1024, 2, new DatabaseArchive(database.db));
    const opened = await restarted.get(before.id);
    const byPath = (job: Job) => [...job.files].sort((x, y) => x.path.localeCompare(y.path));
    expect(byPath(opened)).toEqual(byPath(before));
    expect(opened).toMatchObject({ status: 'done', startedBy: { id: user.id, email: TESTER.email }, mode: 'ocr' });
    const live = (await (await post(`/api/jobs/${before.id}/niks`, {})).json()) as { niks: Record<string, string> };
    expect(await restarted.niks(before.id)).toEqual(live.niks);
    expect((await restarted.forExport(before.id)).job.files).toHaveLength(3);
    expect((await restarted.history(0, 1)).jobs[0]).toMatchObject({ id: before.id, total: 3, failed: 1 });

    await expect(restarted.retryFailed(before.id, user.id + 1000)).rejects.toMatchObject({ code: 'not_your_job' });
    await restarted.retryFailed(before.id, user.id);
    for (let i = 0; i < 200 && (await restarted.get(before.id)).status !== 'done'; i++) await Bun.sleep(25);
    const again = await restarted.get(before.id);
    expect(again.files.find((f) => f.name === 'reject - x.pdf')).toMatchObject({ state: 'error', error: 'too_large' });
    const header = await saved(again);
    expect(header.jobId).toBe(before.id);
    expect(await rowsOf(header.id)).toHaveLength(3); // the same run's rows, updated
  });

  test('a cancelled run keeps the files it read and marks the rest cancelled', async () => {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    const stub: ExtractorClient = {
      status: () => ({ state: 'ready', error: null, workers: 1 }),
      claude: () => false,
      close: async () => {},
      extract: async (filename, data, vendor, mode) => {
        await held; // the first file is still being read when the run is cancelled
        return pool.extract(filename, data, vendor, mode);
      },
    };
    const service = new JobService(new LocalFolderSource(root), stub, 1024 * 1024, 1, new DatabaseArchive(database.db));
    const { user } = (await (await call('/api/auth/me')).json()) as AuthSession;
    const started = await service.start({ folders: [PERIOD], files: [] }, user);
    await service.cancel(started.id, user.id);
    release();
    for (let i = 0; i < 200 && (await service.get(started.id)).status === 'running'; i++) await Bun.sleep(10);
    const job = await service.get(started.id);
    expect(job.status).toBe('cancelled');

    const header = await saved(job);
    expect(header).toMatchObject({ status: 'cancelled', totalFiles: 3, userId: user.id });
    const read = job.files.filter((f) => f.state === 'done');
    expect(read).toHaveLength(1);
    const rows = await rowsOf(header.id);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      if (row.filePath === read[0]!.path) {
        expect(row).toMatchObject({ employeeName: read[0]!.row!.fields.name.value });
        expect(['ready', 'needs_check']).toContain(row.status);
      } else {
        expect(row).toMatchObject({ status: 'cancelled', employeeName: null, checks: null, reasons: null, vendor: 'PT A' });
      }
    }
  });

  test("a PDF opened from the folder page shows its newest reading in any user's run, with the full NIK", async () => {
    const budi = `${PERIOD}/Budi Santoso - 1jan-31mar26.pdf`;
    const other = await activeSession({ email: 'penguji.pdf@gdn-commerce.com', name: 'Penguji PDF' });
    const job = await finished(((await (await post('/api/jobs', { files: [budi] }, {}, other)).json()) as Job).id, other);
    await saved(job);

    const opened = (await (await post('/api/files/result', { path: budi })).json()) as { result: LatestResult | null };
    expect(opened.result).toMatchObject({
      nik: NIK,
      file: { path: budi, state: 'done', row: { nameFromFilename: 'Budi Santoso' } },
      run: { jobId: job.id, by: 'Penguji PDF', mode: 'ocr', status: 'done' },
    });
    expect(await (await post('/api/files/result', { path: `${PERIOD}/reject - x.pdf` })).json()).toEqual({ result: null }); // only failed
    expect(await (await post('/api/files/result', { path: 'PT A/tidak ada.pdf' })).json()).toEqual({ result: null });
    // the full NIK leaves only through a POST, which other origins cannot make
    expect((await post('/api/files/result', { path: budi }, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await call(`/api/files/result?path=${encodeURIComponent(budi)}`)).status).toBe(404);
    expect((await post('/api/files/result', { path: '../luar.pdf' })).status).toBe(400);
    expect((await post('/api/files/result', { path: budi }, {}, null)).status).toBe(401);

    // the folder list marks each PDF some run has read, without its values
    const paths = [budi, `${PERIOD}/reject - x.pdf`, 'PT A/tidak ada.pdf'];
    const marked = (await (await post('/api/files/marks', { paths })).json()) as { marks: Record<string, ReadMark> };
    const toCheck = Object.values(opened.result!.file.row!.fields).some((field) => field.check !== null);
    expect(marked).toEqual({ marks: { [budi]: { outcome: toCheck ? 'needs_check' : 'ready', run: opened.result!.run } } });
    expect(JSON.stringify(marked)).not.toContain(NIK);
    expect((await post('/api/files/marks', { paths: Array.from({ length: 2001 }, (_, i) => `PT A/${i}.pdf`) })).status).toBe(400);
    expect((await post('/api/files/marks', { paths: ['PT A/../luar.pdf'] })).status).toBe(400);
    expect((await post('/api/files/marks', { paths }, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await post('/api/files/marks', { paths }, {}, null)).status).toBe(401);
  });

  test("the folder page's download: each PDF's newest reading, once the unread ones are read in a run of their own", async () => {
    const budi = `${PERIOD}/Budi Santoso - 1jan-31mar26.pdf`;
    const reject = `${PERIOD}/reject - x.pdf`; // the fake worker never reads it
    // by now every PDF of the period has a reading but the one that always fails
    const refused = await post('/api/files/export', { folders: [PERIOD] });
    expect(refused.status).toBe(409);
    expect(await refused.json()).toEqual({ error: 'not_all_read', unread: 1, total: 3 });

    // the page reads the unread ones first, as a run of those alone
    const started = (await (await post('/api/jobs', { folders: [PERIOD], unreadOnly: true })).json()) as Job;
    const run = await finished(started.id);
    expect(run.folders).toEqual([PERIOD]);
    expect(run.files.map((f) => [f.path, f.state])).toEqual([[reject, 'error']]);
    await saved(run);

    // then asks again with includeUnread: a PDF still unread is a row that says why
    exported = null;
    const response = await post('/api/files/export', { folders: [PERIOD], includeUnread: true });
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toContain('spreadsheetml');
    expect(response.headers.get('content-disposition')).toContain(encodeURIComponent('Hasil OCR PKWT - PT A'));
    const rows = exported!.sheets[0]!.rows;
    expect(rows.map((row) => row.at(-1)?.value)).toEqual([budi, reject, `${PERIOD}/Siti - 1jan-31mar26.pdf`]); // folder order
    expect(rows[0]![0]).toEqual({ value: NIK }); // the full NIK of the newest reading
    expect(rows[1]![0]!.check).toContain('File tidak terbaca'); // its newest failure
    expect(exported!.sheets.map((sheet) => sheet.name)).toEqual(['ALL', 'PT A']);

    // nothing unread: a run of the unread PDFs alone has nothing to do, and the download needs no run
    const nothing = await post('/api/jobs', { files: [budi], unreadOnly: true });
    expect(nothing.status).toBe(409);
    expect(await nothing.json()).toMatchObject({ error: 'nothing_to_read' });
    expect((await post('/api/files/export', { files: [budi] })).status).toBe(200);

    expect((await post('/api/files/export', { folders: [PERIOD] }, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await post('/api/files/export', { folders: [PERIOD] }, {}, null)).status).toBe(401);
    expect((await post('/api/files/export', { folders: ['../luar'] })).status).toBe(400);
    expect((await post('/api/files/export', { folders: ['PT A/Kosong'] })).status).toBe(400); // no PDFs
  });

  test('the folder list marks each subfolder by how many of its PDFs some run has read', async () => {
    const folderMarks = async (path: string) => {
      const response = await call(`/api/folders/marks?path=${encodeURIComponent(path)}`);
      expect(response.status).toBe(200);
      return ((await response.json()) as { marks: Record<string, FolderMark> }).marks;
    };
    const readings = async (paths: string[]) =>
      Object.values(((await (await post('/api/files/marks', { paths })).json()) as { marks: Record<string, ReadMark> }).marks);

    // the period: every PDF read but the one the fake worker never reads, counted as the PDF marks count them
    const period = await readings(['Budi Santoso - 1jan-31mar26.pdf', 'Siti - 1jan-31mar26.pdf', 'reject - x.pdf'].map((n) => `${PERIOD}/${n}`));
    expect(period).toHaveLength(2);
    const inA = await folderMarks('PT A');
    expect(inA[PERIOD]).toEqual({ total: 3, read: 2, check: period.filter((m) => m.outcome === 'needs_check').length });
    expect(inA['PT A/Lambat']?.total).toBe(1);
    expect(Object.keys(inA)).not.toContain('PT A/Kosong'); // no PDFs, no mark

    // a folder whose every PDF is read; at the top, each folder counts through its subfolders
    const run = await finished(((await (await post('/api/jobs', { folders: ['PT B'] })).json()) as Job).id);
    await saved(run);
    const andi = await readings(['PT B/Feb 2026 - Apr 2026/Andi - 1feb-30apr26.pdf']);
    const top = await folderMarks('');
    expect(top['PT B']).toEqual({ total: 1, read: 1, check: andi.filter((m) => m.outcome === 'needs_check').length });
    expect(top['PT A']).toEqual({
      total: 4,
      read: inA[PERIOD]!.read + inA['PT A/Lambat']!.read,
      check: inA[PERIOD]!.check + inA['PT A/Lambat']!.check,
    });
    expect(JSON.stringify(top)).not.toContain('.pdf'); // counts only, no file names

    expect((await call('/api/folders/marks?path=..%2F..')).status).toBe(400);
    expect((await call('/api/folders/marks?path=Nope')).status).toBe(404);
    expect((await call('/api/folders/marks', { cookie: null })).status).toBe(401);
  });
});

describe('mock sign-in', () => {
  // fictitious test users
  const ANDI = { email: 'Andi.Uji@GDN-Commerce.com ', name: '  Andi   Uji ' };

  test('every route but sign-in and sign-out needs a session', async () => {
    for (const path of ['/api/health', '/api/folders', '/api/jobs', '/api/jobs/current', '/api/auth/me']) {
      const response = await call(path, { cookie: null });
      expect([path, response.status, await response.json()]).toEqual([path, 401, { error: 'unauthenticated' }]);
    }
    expect((await post('/api/jobs', { folders: ['PT B'] }, {}, null)).status).toBe(401);
    expect((await call('/api/health', { cookie: `${SESSION_COOKIE}=${'x'.repeat(43)}` })).status).toBe(401);
    expect((await call('/api/health', { cookie: `${SESSION_COOKIE}=not-a-token` })).status).toBe(401);
  });

  test('the first sign-in adds an inactive reviewer; later ones keep the row, refresh the name and the last login', async () => {
    const before = await users.count();
    const first = await post('/api/auth/login', ANDI, {}, null);
    expect(first.status).toBe(200);
    const { user } = (await first.json()) as AuthSession;
    expect(user).toMatchObject({ email: 'andi.uji@gdn-commerce.com', name: 'Andi Uji', role: 'reviewer', active: false });
    expect(Date.parse(user.lastLoginAt ?? '')).toBeGreaterThanOrEqual(Date.parse(user.createdAt));
    expect(await users.count()).toBe(before + 1);

    const set = first.headers.get('set-cookie') ?? '';
    expect(set).toContain('HttpOnly');
    expect(set).toContain('SameSite=Strict');
    expect(set).toContain('Path=/');
    expect(set).not.toContain('Secure'); // plain http on 127.0.0.1

    await Bun.sleep(5);
    const again = (await (await post('/api/auth/login', { ...ANDI, name: 'Andi Uji Coba' }, {}, null)).json()) as AuthSession;
    expect(again.user).toEqual({ ...user, name: 'Andi Uji Coba', lastLoginAt: again.user.lastLoginAt });
    expect(Date.parse(again.user.lastLoginAt ?? '')).toBeGreaterThan(Date.parse(user.lastLoginAt ?? ''));
    expect(await users.count()).toBe(before + 1);
  });

  test('a session opens the API, says who is signed in, and ends on sign-out', async () => {
    const session = await activeSession(ANDI);
    expect((await call('/api/health', { cookie: session })).status).toBe(200);
    const me = (await (await call('/api/auth/me', { cookie: session })).json()) as AuthSession;
    expect(me.user.email).toBe('andi.uji@gdn-commerce.com');

    const out = await post('/api/auth/logout', {}, {}, session);
    expect(out.status).toBe(200);
    expect(out.headers.get('set-cookie')).toContain('Max-Age=0');
    expect((await call('/api/health', { cookie: session })).status).toBe(401);
    expect((await call('/api/health')).status).toBe(200); // other sessions stay signed in
  });

  test('signing in again replaces the session it was sent with', async () => {
    const old = await activeSession(ANDI);
    const fresh = sessionPair(await post('/api/auth/login', ANDI, {}, old));
    expect(fresh).not.toBe(old);
    expect((await call('/api/health', { cookie: old })).status).toBe(401);
    expect((await call('/api/health', { cookie: fresh })).status).toBe(200);
  });

  test('accepts any email domain: Apps Gateway, which replaces this, filters accounts itself', async () => {
    for (const email of ['andi.uji@example.com', 'andi.uji@sub.example.co.id']) {
      const response = await post('/api/auth/login', { email, name: 'Andi Uji' }, {}, null);
      expect([email, response.status]).toEqual([email, 200]);
      expect(((await response.json()) as AuthSession).user.email).toBe(email);
    }
  });

  test('refuses bad emails, bad names and bodies, and cross-origin sign-ins', async () => {
    const refused = async (body: unknown) => {
      const response = await post('/api/auth/login', body, {}, null);
      expect(response.headers.get('set-cookie')).toBeNull();
      return [response.status, await response.json()];
    };
    expect(await refused({ email: 'not-an-email', name: 'Andi Uji' })).toEqual([400, { error: 'bad_email' }]);
    expect(await refused({ email: 'andi@localhost', name: 'Andi Uji' })).toEqual([400, { error: 'bad_email' }]);
    expect(await refused({ email: 'andi@gdn-commerce.com', name: ' ' })).toEqual([400, { error: 'bad_name' }]);
    expect(await refused({ email: 'andi@gdn-commerce.com', name: 'Andi\u0000Uji' })).toEqual([400, { error: 'bad_name' }]);
    expect(await refused({ email: 'andi@gdn-commerce.com' })).toEqual([400, { error: 'bad_request' }]);
    expect(await refused({ email: `${'a'.repeat(320)}@gdn-commerce.com`, name: 'Andi' })).toEqual([400, { error: 'bad_request' }]);
    const evil = await post('/api/auth/login', ANDI, { origin: 'https://evil.example' }, null);
    expect(evil.status).toBe(403);
    expect(evil.headers.get('set-cookie')).toBeNull();
  });
});

describe('users and roles', () => {
  // fictitious test users
  const NEW = { email: 'penguji.baru@gdn-commerce.com', name: 'Penguji Baru' };
  const OTHER = { email: 'penguji.lain@gdn-commerce.com', name: 'Penguji Lain' };
  const signIn = async (identity: { email: string; name: string }) => {
    const response = await post('/api/auth/login', identity, {}, null);
    return { session: sessionPair(response), user: ((await response.json()) as AuthSession).user };
  };
  const listed = async (session: string) => ((await (await call('/api/users', { cookie: session })).json()) as UserList).users;
  const answer = async (response: Promise<Response>) => {
    const done = await response;
    return [done.status, await done.json()];
  };

  test('an inactive user signs in but may only ask who it is until an admin activates it', async () => {
    const { session, user } = await signIn(NEW);
    expect(user).toMatchObject({ role: 'reviewer', active: false });
    expect((await call('/api/auth/me', { cookie: session })).status).toBe(200);
    for (const path of ['/api/health', '/api/folders', '/api/jobs', '/api/jobs/current', '/api/users']) {
      expect([path, ...(await answer(call(path, { cookie: session })))]).toEqual([path, 403, { error: 'account_inactive' }]);
    }
    expect(await answer(post('/api/jobs', { folders: ['PT B'] }, {}, session))).toEqual([403, { error: 'account_inactive' }]);

    // the admin activates the user: the session already open works, then stops again once deactivated
    expect((await patch(`/api/users/${user.id}`, { active: true }, adminCookie)).status).toBe(200);
    expect((await call('/api/health', { cookie: session })).status).toBe(200);
    expect((await patch(`/api/users/${user.id}`, { active: false }, adminCookie)).status).toBe(200);
    expect(await answer(call('/api/health', { cookie: session }))).toEqual([403, { error: 'account_inactive' }]);
    const me = (await (await call('/api/auth/me', { cookie: session })).json()) as AuthSession;
    expect(me.user.active).toBe(false);
    expect((await post('/api/auth/logout', {}, {}, session)).status).toBe(200);
  });

  test('an ADMIN_EMAILS user signs in as an active admin', async () => {
    const { user } = await signIn(ADMIN);
    expect(user).toMatchObject({ role: 'admin', active: true });
  });

  test('only an admin lists and changes users; a reviewer gets 403 however the path is written', async () => {
    expect(await answer(call('/api/users'))).toEqual([403, { error: 'admin_only' }]); // TESTER is an active reviewer
    expect((await call('/api/users/')).status).toBe(403);
    expect(await answer(patch('/api/users/1', { active: true }, cookie))).toEqual([403, { error: 'admin_only' }]);
    expect((await call('/api/users', { cookie: null })).status).toBe(401);

    const all = await listed(adminCookie);
    const admin = all.find((u) => u.email === ADMIN.email);
    expect(admin).toMatchObject({ role: 'admin', active: true, adminFromConfig: true });
    expect(all.find((u) => u.email === TESTER.email)).toMatchObject({ role: 'reviewer', active: true, adminFromConfig: false });
    expect(Object.keys(admin!).sort()).toEqual(['active', 'adminFromConfig', 'createdAt', 'email', 'id', 'lastLoginAt', 'name', 'role']);
    const times = all.map((u) => (u.lastLoginAt ? Date.parse(u.lastLoginAt) : 0));
    expect(times).toEqual([...times].sort((a, b) => b - a)); // latest sign-in first
  });

  test('an admin promotes and demotes another user, which opens and closes the User page for them', async () => {
    const other = await activeSession(OTHER);
    const { user } = (await (await call('/api/auth/me', { cookie: other })).json()) as AuthSession;
    const info = spyOn(console, 'info');
    try {
      const promoted = await patch(`/api/users/${user.id}`, { role: 'admin' }, adminCookie);
      expect(promoted.status).toBe(200);
      expect(await promoted.json()).toMatchObject({ role: 'admin', active: true });
      const audit = info.mock.calls.map((args) => String(args[0])).filter((line) => line.startsWith('access:'));
      expect(audit).toEqual([`access: user ${(await listed(adminCookie)).find((u) => u.email === ADMIN.email)!.id} set user ${user.id} to admin, active`]);
    } finally {
      info.mockRestore();
    }
    expect((await call('/api/users', { cookie: other })).status).toBe(200);
    // a promoted admin cannot change an ADMIN_EMAILS admin: the next sign-in would undo it
    const admin = (await listed(other)).find((u) => u.email === ADMIN.email)!;
    expect(await answer(patch(`/api/users/${admin.id}`, { role: 'reviewer' }, other))).toEqual([409, { error: 'admin_from_config' }]);

    const demoted = (await (await patch(`/api/users/${user.id}`, { role: 'reviewer' }, adminCookie)).json()) as ManagedUser;
    expect(demoted).toMatchObject({ role: 'reviewer', active: true, adminFromConfig: false });
    expect(await answer(call('/api/users', { cookie: other }))).toEqual([403, { error: 'admin_only' }]);
  });

  test('an admin cannot change their own role or active state', async () => {
    const other = await activeSession(OTHER);
    const { user } = (await (await call('/api/auth/me', { cookie: other })).json()) as AuthSession;
    await patch(`/api/users/${user.id}`, { role: 'admin' }, adminCookie);
    for (const change of [{ role: 'reviewer' }, { active: false }, { role: 'admin', active: true }]) {
      expect(await answer(patch(`/api/users/${user.id}`, change, other))).toEqual([403, { error: 'cannot_change_self' }]);
    }
    expect((await listed(adminCookie)).find((u) => u.id === user.id)).toMatchObject({ role: 'admin', active: true });
    await patch(`/api/users/${user.id}`, { role: 'reviewer' }, adminCookie);
  });

  test('refuses unknown users, empty or invalid changes, and cross-origin changes', async () => {
    expect(await answer(patch('/api/users/999999', { active: true }, adminCookie))).toEqual([404, { error: 'user_not_found' }]);
    const { user } = await signIn(NEW);
    for (const body of [{}, { role: 'owner' }, { active: 'yes' }]) {
      expect(await answer(patch(`/api/users/${user.id}`, body, adminCookie))).toEqual([400, { error: 'bad_request' }]);
    }
    for (const id of ['abc', '0', '-1', '99999999999']) {
      expect([id, (await patch(`/api/users/${id}`, { active: true }, adminCookie)).status]).toEqual([id, 400]);
    }
    const evil = await patch(`/api/users/${user.id}`, { active: true }, adminCookie, { origin: 'https://evil.example' });
    expect([evil.status, await evil.json()]).toEqual([403, { error: 'forbidden_origin' }]);
    expect((await listed(adminCookie)).find((u) => u.id === user.id)).toMatchObject({ active: false });
  });
});
