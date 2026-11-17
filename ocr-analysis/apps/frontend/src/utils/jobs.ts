import type { FolderMark, Job, JobFile, LatestResult, ReadMark, TokenUsage } from '@contract-ocr/shared-types';
import { formatDateTime, formatDay } from './format.ts';

/** Claude's tokens over a job: summed over every file and every call (text, NIK crop, signature crop); null when
 *  nothing was sent. Results taken from another run count there, where they were read. */
export function claudeTokens(files: JobFile[]): { used: TokenUsage; files: number; calls: number } | null {
  const used: TokenUsage = { input: 0, cacheWrite: 0, cacheRead: 0, output: 0 };
  let count = 0;
  let calls = 0;
  for (const file of files) {
    const row = file.row;
    if (!row || file.readIn || (!row.tokens && !row.claudeCalls?.length)) continue;
    count += 1;
    calls += row.claudeCalls?.length ?? 0;
    if (!row.tokens) continue;
    used.input += row.tokens.input;
    used.cacheWrite += row.tokens.cacheWrite;
    used.cacheRead += row.tokens.cacheRead;
    used.output += row.tokens.output;
  }
  return count ? { used, files: count, calls } : null;
}

/** Queued or running: it can still change, so its page keeps asking for it. */
export const isActive = (job: Pick<Job, 'status'>) => job.status === 'queued' || job.status === 'running';

/** Who read a PDF opened from the folder page, and when: `Penguji A · 6 Nov 2026, 11.02 · Tanpa OCR`. */
export function readBy(run: LatestResult['run']): string {
  const when = isActive(run) ? 'proses masih berjalan' : formatDateTime(run.finishedAt ?? run.startedAt);
  return [run.by ?? 'Tanpa nama', when, ...(run.mode === 'full_page' ? ['Tanpa OCR'] : [])].join(' · ');
}

/** The mark next to a PDF in the folder list that some run has read, with the day; how it came out shows on hover
 *  and in the dialog the PDF opens. */
export const MARK_LABEL = 'Sudah di-OCR';
export const OUTCOME_LABEL: Record<ReadMark['outcome'], string> = { ready: 'Siap dipakai', needs_check: 'Perlu dicek' };
export const markWhen = (mark: ReadMark) =>
  isActive(mark.run) ? 'proses berjalan' : formatDay(mark.run.finishedAt ?? mark.run.startedAt);
export const markTitle = (mark: ReadMark) => `Hasil OCR terakhir: ${OUTCOME_LABEL[mark.outcome]}, ${readBy(mark.run)}`;

/** Whether some run has read every PDF of a folder (subfolders included): the folder then gets the same mark as a PDF. */
export const folderDone = (mark: FolderMark | undefined): mark is FolderMark => !!mark && mark.total > 0 && mark.read === mark.total;
/** `3/9 di-OCR`: a folder some of whose PDFs no run has read yet. */
export const folderPart = (mark: FolderMark) => `${mark.read}/${mark.total} di-OCR`;
/** A folder's mark in full, on hover: how many PDFs are read, and how their newest readings came out. */
export function folderMarkTitle(mark: FolderMark): string {
  const head =
    mark.read === mark.total
      ? `Semua ${mark.total} PDF sudah di-OCR`
      : `${mark.read} dari ${mark.total} PDF sudah di-OCR, ${mark.total - mark.read} belum`;
  const ready = mark.read - mark.check;
  const outcomes = [ready && `${ready} ${OUTCOME_LABEL.ready}`, mark.check && `${mark.check} ${OUTCOME_LABEL.needs_check}`].filter(Boolean);
  return outcomes.length ? `${head}. Hasil OCR terakhir: ${outcomes.join(', ')}` : head;
}

/** `menunggu giliran (2 proses di depan)`, `sedang berjalan`, …: a run's state in a sentence. */
export function runState(job: Pick<Job, 'status' | 'queueAhead'>): string {
  switch (job.status) {
    case 'queued':
      return job.queueAhead ? `menunggu giliran (${job.queueAhead} proses di depan)` : 'menunggu giliran';
    case 'running':
      return 'sedang berjalan';
    case 'done':
      return 'selesai';
    case 'cancelled':
      return 'dibatalkan';
    case 'interrupted':
      return 'terputus';
  }
}

export const parentPath = (path: string) => path.split('/').slice(0, -1).join('/');
export const lastSegment = (path: string, fallback: string) => path.split('/').at(-1) || fallback;
/** The vendor is the first folder under the root (`root/<vendor>/<masa kontrak>/<pdf>`); '' for a PDF at the root. */
export const vendorOf = (path: string) => (path.includes('/') ? path.slice(0, path.indexOf('/')) : '');

/** `Agustus 2036 - Juli 2037` for one folder, `3 folder` for a selection. */
export const jobTitle = (job: Pick<Job, 'folders'>, rootName: string) =>
  job.folders.length === 1 ? lastSegment(job.folders[0]!, rootName) : `${job.folders.length} folder`;

/** Where "back" goes: the folder itself for a single-folder job, else the folder the selection was made in. */
export const jobHome = (job: Pick<Job, 'folders'>) =>
  job.folders.length === 1 ? job.folders[0]! : parentPath(job.folders[0] ?? '');

/** A folder's path as seen from `base` (the job's home): `PT A/Jan` from `PT A` is `Jan`; `base` itself is its own
 *  name. Folders outside `base` keep their full path. */
export function relativeTo(path: string, base: string, rootName: string): string {
  if (path === base) return lastSegment(path, rootName);
  if (!base) return path;
  return path.startsWith(`${base}/`) ? path.slice(base.length + 1) : path;
}

/** Files by folder, folders in the order they first appear in the job. */
export function groupByFolder(files: JobFile[]): Array<{ folder: string; files: JobFile[] }> {
  const groups = new Map<string, JobFile[]>();
  for (const file of files) {
    const group = groups.get(file.folder);
    if (group) group.push(file);
    else groups.set(file.folder, [file]);
  }
  return [...groups].map(([folder, grouped]) => ({ folder, files: grouped }));
}
