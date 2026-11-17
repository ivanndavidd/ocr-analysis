import type { WorkbookSpec } from './export.ts';

/** Turns a workbook spec into .xlsx bytes. */
export type WorkbookWriter = (spec: WorkbookSpec) => Promise<Uint8Array>;

/** The writer failed: it timed out, exited with an error, or wrote more than an export can be. */
export class WorkbookError extends Error {
  constructor(readonly reason: 'timeout' | 'failed' | 'too_large') {
    super(reason);
  }
}

const MAX_XLSX_BYTES = 200 * 1024 * 1024;

/** Runs `<python> -m contract_extractor xlsx` once per export: the spec goes in on stdin, the workbook comes back on
 *  stdout. openpyxl is already vetted in the extractor, so the backend needs no spreadsheet dependency of its own. */
export function processWorkbookWriter(cmd: string[], timeoutMs: number): WorkbookWriter {
  return async (spec) => {
    const proc = Bun.spawn(cmd, { stdin: 'pipe', stdout: 'pipe', stderr: 'pipe' });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill();
    }, timeoutMs);
    try {
      proc.stdin.write(JSON.stringify(spec));
      await proc.stdin.end();
      const [bytes, stderr, code] = await Promise.all([
        new Response(proc.stdout).arrayBuffer(),
        new Response(proc.stderr).text(),
        proc.exited,
      ]);
      if (timedOut) throw new WorkbookError('timeout');
      if (code !== 0) {
        // The writer's own message names a field and a rule, never a cell's value; still, cut it short.
        console.error(`export: workbook writer exited ${code}: ${stderr.trim().split('\n').pop()?.slice(0, 200) ?? ''}`);
        throw new WorkbookError('failed');
      }
      if (bytes.byteLength > MAX_XLSX_BYTES) throw new WorkbookError('too_large');
      return new Uint8Array(bytes);
    } finally {
      clearTimeout(timer);
    }
  };
}
