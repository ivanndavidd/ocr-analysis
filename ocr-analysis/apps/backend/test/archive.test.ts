import { afterAll, beforeAll, describe, expect, spyOn, test } from 'bun:test';
import { type ContractRow, type FieldKey, type FieldValue, type HistoryFilter, type Job, type JobFile, type TokenUsage, note } from '@contract-ocr/shared-types';
import { asc, eq } from 'drizzle-orm';
import { type OpenDatabase, openDatabase } from '../src/modules/database/database.ts';
import { ocrContractDetails, ocrHeaders } from '../src/modules/database/schema.ts';
import { DatabaseArchive, detailValues, documentKey, tokenTotals } from '../src/modules/jobs/archive.ts';
import type { RunArchive } from '../src/modules/jobs/archive.ts';
import { UserStore } from '../src/modules/users/store.ts';

// Fictitious people, numbers and folders only.
const NIK = '3199990101900001';
const FOLDER = 'PT Uji/Januari 2026 - Maret 2026';
const field = (value: FieldValue['value'], check: string | null = null): FieldValue => ({
  value,
  check: check === null ? null : note('legacy', { text: check }),
  basis: null,
});

function readFile(name: string, fields: Partial<Record<FieldKey, FieldValue>> = {}, tokens: TokenUsage | null = null): JobFile {
  const all: Record<FieldKey, FieldValue> = {
    nik: field('************0001'),
    vendor: field('PT Uji'),
    entity: field('ENTITAS UJI'),
    name: field(name.split(' - ')[0]!),
    position: field('Staf Gudang'),
    start: field('2026-01-01'),
    end: field('2026-03-31'),
    months: field(3),
    signature: field('YES'),
    ...fields,
  };
  const row = { fields: all, reasons: ['extractor_disabled'], tokens } as unknown as ContractRow;
  return { path: `${FOLDER}/${name}`, folder: FOLDER, name, state: 'done', seconds: 15.7, row };
}

const failedFile = (name: string, error: string): JobFile => ({
  path: `${FOLDER}/${name}`,
  folder: FOLDER,
  name,
  state: 'error',
  error,
  seconds: 0.4,
});
const cancelledFile = (name: string): JobFile => ({ path: `${FOLDER}/${name}`, folder: FOLDER, name, state: 'cancelled' });

function job(files: JobFile[], status: Job['status'] = 'running'): Job {
  return {
    id: crypto.randomUUID(),
    folders: [FOLDER],
    claude: true,
    mode: 'ocr',
    status,
    startedAt: '2026-11-05T03:00:00.000Z',
    finishedAt: status === 'running' ? null : '2026-11-05T03:05:00.000Z',
    files,
    startedBy: null,
  };
}

describe('what a file row holds', () => {
  test('a file with nothing to check is ready, with its values and the full NIK', () => {
    const file = readFile('Budi Uji - 1jan-31mar26.pdf');
    expect(detailValues(file, NIK)).toEqual({
      documentKey: documentKey(file.path),
      filePath: file.path,
      seconds: 15.7,
      status: 'ready',
      vendor: 'PT Uji',
      entity: 'ENTITAS UJI',
      employeeName: 'Budi Uji',
      nik: NIK,
      position: 'Staf Gudang',
      startDate: '2026-01-01',
      endDate: '2026-03-31',
      months: 3,
      signature: 'YES',
      checks: null,
      reasons: '["extractor_disabled"]',
      result: JSON.stringify(file.row),
    });
  });

  test('a value to check makes it needs_check, with one note per column', () => {
    const file = readFile('Rina Uji - 1jan-31mar26.pdf', {
      nik: field(null, 'NIK tidak ditemukan'),
      signature: field('NO', 'Tanda tangan karyawan tidak terlihat'),
    });
    const values = detailValues(file, null);
    expect(values.status).toBe('needs_check');
    expect(JSON.parse(values.checks!)).toEqual({ nik: 'NIK tidak ditemukan', signature: 'Tanda tangan karyawan tidak terlihat' });
    expect(values.nik).toBeNull();
  });

  test('a failed file keeps its cause as the first reason and no values; a cancelled one has neither', () => {
    expect(detailValues(failedFile('rusak.pdf', 'timeout'), NIK)).toEqual({
      documentKey: documentKey(`${FOLDER}/rusak.pdf`),
      filePath: `${FOLDER}/rusak.pdf`,
      seconds: 0.4,
      status: 'failed',
      vendor: 'PT Uji',
      reasons: '["timeout"]',
    });
    expect(detailValues(cancelledFile('nanti.pdf'), null)).toMatchObject({ status: 'cancelled', reasons: null, seconds: null });
  });

  test('values that do not fit their column are cut or dropped, never sent as they are', () => {
    const values = detailValues(
      readFile('Panjang.pdf', { position: field('P'.repeat(150)), start: field('1 Januari 2026'), months: field(2.6) }),
      '12345',
    );
    expect(values.position).toHaveLength(100);
    expect(values.startDate).toBeNull();
    expect(values.months).toBe(3);
    expect(values.nik).toBeNull(); // not 16 digits
  });

  test('the document key is the SHA-256 of the path: the same file in every run, a different one elsewhere', () => {
    expect(documentKey('PT A/Jan/a.pdf')).toMatch(/^[0-9a-f]{64}$/);
    expect(documentKey('PT A/Jan/a.pdf')).toBe(documentKey('PT A/Jan/a.pdf'));
    expect(documentKey('PT B/Jan/a.pdf')).not.toBe(documentKey('PT A/Jan/a.pdf'));
  });

  test('a run adds up the tokens of the files it read', () => {
    const tokens = (n: number): TokenUsage => ({ input: n, cacheWrite: 2 * n, cacheRead: 3 * n, output: 4 * n });
    const taken = { ...readFile('e.pdf', {}, tokens(100)), readIn: { jobId: 'other', by: 'Penguji Lain' } };
    const run = job([readFile('a.pdf', {}, tokens(10)), readFile('b.pdf', {}, tokens(5)), readFile('c.pdf'), failedFile('d.pdf', 'timeout'), taken]);
    // e.pdf's tokens were spent by the run that read it
    expect(tokenTotals(run)).toEqual({ inputTokens: 15, cacheWriteTokens: 30, cacheReadTokens: 45, outputTokens: 60 });
  });
});

describe('saving a run', () => {
  let database: OpenDatabase;
  let userId: number;

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    userId = (await new UserStore(database.db).signIn('penguji.arsip@gdn-commerce.com', 'Penguji Arsip')).id;
  });
  afterAll(() => database.close());

  const header = async (id: number) => (await database.db.select().from(ocrHeaders).where(eq(ocrHeaders.id, id)))[0]!;
  const details = (headerId: number) =>
    database.db.select().from(ocrContractDetails).where(eq(ocrContractDetails.headerId, headerId)).orderBy(asc(ocrContractDetails.filePath));
  const lastHeaderId = async () => (await database.db.select().from(ocrHeaders).orderBy(asc(ocrHeaders.id))).at(-1)!.id;

  test('a header when it starts, a row per file as it is read, the outcome when it stops', async () => {
    const budi = readFile('Budi Uji - 1jan-31mar26.pdf', {}, { input: 100, cacheWrite: 10, cacheRead: 20, output: 30 });
    const rina = readFile('Rina Uji - 1jan-31mar26.pdf', { position: field(null, 'Jabatan tidak ditemukan') });
    const later = cancelledFile('Sari Uji - 1jan-31mar26.pdf');
    const run = job([budi, rina, later]);
    const saving = new DatabaseArchive(database.db).open(run, userId);
    await saving.settled();
    const id = await lastHeaderId();
    expect(await header(id)).toMatchObject({
      jobId: run.id, // the run's id in links and the API
      status: 'running',
      mode: 'ocr',
      claude: true,
      folders: JSON.stringify([FOLDER]),
      totalFiles: 3,
      inputTokens: 0,
      finishedAt: null,
      userId,
    });

    saving.file(budi, NIK, null);
    saving.file(rina, null, null);
    await saving.settled();
    expect((await details(id)).map((d) => [d.employeeName, d.status, d.nik, d.seconds])).toEqual([
      ['Budi Uji', 'ready', NIK, 15.7], // exactly: a float4 column gave 15.699999809
      ['Rina Uji', 'needs_check', null, 15.7],
    ]);

    saving.finish({ ...run, status: 'cancelled', finishedAt: '2026-11-05T03:05:00.000Z' });
    await saving.settled();
    expect(await header(id)).toMatchObject({
      status: 'cancelled',
      finishedAt: new Date('2026-11-05T03:05:00.000Z'),
      inputTokens: 100,
      cacheWriteTokens: 10,
      cacheReadTokens: 20,
      outputTokens: 30,
    });
    const rows = await details(id);
    expect(rows.map((d) => d.status)).toEqual(['ready', 'needs_check', 'cancelled']);
    expect(rows[2]).toMatchObject({ employeeName: null, checks: null, reasons: null, vendor: 'PT Uji' });
    expect(saving.failed).toBe(false);
  });

  test('a retry in the same run updates its row; the next run gets rows of its own', async () => {
    const archive = new DatabaseArchive(database.db);
    const broken = failedFile('Andi Uji - 1jan-31mar26.pdf', 'timeout');
    const run = job([broken]);
    const saving = archive.open(run, userId);
    saving.file(broken, null, null);
    saving.finish({ ...run, status: 'done', finishedAt: '2026-11-05T03:01:00.000Z' });
    await saving.settled();
    const id = await lastHeaderId();
    expect((await details(id)).map((d) => [d.status, d.reasons])).toEqual([['failed', '["timeout"]']]);

    saving.status('queued');
    await saving.settled();
    expect(await header(id)).toMatchObject({ status: 'queued', finishedAt: null });
    const fixed = readFile('Andi Uji - 1jan-31mar26.pdf', { name: field('Andi Uji') });
    saving.file(fixed, null, null);
    saving.finish({ ...run, files: [fixed], status: 'done', finishedAt: '2026-11-05T03:02:00.000Z' });
    await saving.settled();
    const rows = await details(id);
    expect(rows.map((d) => [d.status, d.employeeName, d.reasons])).toEqual([['ready', 'Andi Uji', '["extractor_disabled"]']]);
    expect((await header(id)).status).toBe('done');

    const again = archive.open(job([fixed]), userId);
    again.file(fixed, null, null);
    await again.settled();
    const next = await lastHeaderId();
    expect(next).not.toBe(id);
    const sameDocument = await database.db
      .select()
      .from(ocrContractDetails)
      .where(eq(ocrContractDetails.documentKey, documentKey(fixed.path)));
    expect(sameDocument.map((d) => d.headerId).sort()).toEqual([id, next].sort());
  });

  test('a write that fails marks the run, logs its code and never its values, and stops nothing', async () => {
    const errors = spyOn(console, 'error').mockImplementation(() => {});
    try {
      const file = readFile('Budi Uji - 1jan-31mar26.pdf');
      const saving = new DatabaseArchive(database.db).open(job([file]), 999_999); // no such user: the header is refused
      saving.file(file, NIK, null);
      saving.finish(job([file], 'done'));
      await saving.settled();
      expect(saving.failed).toBe(true);
      const logged = errors.mock.calls.map((call) => call.join(' ')).join('\n');
      expect(logged).toContain('23503'); // foreign key violation
      expect(logged).not.toContain(NIK);
      expect(logged).not.toContain('Budi');
      expect(logged).not.toContain(FOLDER);
    } finally {
      errors.mockRestore();
    }
  });
});

describe('reading saved runs back', () => {
  let database: OpenDatabase;
  let archive: DatabaseArchive;
  let userId: number;
  let otherId: number;

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    archive = new DatabaseArchive(database.db);
    userId = (await new UserStore(database.db).signIn('penguji.riwayat@gdn-commerce.com', 'Penguji Riwayat')).id;
    otherId = (await new UserStore(database.db).signIn('penguji.lain@gdn-commerce.com', 'Penguji Lain')).id;
  });
  afterAll(() => database.close());

  async function saveRun(run: Job, user: number, write: (saving: RunArchive) => void): Promise<RunArchive> {
    const saving = archive.open(run, user);
    write(saving);
    await saving.settled();
    return saving;
  }

  test('a run reads back as the results page showed it, with whose run read the PDFs it took', async () => {
    const budi = readFile('Budi Uji - 1jan-31mar26.pdf', { position: field(null, 'Jabatan tidak ditemukan') });
    const first = job([budi, failedFile('rusak.pdf', 'timeout')]);
    const reader = await saveRun(first, otherId, (saving) => {
      saving.file(budi, NIK, null);
      saving.file(first.files[1]!, null, null);
      saving.finish({ ...first, status: 'done', finishedAt: '2026-11-05T03:05:00.000Z' });
    });

    const taken = { ...budi, readIn: { jobId: first.id, by: 'Penguji Lain' } };
    const second = job([taken, cancelledFile('nanti.pdf')]);
    await saveRun(second, userId, (saving) => {
      saving.file(taken, NIK, reader);
      saving.finish({ ...second, status: 'cancelled', finishedAt: '2026-11-05T03:06:00.000Z' });
    });
    const firstHeader = await reader.headerId();
    const reused = await database.db.select().from(ocrContractDetails).where(eq(ocrContractDetails.reusedFromHeaderId, firstHeader!));
    expect(reused.map((d) => d.filePath)).toEqual([budi.path]);

    const stored = (await archive.load(second.id))!;
    expect(stored.job).toEqual({
      ...second,
      status: 'cancelled',
      finishedAt: '2026-11-05T03:06:00.000Z',
      startedBy: { id: userId, name: 'Penguji Riwayat', email: 'penguji.riwayat@gdn-commerce.com' },
      files: [
        { ...taken, readIn: { jobId: first.id, by: 'Penguji Lain' } }, // by path: Budi before nanti
        { ...cancelledFile('nanti.pdf') },
      ],
    });
    expect([...stored.niks]).toEqual([[budi.path, NIK]]);
    expect((await archive.load(first.id))!.job.files[1]).toEqual({ ...failedFile('rusak.pdf', 'timeout') });
    expect(await archive.load(crypto.randomUUID())).toBeNull();
    expect(await archive.load('not-a-run-id')).toBeNull(); // never reaches the database as a uuid
  });

  test("every user's runs, newest first, with who started them and how their files came out", async () => {
    const page = await archive.list(0, 2);
    expect(page.more).toBe(false);
    expect(page.jobs.map((j) => [j.startedBy?.name, j.status, j.total, j.ready, j.check, j.failed])).toEqual([
      ['Penguji Riwayat', 'cancelled', 2, 0, 1, 0],
      ['Penguji Lain', 'done', 2, 0, 1, 1],
    ]);
    expect((await archive.list(0, 1)).more).toBe(true);
    expect((await archive.list(1, 1)).jobs[0]!.startedBy?.name).toBe('Penguji Lain');
  });

  test('a run a stopped backend left queued or running is interrupted, keeps what it read, and can go on', async () => {
    const read = readFile('Sari Uji - 1jan-31mar26.pdf', {}, { input: 7, cacheWrite: 5, cacheRead: 3, output: 2 });
    const taken = { ...readFile('Tono Uji - 1jan-31mar26.pdf', {}, { input: 900, cacheWrite: 0, cacheRead: 0, output: 900 }), readIn: { jobId: 'x', by: null } };
    const run = job([read, taken, readFile('Wati Uji - 1jan-31mar26.pdf'), readFile('Yudi Uji - 1jan-31mar26.pdf')]);
    const source = archive.open(job([]), otherId);
    const saving = await saveRun(run, userId, (s) => {
      s.file(read, null, null);
      s.file(taken, null, source);
    }); // then the backend stops
    expect(await archive.interruptUnfinished()).toBe(2); // and the empty run opened above with it
    expect(await archive.interruptUnfinished()).toBe(0);
    // the tokens of what it read itself, not of the result it took
    expect((await database.db.select().from(ocrHeaders).where(eq(ocrHeaders.jobId, run.id)))[0]).toMatchObject({
      inputTokens: 7,
      cacheWriteTokens: 5,
      cacheReadTokens: 3,
      outputTokens: 2,
    });
    const stored = (await archive.load(run.id))!;
    expect(stored.job).toMatchObject({ status: 'interrupted', missing: 2 });
    expect(stored.job.files.map((f) => f.name)).toEqual([read.name, taken.name]);

    const resumed = archive.resume(run.id, stored.headerId);
    resumed.status('queued');
    await resumed.settled();
    expect((await archive.load(run.id))!.job.status).toBe('interrupted'); // not in this backend's memory: it stopped
    expect((await database.db.select().from(ocrHeaders).where(eq(ocrHeaders.id, stored.headerId)))[0]!.status).toBe('queued');
    expect(await saving.headerId()).toBe(stored.headerId);
  });

  test('a row saved before the whole reading was kept opens with its values and notes', async () => {
    const old = readFile('Lama Uji - 1jan-31mar26.pdf', { signature: field('NO', 'Tanda tangan karyawan tidak terlihat') });
    const run = job([old]);
    const saving = await saveRun(run, userId, (s) => {
      s.file(old, NIK, null);
      s.finish({ ...run, status: 'done', finishedAt: '2026-11-05T03:05:00.000Z' });
    });
    await database.db.update(ocrContractDetails).set({ result: null }).where(eq(ocrContractDetails.headerId, (await saving.headerId())!));
    const row = (await archive.load(run.id))!.job.files[0]!.row!;
    expect(row.fields.name).toEqual({ value: 'Lama Uji', check: null, basis: null });
    // its note was saved as text only: shown as written, in either language
    expect(row.fields.signature).toEqual({ value: 'NO', check: note('legacy', { text: 'Tanda tangan karyawan tidak terlihat' }), basis: null });
    expect(row.fields.nik.value).toBe('************0001');
    expect(row.nik.masked).toBe('************0001');
  });

  test('a reading saved with its notes as text (before 2026-11-16) opens with notes the page can render', async () => {
    const old = readFile('Teks Uji - 1jan-31mar26.pdf');
    const run = job([old]);
    const saving = await saveRun(run, userId, (s) => {
      s.file(old, NIK, null);
      s.finish({ ...run, status: 'done', finishedAt: '2026-11-05T03:05:00.000Z' });
    });
    const asText = { ...old.row!, fields: { ...old.row!.fields, signature: { value: 'NO', check: 'Kurang jelas; lihat sekilas', basis: null } } };
    await database.db
      .update(ocrContractDetails)
      .set({ result: JSON.stringify(asText) })
      .where(eq(ocrContractDetails.headerId, (await saving.headerId())!));
    const row = (await archive.load(run.id))!.job.files[0]!.row!;
    // this fixture holds too few facts for today's rules, so the text is kept as written rather than guessed
    expect(row.fields.signature).toEqual({ value: 'NO', check: note('legacy', { text: 'Kurang jelas; lihat sekilas' }), basis: null });
    expect(row.fields.name).toEqual({ value: 'Teks Uji', check: null, basis: null });
  });

  test("a PDF's newest reading in any run: never a taken copy, a failure or a cancelled file", async () => {
    const name = 'Budi Uji - 1jan-31mar26.pdf';
    const path = `${FOLDER}/${name}`;
    // the first test's runs: Penguji Lain's run read it, then Penguji Riwayat's newer run took that result
    const before = (await archive.latest(path))!;
    expect(before.run).toMatchObject({ by: 'Penguji Lain', mode: 'ocr', status: 'done' });
    expect(before.file).toMatchObject({ path, name, folder: FOLDER, state: 'done' });
    expect(before.file.readIn).toBeUndefined();
    expect(before.nik).toBe(NIK);

    const again = readFile(name, { position: field('Kepala Gudang') });
    const reread = job([again]);
    await saveRun(reread, userId, (s) => {
      s.file(again, null, null);
      s.finish({ ...reread, status: 'done', finishedAt: '2026-11-06T03:05:00.000Z' });
    });
    const failing = job([failedFile(name, 'timeout'), cancelledFile('Sari Uji - 1jan-31mar26.pdf')]);
    await saveRun(failing, otherId, (s) => {
      s.file(failing.files[0]!, null, null);
      s.finish({ ...failing, status: 'cancelled', finishedAt: '2026-11-06T03:06:00.000Z' });
    });

    const after = (await archive.latest(path))!;
    expect(after.run).toMatchObject({ jobId: reread.id, by: 'Penguji Riwayat', finishedAt: '2026-11-06T03:05:00.000Z' });
    expect(after.file.row!.fields.position.value).toBe('Kepala Gudang');
    expect(after.nik).toBeNull();
    // the cancelled copy in the newest run is no reading either: the interrupted run's own reading stays the latest
    expect((await archive.latest(`${FOLDER}/Sari Uji - 1jan-31mar26.pdf`))!.run.by).toBe('Penguji Riwayat');
    expect(await archive.latest(`${FOLDER}/rusak.pdf`)).toBeNull(); // only ever failed
    expect(await archive.latest(`${FOLDER}/tidak ada.pdf`)).toBeNull();

    // the folder list's marks: the same newest reading per PDF, in brief
    const sari = `${FOLDER}/Sari Uji - 1jan-31mar26.pdf`;
    const lama = `${FOLDER}/Lama Uji - 1jan-31mar26.pdf`; // its signature is to check
    const marks = await archive.marks([path, sari, lama, `${FOLDER}/rusak.pdf`, `${FOLDER}/tidak ada.pdf`]);
    expect(Object.keys(marks).sort()).toEqual([path, lama, sari]); // Budi, Lama, Sari
    expect(marks[path]).toEqual({ outcome: 'ready', run: after.run });
    expect(marks[lama]).toMatchObject({ outcome: 'needs_check', run: { by: 'Penguji Riwayat' } });
    expect(marks[sari]!.run).toEqual((await archive.latest(sari))!.run);
    expect(await archive.marks([])).toEqual({});
  });
});

describe('filtering the shared history', () => {
  let database: OpenDatabase;
  let archive: DatabaseArchive;
  let saring: number;
  const ids = new Map<string, string>(); // run id → the test's name for it
  const check = (name: string) => readFile(name, { position: field(null, 'Jabatan tidak ditemukan') });

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    archive = new DatabaseArchive(database.db);
    const store = new UserStore(database.db);
    saring = (await store.signIn('penguji.saring@gdn-commerce.com', 'Penguji Saring')).id;
    const lain = (await store.signIn('penguji.lain@gdn-commerce.com', 'Penguji Lain')).id;
    const runs: [string, number, Partial<Job>, JobFile[], Job['status']][] = [
      ['januari', saring, { folders: ['PT Uji/Januari 2026 - Maret 2026'], startedAt: '2026-11-01T03:00:00.000Z' }, [readFile('Ani Uji - a.pdf')], 'done'],
      [
        'persen',
        lain,
        { folders: ['PT 50%_Uji/April 2026 - Juni 2026'], mode: 'full_page', startedAt: '2026-11-03T03:00:00.000Z' },
        [check('Budi Uji - b.pdf'), failedFile('rusak.pdf', 'timeout')],
        'done',
      ],
      [
        'april',
        saring,
        { folders: ['PT Uji/April 2026 - Juni 2026'], mode: 'claude_page', startedAt: '2026-11-05T03:00:00.000Z' },
        [check('Cici Uji - c.pdf')],
        'cancelled',
      ],
      ['berjalan', lain, { folders: ['PT Lain/Juli 2026 - September 2026'], startedAt: '2026-11-06T03:00:00.000Z' }, [], 'running'],
    ];
    for (const [name, user, over, files, status] of runs) {
      const run = { ...job(files, status), ...over };
      const saving = archive.open(run, user);
      for (const file of files) saving.file(file, null, null);
      if (status !== 'running') saving.finish(run);
      await saving.settled();
      ids.set(run.id, name);
    }
  });
  afterAll(() => database.close());

  async function listed(filter: HistoryFilter, userId: number | null = saring, offset = 0, limit = 10) {
    const page = await archive.list(offset, limit, filter, userId);
    return { runs: page.jobs.map((j) => ids.get(j.id)), total: page.total, more: page.more };
  }

  test('pages on the server: the page asked for, and how many match in all', async () => {
    expect(await listed({})).toEqual({ runs: ['berjalan', 'april', 'persen', 'januari'], total: 4, more: false });
    expect(await listed({}, saring, 1, 2)).toEqual({ runs: ['april', 'persen'], total: 4, more: true });
    expect(await listed({}, saring, 4, 2)).toEqual({ runs: [], total: 4, more: false });
    const persen = (await archive.list(0, 10, { q: '50%' })).jobs[0]!;
    expect(persen).toMatchObject({ mode: 'full_page', total: 2, ready: 0, check: 1, failed: 1, startedBy: { name: 'Penguji Lain' } });
  });

  test("searches a chosen folder's path and the starter's name or email, in any case, wildcards taken literally", async () => {
    expect((await listed({ q: 'pt uji/APRIL' })).runs).toEqual(['april']);
    expect((await listed({ q: '  penguji lain ' })).runs).toEqual(['berjalan', 'persen']);
    expect((await listed({ q: 'SARING@gdn' })).runs).toEqual(['april', 'januari']);
    expect((await listed({ q: '%' })).runs).toEqual(['persen']);
    expect((await listed({ q: '_' })).runs).toEqual(['persen']);
    expect(await listed({ q: 'tidak ada' })).toEqual({ runs: [], total: 0, more: false });
  });

  test("filters by state, by outcome, by how it read, by who started it, and by when it started", async () => {
    expect((await listed({ status: 'active' })).runs).toEqual(['berjalan']);
    expect((await listed({ status: 'done' })).runs).toEqual(['persen', 'januari']);
    expect((await listed({ status: 'cancelled' })).runs).toEqual(['april']);
    expect((await listed({ outcome: 'check' })).runs).toEqual(['april', 'persen']);
    expect((await listed({ outcome: 'failed' })).runs).toEqual(['persen']);
    expect((await listed({ mode: 'full_page' })).runs).toEqual(['persen']); // the old no-OCR trial still lists
    expect((await listed({ mode: 'claude_page' })).runs).toEqual(['april']);
    expect((await listed({ mode: 'ocr' })).runs).toEqual(['berjalan', 'januari']);
    expect((await listed({ mine: true })).runs).toEqual(['april', 'januari']);
    expect(await listed({ mine: true }, null)).toEqual({ runs: [], total: 0, more: false });
    expect((await listed({ from: '2026-11-03T00:00:00.000Z', to: '2026-11-06T00:00:00.000Z' })).runs).toEqual(['april', 'persen']);
    expect((await listed({ from: '2026-11-06T03:00:00.000Z' })).runs).toEqual(['berjalan']); // from is inclusive
    expect((await listed({ to: '2026-11-01T03:00:00.000Z' })).runs).toEqual([]); // to is not
    expect(await listed({ q: 'uji', status: 'done', outcome: 'check' })).toEqual({ runs: ['persen'], total: 1, more: false });
  });
});
