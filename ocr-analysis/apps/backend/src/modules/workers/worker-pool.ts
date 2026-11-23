import type { Subprocess } from 'bun';
import type { ExtractorState, StartMode } from '@contract-ocr/shared-types';
import { type ExtractorClient, ExtractorFailure, type ExtractorReply } from './protocol.ts';

export interface PoolOptions {
  cmd: string[];
  size: number;
  timeoutMs: number;
  recycleAfter: number; // files per worker before it is replaced, so slow memory growth never builds up
  env?: Record<string, string>; // added to this process's environment for the workers
}

const MAX_FAILED_STARTS = 3; // workers that die before "ready" this many times in a row: the extractor cannot run
const CLOSE_GRACE_MS = 5000;

type Pending = {
  id: number;
  resolve: (reply: ExtractorReply) => void;
  reject: (failure: ExtractorFailure) => void;
  timer: ReturnType<typeof setTimeout>;
};

/** One long-lived `serve-stdio` process. It answers one request at a time over its stdin/stdout. */
class Worker {
  state: 'starting' | 'idle' | 'busy' | 'dead' = 'starting';
  served = 0;
  claude = false; // its ready message said Claude reads with it
  readonly ready: Promise<void>;
  private readonly proc: Subprocess<'pipe', 'pipe', 'inherit'>;
  private pending: Pending | null = null;
  private settleReady!: { resolve: () => void; reject: (failure: ExtractorFailure) => void };

  constructor(cmd: string[], env: Record<string, string> | undefined, onExit: (worker: Worker) => void) {
    this.ready = new Promise((resolve, reject) => (this.settleReady = { resolve, reject }));
    this.ready.catch(() => {}); // the pool handles it; avoid an unhandled rejection
    this.proc = Bun.spawn(cmd, {
      env: env ? { ...process.env, ...env } : undefined,
      stdin: 'pipe',
      stdout: 'pipe',
      stderr: 'inherit', // extractor logs carry file hashes only, never names or text
      onExit: () => {
        this.exited();
        onExit(this);
      },
    });
    void this.readLines();
  }

  private async readLines(): Promise<void> {
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for await (const chunk of this.proc.stdout) {
        buffer += decoder.decode(chunk, { stream: true });
        for (let at = buffer.indexOf('\n'); at >= 0; at = buffer.indexOf('\n')) {
          const line = buffer.slice(0, at);
          buffer = buffer.slice(at + 1);
          if (line.trim()) this.handle(line);
        }
      }
    } catch {
      // the pipe closes when the process exits; exited() settles anything still waiting
    }
  }

  private handle(line: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(line);
    } catch {
      return this.fail('bad_reply');
    }
    if (this.state === 'starting') {
      if (message.ready === true) {
        this.claude = message.claude === true;
        this.state = 'idle';
        this.settleReady.resolve();
      } else {
        this.settleReady.reject(new ExtractorFailure('extractor_unavailable', String(message.error ?? 'not_ready')));
        this.kill();
      }
      return;
    }
    const pending = this.pending;
    if (!pending || message.id !== pending.id) return this.fail('bad_reply');
    this.pending = null;
    clearTimeout(pending.timer);
    this.served += 1;
    this.state = 'idle';
    if (message.ok === true) {
      pending.resolve({ ok: true, result: message.result, filename: message.filename } as ExtractorReply);
    } else {
      pending.resolve({ ok: false, error: String(message.error ?? 'unknown') });
    }
  }

  send(
    id: number,
    filename: string,
    data: Uint8Array,
    timeoutMs: number,
    vendor: string | null = null,
    mode: StartMode = 'ocr',
  ): Promise<ExtractorReply> {
    this.state = 'busy';
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail('timeout'), timeoutMs);
      this.pending = { id, resolve, reject, timer };
      try {
        const header = { id, filename, size: data.byteLength, ...(vendor ? { vendor } : {}), mode };
        this.proc.stdin.write(`${JSON.stringify(header)}\n`);
        this.proc.stdin.write(data);
        void this.proc.stdin.flush();
      } catch {
        this.fail('worker_crashed');
      }
    });
  }

  /** Give up on the current request and stop the process; the pool starts a replacement. */
  private fail(code: 'timeout' | 'bad_reply' | 'worker_crashed'): void {
    const pending = this.pending;
    this.pending = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new ExtractorFailure(code));
    }
    this.kill();
  }

  kill(): void {
    if (this.state === 'dead') return;
    this.state = 'dead';
    this.proc.kill();
  }

  /** Let the worker finish and exit on its own (stdin closed), then force it if it does not. */
  async close(): Promise<void> {
    if (this.state === 'dead') return;
    this.state = 'dead';
    try {
      this.proc.stdin.end();
    } catch {
      // already closed
    }
    const timer = setTimeout(() => this.proc.kill(), CLOSE_GRACE_MS);
    await this.proc.exited;
    clearTimeout(timer);
  }

  private exited(): void {
    this.settleReady.reject(new ExtractorFailure('extractor_unavailable', 'exited_before_ready'));
    const pending = this.pending;
    this.pending = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new ExtractorFailure('worker_crashed'));
    }
    this.state = 'dead';
  }
}

/** Long-lived extractor workers. Files wait here for an idle worker; hung or crashed workers are replaced. */
export class WorkerPool implements ExtractorClient {
  private readonly workers = new Set<Worker>();
  private readonly idle: Worker[] = [];
  private readonly waiting: Array<{ resolve: (w: Worker) => void; reject: (f: ExtractorFailure) => void }> = [];
  private nextId = 1;
  private state: ExtractorState = 'starting';
  private error: string | null = null;
  private failedStarts = 0;
  private closed = false;
  private withClaude: boolean | null = null;

  constructor(private readonly options: PoolOptions) {}

  start(): void {
    for (let i = 0; i < this.options.size; i++) this.spawn();
  }

  status() {
    const ready = [...this.workers].filter((w) => w.state === 'idle' || w.state === 'busy').length;
    return { state: this.state, error: this.error, workers: ready };
  }

  claude(): boolean | null {
    return this.withClaude;
  }

  private spawn(): void {
    if (this.closed || this.state === 'unavailable') return;
    const worker = new Worker(this.options.cmd, this.options.env, (w) => this.onExit(w));
    this.workers.add(worker);
    worker.ready.then(
      () => {
        this.failedStarts = 0;
        this.state = 'ready';
        this.error = null;
        this.withClaude = worker.claude;
        this.release(worker);
      },
      (failure: ExtractorFailure) => {
        this.failedStarts += 1;
        this.error = failure.detail;
        if (this.failedStarts >= MAX_FAILED_STARTS) {
          this.state = 'unavailable';
          for (const waiter of this.waiting.splice(0)) waiter.reject(new ExtractorFailure('extractor_unavailable'));
        }
      },
    );
  }

  private onExit(worker: Worker): void {
    this.workers.delete(worker);
    const at = this.idle.indexOf(worker);
    if (at >= 0) this.idle.splice(at, 1);
    this.spawn();
  }

  private release(worker: Worker): void {
    if (worker.state !== 'idle') return;
    if (worker.served >= this.options.recycleAfter) {
      void worker.close(); // onExit starts its replacement
      return;
    }
    const waiter = this.waiting.shift();
    if (waiter) waiter.resolve(worker);
    else this.idle.push(worker);
  }

  private acquire(): Promise<Worker> {
    if (this.closed || this.state === 'unavailable') {
      return Promise.reject(new ExtractorFailure('extractor_unavailable'));
    }
    const worker = this.idle.shift();
    if (worker) return Promise.resolve(worker);
    return new Promise((resolve, reject) => this.waiting.push({ resolve, reject }));
  }

  async extract(filename: string, data: Uint8Array, vendor: string | null = null, mode: StartMode = 'ocr'): Promise<ExtractorReply> {
    const worker = await this.acquire();
    const reply = await worker.send(this.nextId++, filename, data, this.options.timeoutMs, vendor, mode);
    this.release(worker);
    return reply;
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const waiter of this.waiting.splice(0)) waiter.reject(new ExtractorFailure('extractor_unavailable'));
    await Promise.all([...this.workers].map((w) => w.close()));
  }
}
