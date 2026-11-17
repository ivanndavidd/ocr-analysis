import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Job, RunUser } from '@contract-ocr/shared-types';
import { LocalFolderSource } from '../src/modules/folders/source.ts';
import { JobService } from '../src/modules/jobs/service.ts';
import type { ExtractorClient, ExtractorReply } from '../src/modules/workers/protocol.ts';

// Fictitious users and files. Every read waits until the test releases it, so the order of events is the test's.
const A: RunUser = { id: 1, name: 'Penguji A', email: 'penguji.a@gdn-commerce.com' };
const B: RunUser = { id: 2, name: 'Penguji B', email: 'penguji.b@gdn-commerce.com' };
const C: RunUser = { id: 3, name: 'Penguji C', email: 'penguji.c@gdn-commerce.com' };
const X = { folders: ['V/X'], files: [] }; // x1.pdf, x2.pdf
const Y = { folders: ['V/Y'], files: [] }; // y1.pdf
const Z = { folders: ['V/Z'], files: [] }; // z1.pdf

let root = '';
beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'ocr-queue-'));
  for (const [folder, names] of [
    ['V/X', ['x1.pdf', 'x2.pdf']],
    ['V/Y', ['y1.pdf']],
    ['V/Z', ['z1.pdf']],
  ] as const) {
    mkdirSync(join(root, folder), { recursive: true });
    for (const name of names) writeFileSync(join(root, folder, name), '%PDF-1.4 fake');
  }
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

const read = (file: string): ExtractorReply => ({
  ok: true,
  filename: { name: 'Uji', start: '2026-01-01', end: '2026-03-31', duration_months: null, flags: [] },
  result: {
    file,
    nik: null,
    status_ttd: 'signed',
    keputusan: 'review',
    flags: [],
    catatan_vendor: null,
    log: { p_id: [1], p_ttd: 2, jenis_halaman: {}, ocr_conf: {}, periode_regex: null, tanda_tangan: null, alasan: [] },
  },
} as unknown as ExtractorReply);

function setup() {
  const calls: string[] = []; // every PDF the workers were asked to read, in order
  const fails = new Set<string>();
  const opened = new Map<string, () => void>();
  const gates = new Map<string, Promise<void>>();
  const gate = (name: string) => {
    if (!gates.has(name)) gates.set(name, new Promise<void>((resolve) => opened.set(name, resolve)));
    return gates.get(name)!;
  };
  const extractor: ExtractorClient = {
    status: () => ({ state: 'ready', error: null, workers: 1 }),
    claude: () => true,
    close: async () => {},
    extract: async (filename) => {
      calls.push(filename);
      await gate(filename);
      gates.delete(filename); // reading it again waits again
      return fails.has(filename) ? { ok: false, error: 'too_large' } : read(filename);
    },
  };
  const release = (...names: string[]) => {
    for (const name of names) {
      void gate(name);
      opened.get(name)!();
    }
  };
  const service = new JobService(new LocalFolderSource(root), extractor, 1024 * 1024, 1);
  const status = async (job: Job) => (await service.get(job.id)).status;
  return { service, calls, fails, release, status };
}

async function until(check: () => boolean | Promise<boolean>): Promise<void> {
  for (let i = 0; i < 300; i++) {
    if (await check()) return;
    await Bun.sleep(5);
  }
  throw new Error('timed out');
}

const names = (job: Job, pick: (f: Job['files'][number]) => unknown) => job.files.map((f) => [f.name, pick(f)]);

describe('the run queue', () => {
  test('runs wait their turn, first come first served, and each user has one run at a time', async () => {
    const { service, calls, release, status } = setup();
    const a = await service.start(Z, A);
    const b = await service.start(Y, B);
    expect(a.status).toBe('running');
    expect(b).toMatchObject({ status: 'queued', queueAhead: 1, startedBy: B });
    await expect(service.start(Y, A)).rejects.toMatchObject({ code: 'user_has_job', jobId: a.id });
    expect(service.current(B.id)).toMatchObject({ job: { id: b.id }, active: 2 });
    expect(service.current(C.id)).toEqual({ job: null, active: 2 });

    await until(() => calls.length === 1);
    release('z1.pdf');
    await until(async () => (await status(b)) === 'running');
    expect(await status(a)).toBe('done');
    release('y1.pdf');
    await until(async () => (await status(b)) === 'done');
    expect(calls).toEqual(['z1.pdf', 'y1.pdf']);
    expect((await service.history(0, 5)).jobs.map((j) => [j.startedBy?.name, j.status])).toEqual([
      ['Penguji B', 'done'],
      ['Penguji A', 'done'],
    ]);
    // without a database the runs in memory page and filter the same way
    expect(await service.history(1, 1)).toMatchObject({ jobs: [{ id: a.id }], more: false, total: 2 });
    expect(await service.history(0, 5, { mine: true }, A.id)).toMatchObject({ jobs: [{ id: a.id }], total: 1 });
    expect(await service.history(0, 5, { q: 'V/y' })).toMatchObject({ jobs: [{ id: b.id }], total: 1 });
    expect(await service.history(0, 5, { q: 'PENGUJI A' })).toMatchObject({ jobs: [{ id: a.id }], total: 1 });
    expect((await service.history(0, 5, { status: 'active' })).total).toBe(0);
    const both = (await service.history(0, 5)).jobs;
    expect((await service.history(0, 5, { status: 'done', mode: 'ocr', outcome: 'check' })).total).toBe(both.filter((j) => j.check > 0).length);
    expect((await service.history(0, 5, { outcome: 'failed' })).total).toBe(both.filter((j) => j.failed > 0).length);
    expect((await service.history(0, 5, { from: new Date(Date.now() + 60_000).toISOString() })).total).toBe(0);
    expect((await service.history(0, 5, { to: new Date(Date.now() + 60_000).toISOString() })).total).toBe(2);
  });

  test('a PDF the run ahead has is read once: the run behind takes its result and reads only the rest', async () => {
    const { service, calls, release, status } = setup();
    const a = await service.start(X, A);
    const b = await service.start({ folders: ['V/X', 'V/Y'], files: [] }, B);
    expect(names(b, (f) => f.waitingFor)).toEqual([
      ['x1.pdf', { jobId: a.id, by: 'Penguji A' }],
      ['x2.pdf', { jobId: a.id, by: 'Penguji A' }],
      ['y1.pdf', undefined],
    ]);
    release('x1.pdf');
    await until(async () => (await service.get(b.id)).files[0]!.state === 'done'); // as soon as the run ahead reads it
    release('x2.pdf', 'y1.pdf');
    await until(async () => (await status(b)) === 'done');
    const done = await service.get(b.id);
    expect(names(done, (f) => f.readIn?.jobId ?? null)).toEqual([
      ['x1.pdf', a.id],
      ['x2.pdf', a.id],
      ['y1.pdf', null],
    ]);
    expect(done.files[0]!.row).toEqual((await service.get(a.id)).files[0]!.row!);
    expect(calls).toEqual(['x1.pdf', 'x2.pdf', 'y1.pdf']);
  });

  test('without a database, a PDF opened from the folder page shows its newest reading among the runs in memory', async () => {
    const { service, fails, release, status } = setup();
    fails.add('y1.pdf');
    const a = await service.start({ folders: ['V/X', 'V/Y'], files: [] }, A);
    const b = await service.start(X, B); // takes both of a's X results
    release('x1.pdf', 'x2.pdf', 'y1.pdf');
    await until(async () => (await status(a)) === 'done' && (await status(b)) === 'done');

    const latest = (await service.latest('V/X/x1.pdf'))!;
    expect(latest.run).toMatchObject({ jobId: a.id, by: 'Penguji A', mode: 'ocr', status: 'done' }); // who read it
    expect(latest.file).toMatchObject({ path: 'V/X/x1.pdf', state: 'done' });
    expect(latest.file.readIn).toBeUndefined();
    expect(latest.nik).toBeNull();
    expect(await service.latest('V/Y/y1.pdf')).toBeNull(); // failed: no reading
    expect(await service.latest('V/Z/z1.pdf')).toBeNull(); // never run
    await expect(service.latest('V/../x1.pdf')).rejects.toMatchObject({ code: 'bad_path' });

    const toCheck = Object.values(latest.file.row!.fields).some((field) => field.check !== null);
    expect(await service.marks(['V/X/x1.pdf', 'V/Y/y1.pdf', 'V/Z/z1.pdf', 'V/X/x1.pdf'])).toEqual({
      'V/X/x1.pdf': { outcome: toCheck ? 'needs_check' : 'ready', run: latest.run },
    });
    await expect(service.marks(['V/X/x1.pdf', '../x1.pdf'])).rejects.toMatchObject({ code: 'bad_path' });

    // the folder page's download: the newest readings, and a run of the unread PDFs alone
    const download = await service.latestForExport({ folders: ['V/X', 'V/Y'], files: [] });
    expect(download.unread).toBe(1);
    expect(names(download.job, (f) => f.state)).toEqual([['x1.pdf', 'done'], ['x2.pdf', 'done'], ['y1.pdf', 'waiting']]);
    await expect(service.start({ ...X, unreadOnly: true }, C)).rejects.toMatchObject({ code: 'nothing_to_read' });
    const c = await service.start({ folders: ['V/X', 'V/Y'], files: [], unreadOnly: true }, C);
    expect(c.files.map((f) => f.name)).toEqual(['y1.pdf']); // the only PDF no run has read
    release('y1.pdf');
    await until(async () => (await status(c)) === 'done');
  });

  test('a PDF the run ahead fails is read again by the run behind', async () => {
    const { service, calls, fails, release, status } = setup();
    fails.add('x1.pdf');
    const a = await service.start(X, A);
    const b = await service.start(X, B);
    release('x1.pdf', 'x2.pdf');
    await until(async () => (await status(a)) === 'done');
    fails.delete('x1.pdf');
    await until(() => calls.length === 3);
    release('x1.pdf');
    await until(async () => (await status(b)) === 'done');
    expect(calls).toEqual(['x1.pdf', 'x2.pdf', 'x1.pdf']);
    expect(names(await service.get(b.id), (f) => [f.state, f.readIn?.jobId ?? null])).toEqual([
      ['x1.pdf', ['done', null]],
      ['x2.pdf', ['done', a.id]],
    ]);
  });

  test('only the starter cancels a run; PDFs a cancelled run never read are read by the run behind', async () => {
    const { service, calls, release, status } = setup();
    const a = await service.start(X, A);
    const b = await service.start(X, B);
    await expect(service.cancel(a.id, B.id)).rejects.toMatchObject({ code: 'not_your_job' });
    await until(() => calls.length === 1); // x1 is being read
    await service.cancel(a.id, A.id);
    expect((await service.get(b.id)).files[1]!.waitingFor).toBeUndefined(); // x2 will never be read there
    release('x1.pdf');
    await until(async () => (await status(b)) === 'running');
    expect(await status(a)).toBe('cancelled');
    release('x2.pdf');
    await until(async () => (await status(b)) === 'done');
    expect(calls).toEqual(['x1.pdf', 'x2.pdf']);
    expect(names(await service.get(b.id), (f) => f.readIn?.jobId ?? null)).toEqual([
      ['x1.pdf', a.id],
      ['x2.pdf', null],
    ]);
  });

  test('a queued run with nothing of its own to read finishes when its PDFs are read, without waiting its turn', async () => {
    const { service, release, status } = setup();
    const a = await service.start(X, A);
    const c = await service.start(Z, C);
    const b = await service.start(X, B);
    expect(b.queueAhead).toBe(2);
    release('x1.pdf', 'x2.pdf');
    await until(async () => (await status(b)) === 'done');
    expect(await status(c)).toBe('running'); // z1 is still held
    release('z1.pdf');
    await until(async () => (await status(c)) === 'done');
  });

  test('a queued run cancelled at once: the runs waiting for it look elsewhere or read the PDFs themselves', async () => {
    const { service, calls, release, status } = setup();
    const a = await service.start(Z, A);
    const b = await service.start(X, B);
    const c = await service.start(X, C);
    expect(names(c, (f) => f.waitingFor?.jobId)).toEqual([
      ['x1.pdf', b.id],
      ['x2.pdf', b.id],
    ]);
    expect((await service.cancel(b.id, B.id)).status).toBe('cancelled');
    expect(names(await service.get(c.id), (f) => f.waitingFor)).toEqual([
      ['x1.pdf', undefined],
      ['x2.pdf', undefined],
    ]);
    release('z1.pdf', 'x1.pdf', 'x2.pdf');
    await until(async () => (await status(c)) === 'done');
    expect(await status(a)).toBe('done');
    expect(calls).toEqual(['z1.pdf', 'x1.pdf', 'x2.pdf']);
  });

  test('a retried run takes the result of a run ahead that is reading the same PDF', async () => {
    const { service, calls, fails, release, status } = setup();
    fails.add('x1.pdf');
    const a = await service.start(X, A);
    release('x1.pdf', 'x2.pdf');
    await until(async () => (await status(a)) === 'done');
    fails.delete('x1.pdf');
    const b = await service.start(X, B); // a has finished: b reads both itself
    await until(() => calls.length === 3);
    const retried = await service.retryFailed(a.id, A.id);
    expect(retried).toMatchObject({ status: 'queued', queueAhead: 1 });
    expect(retried.files[0]!.waitingFor).toEqual({ jobId: b.id, by: 'Penguji B' });
    await expect(service.retryFailed(a.id, A.id)).rejects.toMatchObject({ code: 'job_running' });
    release('x1.pdf', 'x2.pdf');
    await until(async () => (await status(a)) === 'done' && (await status(b)) === 'done');
    expect(calls).toEqual(['x1.pdf', 'x2.pdf', 'x1.pdf', 'x2.pdf']);
    expect(names(await service.get(a.id), (f) => [f.state, f.readIn?.jobId ?? null])).toEqual([
      ['x1.pdf', ['done', b.id]],
      ['x2.pdf', ['done', null]],
    ]);
  });

  test('runs that read differently never share results', async () => {
    const { service, calls, release, status } = setup();
    const a = await service.start(X, A);
    const b = await service.start({ ...X, mode: 'full_page' }, B);
    expect(b.files.every((f) => !f.waitingFor && !f.readIn)).toBe(true);
    release('x1.pdf', 'x2.pdf');
    await until(async () => (await status(a)) === 'done' && calls.length === 3);
    release('x1.pdf');
    await until(() => calls.length === 4); // one worker: the next PDF is asked for once the last is read
    release('x2.pdf');
    await until(async () => (await status(b)) === 'done');
    expect(calls).toEqual(['x1.pdf', 'x2.pdf', 'x1.pdf', 'x2.pdf']);
  });
});
