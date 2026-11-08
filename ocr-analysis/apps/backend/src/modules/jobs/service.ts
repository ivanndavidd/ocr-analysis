import type {
  CurrentRun,
  FolderMark,
  HistoryFilter,
  Job,
  JobFile,
  JobHistory,
  JobSummary,
  LatestResult,
  ReadMark,
  ReadMode,
  RunRef,
  RunUser,
} from '@contract-ocr/shared-types';
import { type ExtractorClient, ExtractorFailure } from '../workers/protocol.ts';
import type { ResultArchive, RunArchive } from './archive.ts';
import { matchesHistory } from './history.ts';
import { type FolderSource, SourceError, segments } from '../folders/source.ts';
import { vendorOf } from './export.ts';
import { FULL_PAGES } from './fields.ts';
import { fullNik, toRow } from './rows.ts';

const KEEP_JOBS = 20; // finished runs kept in memory; older ones are read back from the database when opened
const MAX_JOB_FILES = 2000;
const MARKS_AT_ONCE = 1000; // PDFs looked up in one database query

/** A request the jobs service refuses; `code` goes to the client as is. */
export class JobError extends Error {
  constructor(
    readonly code:
      | 'job_running' // this run is still queued or running
      | 'user_has_job' // the user already has a run queued or running (one at a time per user)
      | 'not_your_job' // only whoever started a run may cancel or retry it
      | 'no_pdfs'
      | 'too_many_files'
      | 'extractor_unavailable'
      | 'job_not_found'
      | 'file_not_found'
      | 'not_retryable'
      | 'job_not_finished'
      | 'nothing_to_read' // a run of the unread PDFs only, when every PDF of the selection has been read
      | 'claude_unavailable',
    readonly jobId?: string,
  ) {
    super(code);
  }
}

interface JobRecord extends Job {
  cancelRequested: boolean;
  niks: Map<string, string>; // full NIK by file path; never in a job view or a log (the database row holds it)
  archive: RunArchive | null; // where the run is saved; null without a database or a signed-in user
  byPath: Map<string, JobFile>;
  waits: Map<string, JobRecord>; // files of this run another run ahead of it is reading: path → that run
  followers: Map<string, Set<JobRecord>>; // files of this run that runs behind it wait for: path → those runs
}

function jobFile(path: string): JobFile {
  const at = path.lastIndexOf('/');
  return { path, folder: at < 0 ? '' : path.slice(0, at), name: path.slice(at + 1), state: 'waiting' };
}

/** What the user ticked: folders (with their subfolders) and single PDFs, as root-relative paths, and how to read them. */
export interface Selection {
  folders: string[];
  files: string[];
  mode?: ReadMode; // 'ocr' when left out
  unreadOnly?: boolean; // only the PDFs no run has read yet (the folder page's download reads those first)
}

const parent = (path: string) => path.split('/').slice(0, -1).join('/');
/** In the no-OCR mode Claude is the only reader: a file its call failed for (or a worker without Claude read) has
 *  nothing read, so it fails and can be retried like a worker failure. */
const unreadByClaude = (reasons: string[]) =>
  reasons.includes(FULL_PAGES) && reasons.some((r) => r === 'extractor_disabled' || r.startsWith('extractor_error'));
const retryable = (file: JobFile) => file.state === 'error' || file.state === 'cancelled';
const isActive = (job: Job) => job.status === 'queued' || job.status === 'running';
const ref = (job: JobRecord): RunRef => ({ jobId: job.id, by: job.startedBy?.name ?? null });
const runOf = (job: JobRecord): LatestResult['run'] => {
  const { mode, status, startedAt, finishedAt } = job;
  return { ...ref(job), mode, status, startedAt, finishedAt };
};
const needsCheck = (file: JobFile) => Object.values(file.row?.fields ?? {}).some((field) => field.check !== null);

function summary(job: JobRecord): JobSummary {
  let ready = 0;
  let check = 0;
  let failed = 0;
  for (const file of job.files) {
    if (file.state === 'error') failed += 1;
    else if (file.state === 'done' && file.row) {
      if (needsCheck(file)) check += 1;
      else ready += 1;
    }
  }
  const { id, folders, claude, mode, status, startedAt, finishedAt, startedBy } = job;
  const total = job.files.length + (job.missing ?? 0);
  return { id, folders, claude, mode, status, startedAt, finishedAt, startedBy, total, ready, check, failed };
}

/**
 * Runs "Jalankan OCR" for many users: one run reads at a time with every worker (`lanes` files in parallel), the others
 * wait in a first-come-first-served queue, and each user has at most one run queued or running. A PDF is read once:
 * a run that has a PDF which a run ahead of it (queued or running when it started) also has takes that run's result,
 * and reads the PDF itself only if that run fails it or is cancelled first.
 */
export class JobService {
  private readonly jobs = new Map<string, JobRecord>(); // every queued or running run, and the latest finished ones
  private readonly loading = new Map<string, Promise<JobRecord | null>>();
  private active: JobRecord | null = null;
  private readonly queue: JobRecord[] = [];

  constructor(
    private readonly source: FolderSource,
    private readonly extractor: ExtractorClient,
    private readonly maxFileBytes: number,
    private readonly lanes: number,
    private readonly results: ResultArchive | null = null,
  ) {}

  /** Queues a run for `user`, the signed-in user who asked for it (its OCRHeader row names them). */
  async start({ folders, files, mode = 'ocr', unreadOnly = false }: Selection, user: RunUser | null = null): Promise<Job> {
    this.ensureFree(user);
    this.client();
    // The no-OCR mode is Claude's reading alone: without Claude it would read nothing.
    if (mode === 'full_page' && this.extractor.claude() !== true) throw new JobError('claude_unavailable');
    const chosen = await this.resolve({ folders, files });
    let paths = chosen.paths;
    if (unreadOnly) {
      const read = await this.readingsOf(paths, false);
      paths = paths.filter((path) => !read.has(path));
      if (paths.length === 0) throw new JobError('nothing_to_read');
    }
    this.ensureFree(user); // listing awaited: the same user may have started a run meanwhile

    const job = this.newRecord({
      id: crypto.randomUUID(),
      folders: chosen.folders,
      claude: this.extractor.claude() === true,
      mode,
      status: 'queued',
      startedAt: new Date().toISOString(),
      finishedAt: null,
      files: paths.map(jobFile),
      startedBy: user,
    });
    if (this.results && user) job.archive = this.results.open(this.view(job), user.id);
    this.jobs.set(job.id, job);
    this.enqueue(job, job.files);
    this.trim();
    return this.view(job);
  }

  async get(id: string): Promise<Job> {
    return this.view(await this.record(id));
  }

  /** The user's own run while it is queued or running, and how many runs are queued or running in all. */
  current(userId: number | null): CurrentRun {
    const order = this.order();
    const mine = userId === null ? undefined : order.find((job) => job.startedBy?.id === userId);
    return { job: mine ? this.view(mine) : null, active: order.length };
  }

  /** A page of every user's runs that match the filters, newest first, and how many match: from the database when
   *  there is one, else the runs still in memory. `userId` is who the `mine` filter means. */
  async history(offset: number, limit: number, filter: HistoryFilter = {}, userId: number | null = null): Promise<JobHistory> {
    if (!this.results) {
      const all = [...this.jobs.values()].reverse().map(summary).filter((run) => matchesHistory(run, filter, userId));
      return { jobs: all.slice(offset, offset + limit), more: all.length > offset + limit, total: all.length };
    }
    const page = await this.results.list(offset, limit, filter, userId);
    // a run in memory is fresher than its row, which trails it by a write or two
    return { ...page, jobs: page.jobs.map((saved) => this.jobs.get(saved.id) ?? saved).map((j) => ('files' in j ? summary(j) : j)) };
  }

  /** Cancels a queued run at once, or a running one after the files being read; only its starter may. */
  async cancel(id: string, userId: number | null): Promise<Job> {
    const job = await this.record(id);
    this.ensureOwner(job, userId);
    if (job.status === 'queued') {
      this.dequeue(job);
      this.cancelWaiting(job);
      this.finish(job, 'cancelled');
    } else if (job.status === 'running') {
      job.cancelRequested = true;
      this.cancelWaiting(job);
    }
    return this.view(job);
  }

  /** A run that has stopped, with its full NIKs, for the Excel export; a queued or running one would give a
   *  half-filled workbook. The NIKs never travel in a job view. */
  async forExport(id: string): Promise<{ job: Job; niks: ReadonlyMap<string, string> }> {
    const job = await this.record(id);
    if (isActive(job)) throw new JobError('job_not_finished');
    return { job: this.view(job), niks: new Map(job.niks) };
  }

  /** Every full NIK of a run by file path, for the results page; rows only carry the last four digits. */
  async niks(id: string): Promise<Record<string, string>> {
    return Object.fromEntries((await this.record(id)).niks);
  }

  /** The newest reading of one PDF (by its root-relative path) in any user's run, with its full NIK: from the database
   *  when there is one, else from the runs in memory. Null when no run has read it. */
  async latest(path: string): Promise<LatestResult | null> {
    const key = segments(path).join('/');
    return (await this.readingsOf([key], false)).get(key) ?? null;
  }

  /** The folder page's download: every PDF of the selection with its newest reading in any user's run, as a run the
   *  Excel export takes. A PDF no run has read is a row with nothing read (its newest failure, if it failed); `unread`
   *  counts those. */
  async latestForExport(selection: Selection): Promise<{ job: Job; niks: Map<string, string>; unread: number }> {
    const { paths, folders } = await this.resolve(selection);
    const found = await this.readingsOf(paths, true);
    const niks = new Map<string, string>();
    const files = paths.map((path) => {
      const saved = found.get(path);
      if (saved?.nik) niks.set(path, saved.nik);
      return saved?.file ?? jobFile(path);
    });
    const now = new Date().toISOString();
    const job: Job = { id: 'latest', folders, claude: false, mode: 'ocr', status: 'done', startedAt: now, finishedAt: now, files, startedBy: null };
    return { job, niks, unread: files.filter((file) => file.state !== 'done').length };
  }

  /** The selection's PDFs (root-relative, each once) and the folders that title a run of them: the chosen folders, then
   *  those holding chosen single PDFs. */
  private async resolve({ folders, files }: Pick<Selection, 'folders' | 'files'>): Promise<{ paths: string[]; folders: string[] }> {
    const chosen = await this.source.pdfFiles(files);
    const paths = [...new Set([...(await this.source.pdfPaths(folders, MAX_JOB_FILES)), ...chosen])];
    if (paths.length === 0) throw new JobError('no_pdfs');
    if (paths.length > MAX_JOB_FILES) throw new JobError('too_many_files');
    return { paths, folders: [...new Set([...folders.map((f) => segments(f).join('/')), ...chosen.map(parent)])] };
  }

  /** The newest reading of each PDF in any user's run, from the database when there is one, else from the runs in
   *  memory; with `failures`, a PDF never read gets its newest failure (the database keeps those). */
  private async readingsOf(paths: string[], failures: boolean): Promise<Map<string, LatestResult>> {
    if (this.results) return this.results.readings(paths, failures);
    const found = new Map<string, LatestResult>();
    for (const path of paths) {
      const read = this.readInMemory(path);
      if (read) found.set(path, { file: structuredClone(read.file), nik: read.job.niks.get(path) ?? null, run: runOf(read.job) });
    }
    return found;
  }

  /** How each of these PDFs (root-relative paths, e.g. those of one folder) came out in its newest reading in any
   *  user's run, for the folder list; PDFs no run has read are left out. */
  async marks(paths: string[]): Promise<Record<string, ReadMark>> {
    const keys = [...new Set(paths.map((path) => segments(path).join('/')))];
    if (this.results) return this.results.marks(keys);
    const marks: Record<string, ReadMark> = {};
    for (const key of keys) {
      const found = this.readInMemory(key);
      if (found) marks[key] = { outcome: needsCheck(found.file) ? 'needs_check' : 'ready', run: runOf(found.job) };
    }
    return marks;
  }

  /** How far the runs have read each subfolder of a folder, for the marks in the folder list: of the PDFs the
   *  listing counts, how many some run has read (as `marks` finds them) and how many of those need a check. A
   *  subfolder with no PDFs, or more than a listing counts, gets no mark. */
  async folderMarks(path: string): Promise<Record<string, FolderMark>> {
    const folders = (await this.source.subfolderPdfs(path)).filter((folder) => folder.complete && folder.pdfs.length > 0);
    const paths = folders.flatMap((folder) => folder.pdfs);
    const read: Record<string, ReadMark> = {};
    for (let at = 0; at < paths.length; at += MARKS_AT_ONCE) Object.assign(read, await this.marks(paths.slice(at, at + MARKS_AT_ONCE)));
    const marks: Record<string, FolderMark> = {};
    for (const folder of folders) {
      const found = folder.pdfs.map((pdf) => read[pdf]).filter((mark) => mark !== undefined);
      marks[folder.path] = { total: folder.pdfs.length, read: found.length, check: found.filter((mark) => mark.outcome === 'needs_check').length };
    }
    return marks;
  }

  /** The newest run in memory that read this PDF itself (not one that took the result from another run). */
  private readInMemory(path: string): { job: JobRecord; file: JobFile } | null {
    for (const job of [...this.jobs.values()].reverse()) {
      const file = job.byPath.get(path);
      if (file?.state === 'done' && file.row && !file.readIn) return { job, file };
    }
    return null;
  }

  /** Queues one failed or cancelled file (by its root-relative path) again; only the run's starter may. */
  async retry(id: string, path: string, userId: number | null): Promise<Job> {
    const job = await this.ready(id, userId);
    const file = job.byPath.get(path);
    if (!file) throw new JobError('file_not_found');
    if (!retryable(file)) throw new JobError('not_retryable');
    return this.rerun(job, [file]);
  }

  /** Queues every failed or cancelled file of a run again, e.g. after the connection dropped. */
  async retryFailed(id: string, userId: number | null): Promise<Job> {
    const job = await this.ready(id, userId);
    const files = job.files.filter(retryable);
    if (files.length === 0) throw new JobError('not_retryable');
    return this.rerun(job, files);
  }

  /** A run its starter may queue again: stopped, and the starter has no other run queued or running. */
  private async ready(id: string, userId: number | null): Promise<JobRecord> {
    const job = await this.record(id);
    this.ensureOwner(job, userId);
    if (isActive(job)) throw new JobError('job_running', job.id);
    this.ensureFree(job.startedBy);
    return job;
  }

  private rerun(job: JobRecord, files: JobFile[]): Job {
    this.client();
    if (job.mode === 'full_page' && this.extractor.claude() !== true) throw new JobError('claude_unavailable');
    for (const file of files) {
      file.state = 'waiting';
      delete file.error;
      delete file.seconds;
    }
    job.archive?.status('queued');
    this.enqueue(job, files);
    return this.view(job);
  }

  /** Puts a run at the end of the queue, gives `files` the results of runs ahead that have them, and starts it when
   *  no run is reading. A run left with nothing to read itself is finished at once. */
  private enqueue(job: JobRecord, files: JobFile[]): void {
    job.status = 'queued';
    job.finishedAt = null;
    this.queue.push(job);
    this.share(job, files);
    this.complete(job);
    this.pump();
  }

  /** Each of `files` takes the result of a run ahead of `job` that has the same PDF (and reads the same way), or waits
   *  for that run to read it; a PDF no run ahead has, or only had fail, is read by `job` itself. */
  private share(job: JobRecord, files: JobFile[]): void {
    const ahead = this.ahead(job).filter((other) => other.mode === job.mode);
    for (const file of files) {
      for (const other of ahead) {
        const theirs = other.byPath.get(file.path);
        if (theirs?.state === 'done' && theirs.row) {
          this.take(job, file, other, theirs);
          break;
        }
        if (theirs?.state === 'waiting' || theirs?.state === 'reading') {
          job.waits.set(file.path, other);
          const waiting = other.followers.get(file.path) ?? new Set<JobRecord>();
          waiting.add(job);
          other.followers.set(file.path, waiting);
          break;
        }
      }
    }
  }

  /** `file` of `job` gets the result `theirs` of the same PDF in `other`, and passes it on to runs waiting for it. */
  private take(job: JobRecord, file: JobFile, other: JobRecord, theirs: JobFile): void {
    file.state = 'done';
    file.row = structuredClone(theirs.row);
    if (theirs.seconds !== undefined) file.seconds = theirs.seconds;
    delete file.error;
    file.readIn = theirs.readIn ?? ref(other);
    const nik = other.niks.get(file.path) ?? null;
    if (nik) job.niks.set(file.path, nik);
    else job.niks.delete(file.path);
    job.archive?.file(file, nik, this.jobs.get(file.readIn.jobId)?.archive ?? null);
    this.settle(job, file);
  }

  /** `file` of `job` has its outcome: runs waiting for it take it if it was read, and otherwise look for it in another
   *  run ahead of them or read the PDF themselves when their turn comes. */
  private settle(job: JobRecord, file: JobFile): void {
    const followers = job.followers.get(file.path);
    if (!followers) return;
    job.followers.delete(file.path);
    for (const follower of followers) {
      follower.waits.delete(file.path);
      const mine = follower.byPath.get(file.path);
      if (mine?.state !== 'waiting') continue; // cancelled meanwhile
      if (file.state === 'done' && file.row) this.take(follower, mine, job, file);
      else this.share(follower, [mine]);
      this.complete(follower);
    }
  }

  /** A queued run whose every file has its outcome from other runs has nothing left to read: it is done. */
  private complete(job: JobRecord): void {
    if (job.status !== 'queued' || job.waits.size > 0 || job.files.some((f) => f.state === 'waiting')) return;
    this.dequeue(job);
    this.finish(job, 'done');
  }

  private dequeue(job: JobRecord): void {
    const at = this.queue.indexOf(job);
    if (at >= 0) this.queue.splice(at, 1);
  }

  private cancelWaiting(job: JobRecord): void {
    for (const [path, other] of job.waits) other.followers.get(path)?.delete(job);
    job.waits.clear();
    for (const file of job.files) {
      if (file.state !== 'waiting') continue;
      file.state = 'cancelled';
      this.settle(job, file);
    }
  }

  private finish(job: JobRecord, status: 'done' | 'cancelled'): void {
    job.status = status;
    job.cancelRequested = false;
    job.finishedAt = new Date().toISOString();
    job.archive?.finish(this.view(job));
    const failed = job.files.filter((f) => f.state === 'error').length;
    console.info(`job ${job.id}: ${job.status}, ${failed} file(s) failed`);
    this.trim();
  }

  /** Starts the run at the head of the queue when no run is reading. */
  private pump(): void {
    if (this.active) return;
    const next = this.queue.shift();
    if (!next) return;
    this.active = next;
    void this.run(next);
  }

  private async run(job: JobRecord): Promise<void> {
    job.status = 'running';
    job.archive?.status('running');
    // every run ahead has finished, so nothing should still wait for one; if something does, it is read here
    for (const [path, other] of job.waits) other.followers.get(path)?.delete(job);
    job.waits.clear();
    console.info(`job ${job.id}: ${job.files.filter((f) => f.state === 'waiting').length} file(s) to read`);
    const next = () => (job.cancelRequested ? undefined : job.files.find((f) => f.state === 'waiting'));
    const lane = async () => {
      for (let file = next(); file; file = next()) {
        file.state = 'reading'; // claimed synchronously, so two lanes never take the same file
        await this.process(job, file);
      }
    };
    await Promise.all(Array.from({ length: this.lanes }, lane));
    this.active = null;
    this.finish(job, job.cancelRequested ? 'cancelled' : 'done');
    this.pump();
  }

  private async process(job: JobRecord, file: JobFile): Promise<void> {
    const started = performance.now();
    try {
      const data = await this.source.readPdf(file.path, this.maxFileBytes);
      const vendor = vendorOf(file.path) || null;
      const reply = await this.client().extract(file.name, data, vendor, job.mode);
      if (reply.ok && job.mode === 'full_page' && unreadByClaude(reply.result.log.alasan)) {
        file.state = 'error';
        file.error = 'claude_failed';
        delete file.row;
        job.niks.delete(file.path);
      } else if (reply.ok) {
        const nik = fullNik(reply.result);
        if (nik) job.niks.set(file.path, nik);
        else job.niks.delete(file.path);
        file.row = toRow(reply.result, reply.filename, vendor);
        file.state = 'done';
      } else {
        file.state = 'error';
        file.error = reply.error;
      }
    } catch (error) {
      file.state = 'error';
      file.error =
        error instanceof ExtractorFailure || error instanceof SourceError ? error.code : 'internal_error';
    } finally {
      file.seconds = Math.round((performance.now() - started) / 100) / 10;
      delete file.readIn;
      job.archive?.file(file, job.niks.get(file.path) ?? null, null);
      this.settle(job, file);
    }
  }

  /** The extractor, refused when its workers cannot start. */
  private client(): ExtractorClient {
    if (this.extractor.status().state === 'unavailable') throw new JobError('extractor_unavailable');
    return this.extractor;
  }

  /** Every queued or running run, the one reading first. */
  private order(): JobRecord[] {
    return this.active ? [this.active, ...this.queue] : [...this.queue];
  }

  /** The runs ahead of `job` (all of them when it is not in the queue yet). */
  private ahead(job: JobRecord): JobRecord[] {
    const order = this.order();
    const at = order.indexOf(job);
    return at < 0 ? order : order.slice(0, at);
  }

  private ensureFree(user: RunUser | null): void {
    const mine = user && this.order().find((job) => job.startedBy?.id === user.id);
    if (mine) throw new JobError('user_has_job', mine.id);
  }

  private ensureOwner(job: JobRecord, userId: number | null): void {
    if (job.startedBy && job.startedBy.id !== userId) throw new JobError('not_your_job');
  }

  private newRecord(job: Job): JobRecord {
    return {
      ...job,
      cancelRequested: false,
      niks: new Map(),
      archive: null,
      byPath: new Map(job.files.map((file) => [file.path, file])),
      waits: new Map(),
      followers: new Map(),
    };
  }

  /** A run from memory, or read back from the database (a run that finished long ago, or before a restart). */
  private async record(id: string): Promise<JobRecord> {
    const job = this.jobs.get(id) ?? (await this.loadSaved(id));
    if (!job) throw new JobError('job_not_found');
    return job;
  }

  private loadSaved(id: string): Promise<JobRecord | null> {
    const results = this.results;
    if (!results) return Promise.resolve(null);
    let pending = this.loading.get(id);
    if (!pending) {
      pending = results
        .load(id)
        .then((stored) => {
          if (!stored) return null;
          const known = this.jobs.get(id);
          if (known) return known;
          const job = this.newRecord(stored.job);
          for (const [path, nik] of stored.niks) job.niks.set(path, nik);
          job.archive = results.resume(id, stored.headerId);
          this.jobs.set(id, job);
          this.trim();
          return job;
        })
        .finally(() => this.loading.delete(id));
      this.loading.set(id, pending);
    }
    return pending;
  }

  /** Drops the oldest finished runs beyond KEEP_JOBS from memory; queued and running runs always stay. */
  private trim(): void {
    let finished = [...this.jobs.values()].filter((job) => !isActive(job)).length;
    for (const [id, job] of this.jobs) {
      if (finished <= KEEP_JOBS) break;
      if (isActive(job)) continue;
      this.jobs.delete(id);
      finished -= 1;
    }
  }

  private view(job: JobRecord): Job {
    const { cancelRequested: _, niks: __, archive, byPath: ___, waits, followers: ____, ...rest } = job;
    const out: Job = structuredClone(rest);
    for (const file of out.files) {
      const other = waits.get(file.path);
      if (other) file.waitingFor = ref(other);
    }
    if (job.status === 'queued') out.queueAhead = this.ahead(job).length;
    if (archive?.failed) out.saveFailed = true;
    return out;
  }
}
