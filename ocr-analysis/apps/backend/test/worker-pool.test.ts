import { afterEach, describe, expect, test } from 'bun:test';
import { join } from 'node:path';
import { claudeState } from '../src/modules/workers/claude.ts';
import { ExtractorFailure } from '../src/modules/workers/protocol.ts';
import { type PoolOptions, WorkerPool } from '../src/modules/workers/worker-pool.ts';

const FAKE = [process.execPath, join(import.meta.dir, 'fixtures', 'fake-worker.ts')];
const PDF = new TextEncoder().encode('%PDF-1.4 fake');
const pools: WorkerPool[] = [];

function pool(options: Partial<PoolOptions> = {}) {
  const p = new WorkerPool({ cmd: FAKE, size: 2, timeoutMs: 10_000, recycleAfter: 100, ...options });
  pools.push(p);
  p.start();
  return p;
}

async function until(check: () => boolean, ms = 5000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error('condition not reached');
    await Bun.sleep(20);
  }
}

async function failure(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ExtractorFailure) return error.code;
    throw error;
  }
  throw new Error('expected a failure');
}

const pid = (reply: Awaited<ReturnType<WorkerPool['extract']>>) => (reply.ok ? reply.result.catatan_vendor : null);

afterEach(async () => {
  await Promise.all(pools.splice(0).map((p) => p.close()));
});

describe('WorkerPool', () => {
  test('sends the read mode in the header, the OCR mode unless told otherwise', async () => {
    const p = pool({ size: 1 });
    const mode = (reply: Awaited<ReturnType<WorkerPool['extract']>>) =>
      reply.ok ? reply.result.log.alasan.find((r) => r.startsWith('mode:')) : 'failed';
    expect(mode(await p.extract('a.pdf', PDF))).toBe('mode:ocr');
    expect(mode(await p.extract('b.pdf', PDF, null, 'claude_page'))).toBe('mode:claude_page');
  });

  test('sends the vendor folder in the header only when there is one', async () => {
    const p = pool({ size: 1 });
    const check = (reply: Awaited<ReturnType<WorkerPool['extract']>>) => (reply.ok ? reply.result.log.validasi_vendor : 'failed');
    expect(check(await p.extract('a.pdf', PDF, 'PT Contoh Sejahtera'))).toBe('cocok');
    expect(check(await p.extract('b.pdf', PDF))).toBeNull();
  });

  test('answers files on long-lived workers', async () => {
    const p = pool();
    await until(() => p.status().workers === 2); // the second process may still be starting
    const replies = await Promise.all(['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf'].map((n) => p.extract(n, PDF)));
    expect(replies.every((r) => r.ok)).toBe(true);
    expect(new Set(replies.map(pid)).size).toBeLessThanOrEqual(2);
    expect(p.status()).toEqual({ state: 'ready', error: null, workers: 2 });
  });

  test('passes an ok:false answer through without replacing the worker', async () => {
    const p = pool({ size: 1 });
    const first = await p.extract('a.pdf', PDF);
    expect(await p.extract('reject.pdf', PDF)).toEqual({ ok: false, error: 'too_large' });
    expect(pid(await p.extract('b.pdf', PDF))).toBe(pid(first));
  });

  test.each([
    ['slow.pdf', 'timeout'],
    ['crash.pdf', 'worker_crashed'],
    ['garbage.pdf', 'bad_reply'],
  ])('%s fails with %s and a fresh worker takes the next file', async (name, code) => {
    const p = pool({ size: 1, timeoutMs: 500 });
    const before = pid(await p.extract('a.pdf', PDF));
    expect(await failure(p.extract(name, PDF))).toBe(code);
    const after = await p.extract('b.pdf', PDF);
    expect(after.ok).toBe(true);
    expect(pid(after)).not.toBe(before);
  });

  test('recycles a worker after recycleAfter files', async () => {
    const p = pool({ size: 1, recycleAfter: 2 });
    const pids = [];
    for (const name of ['a.pdf', 'b.pdf', 'c.pdf']) pids.push(pid(await p.extract(name, PDF)));
    expect(pids[0]).toBe(pids[1]);
    expect(pids[2]).not.toBe(pids[0]);
  });

  test('becomes unavailable when workers cannot start', async () => {
    const p = pool({ env: { FAKE_MODE: 'not_ready' } });
    await until(() => p.status().state === 'unavailable');
    expect(p.status().error).toBe('ocr_unavailable');
    expect(await failure(p.extract('a.pdf', PDF))).toBe('extractor_unavailable');
    expect(claudeState(true, p)).toBe('unavailable');
  });

  test("reports whether Claude reads with the workers, as their ready message says", async () => {
    const withClaude = pool({ size: 1, env: { FAKE_CLAUDE: '1' } });
    expect(withClaude.claude()).toBeNull(); // not started yet
    expect(claudeState(true, withClaude)).toBe('starting');
    await until(() => withClaude.status().state === 'ready');
    expect([withClaude.claude(), claudeState(true, withClaude), claudeState(false, withClaude)]).toEqual([true, 'on', 'off']);

    const without = pool({ size: 1 }); // `--claude` given, but Claude could not be reached: the patterns read alone
    await until(() => without.status().state === 'ready');
    expect([without.claude(), claudeState(true, without)]).toEqual([false, 'unavailable']);
  });
});
