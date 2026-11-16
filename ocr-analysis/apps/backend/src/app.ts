import { Elysia, t } from 'elysia';
import type {
  ApiError,
  AuthSession,
  CurrentRun,
  FolderMark,
  Health,
  Job,
  JobHistory,
  LatestResult,
  ManagedUser,
  ReadMark,
  RunUser,
  User,
  UserAccessErrorCode,
  UserList,
} from '@contract-ocr/shared-types';
import { type SessionStore, loginIdentity, sessionCookie, sessionToken } from './modules/auth/sessions.ts';
import type { UserRow } from './modules/database/schema.ts';
import { AccessError, type UserStore } from './modules/users/store.ts';
import { claudeState } from './modules/workers/claude.ts';
import type { ExtractorClient } from './modules/workers/protocol.ts';
import { GraphError } from './modules/folders/graph.ts';
import { type FolderSource, SourceError } from './modules/folders/source.ts';
import { exportFileName, workbookSpec } from './modules/jobs/export.ts';
import { JobError, type JobService } from './modules/jobs/service.ts';
import { WorkbookError, type WorkbookWriter } from './modules/jobs/workbook.ts';

export interface AppDeps {
  allowedHosts: Set<string>;
  source: FolderSource;
  jobs: JobService;
  extractor: ExtractorClient;
  claude: boolean; // configured; the workers report whether they can reach it
  workbook: WorkbookWriter;
  users: UserStore;
  sessions: SessionStore;
}

// Everything else under /api needs a signed-in session, and everything but these also an active user: an inactive one
// may only ask who it is (the page then says it waits for an admin) and sign out.
const PUBLIC_PATHS = new Set(['/api/auth/login', '/api/auth/logout']);
const INACTIVE_PATHS = new Set(['/api/auth/me']);

const publicUser = (row: UserRow): User => ({
  id: row.id,
  email: row.email,
  name: row.name,
  role: row.role,
  active: row.active,
  createdAt: row.createdAt.toISOString(),
  lastLoginAt: row.lastLoginAt?.toISOString() ?? null,
});

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const PATH = t.String({ maxLength: 1000 });
const MAX_SELECTED_FOLDERS = 200;
const MAX_SELECTED_FILES = 2000; // a job's file limit
const MAX_EXPORTS = 2; // each export starts a Python process
const HISTORY_PAGE = 20; // runs per page of the shared history
const INSTANT = t.String({ pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,3})?Z$' }); // an ISO date-time in UTC
const MAX_MARKED_FILES = 2000; // PDFs marked in one folder listing, as many as one run may read
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

const SOURCE_STATUS: Record<SourceError['code'], number> = { bad_path: 400, not_found: 404, not_pdf: 400, too_large: 413 };
const ACCESS_STATUS: Record<UserAccessErrorCode, number> = {
  admin_only: 403,
  cannot_change_self: 403,
  admin_from_config: 409,
  user_not_found: 404,
};
const USER_ID = t.Numeric({ minimum: 1, maximum: 2_147_483_647 }); // a PostgreSQL integer id
const JOB_STATUS: Record<JobError['code'], number> = {
  job_running: 409,
  user_has_job: 409,
  not_your_job: 403,
  no_pdfs: 400,
  too_many_files: 400,
  extractor_unavailable: 503,
  job_not_found: 404,
  file_not_found: 404,
  not_retryable: 409,
  job_not_finished: 409,
  nothing_to_read: 409,
  claude_unavailable: 503,
};

function fail(status: number, body: ApiError): Response {
  return Response.json(body, { status, headers: { 'cache-control': 'no-store' } });
}

/**
 * The API. It only answers requests addressed to an allowed host (blocks DNS-rebinding pages from reading local
 * contracts) and refuses state changes from other origins (blocks cross-site form posts).
 */
export function createApp({
  allowedHosts,
  source,
  jobs,
  extractor,
  claude,
  workbook,
  users,
  sessions,
}: AppDeps) {
  let exporting = 0;

  /** A run's rows as the "data output" workbook download, at most MAX_EXPORTS at a time. */
  async function sendWorkbook(job: Job, niks: ReadonlyMap<string, string>): Promise<Response> {
    if (exporting >= MAX_EXPORTS) return fail(429, { error: 'export_busy' });
    exporting++;
    try {
      const bytes = await workbook(workbookSpec(job, niks));
      return new Response(bytes, {
        headers: {
          'content-type': XLSX,
          // an ASCII fallback, then the real name (vendor names may hold any character)
          'content-disposition': `attachment; filename="Hasil OCR PKWT.xlsx"; filename*=UTF-8''${encodeURIComponent(exportFileName(job))}`,
          'cache-control': 'no-store',
          'x-content-type-options': 'nosniff',
        },
      });
    } finally {
      exporting--;
    }
  }
  // The guard below looks up the signed-in user once per request, in the database, so a role change or deactivation
  // applies to sessions already open; the routes read that user here.
  const signedIn = new WeakMap<Request, UserRow>();
  const userId = (request: Request) => signedIn.get(request)?.id ?? null;
  const managedUser = (row: UserRow): ManagedUser => ({ ...publicUser(row), adminFromConfig: users.adminFromConfig(row.email) });
  /** The signed-in user if an admin, else the 403 to answer. Checked by the route itself, after routing, so no spelling
   *  of the path gets past it; the store checks again while it writes. */
  const admin = (request: Request): UserRow | Response => {
    const user = signedIn.get(request);
    return user?.active && user.role === 'admin' ? user : fail(403, { error: 'admin_only' });
  };
  return new Elysia()
    .onRequest(async ({ request, set }) => {
      const host = (request.headers.get('host') ?? '').toLowerCase();
      if (!allowedHosts.has(host)) return fail(403, { error: 'forbidden_host' });
      if (!SAFE_METHODS.has(request.method)) {
        const origin = request.headers.get('origin');
        if (origin !== null && !allowedHosts.has(origin.replace(/^https?:\/\//, '').toLowerCase())) {
          return fail(403, { error: 'forbidden_origin' });
        }
        if (request.headers.get('sec-fetch-site') === 'cross-site') return fail(403, { error: 'forbidden_origin' });
      }
      const path = new URL(request.url).pathname;
      if (!PUBLIC_PATHS.has(path)) {
        const id = sessions.userId(sessionToken(request.headers.get('cookie')));
        const user = id === null ? null : await users.get(id);
        if (!user) return fail(401, { error: 'unauthenticated' });
        if (!user.active && !INACTIVE_PATHS.has(path)) return fail(403, { error: 'account_inactive' });
        signedIn.set(request, user);
      }
      set.headers['cache-control'] = 'no-store'; // results carry employee names
      set.headers['x-content-type-options'] = 'nosniff';
      set.headers['referrer-policy'] = 'no-referrer';
    })
    .onError(({ code, error }) => {
      if (error instanceof SourceError) return fail(SOURCE_STATUS[error.code], { error: error.code });
      if (error instanceof AccessError) return fail(ACCESS_STATUS[error.code], { error: error.code });
      if (error instanceof JobError) {
        return fail(JOB_STATUS[error.code], { error: error.code, ...(error.jobId ? { jobId: error.jobId } : {}) });
      }
      if (error instanceof WorkbookError) {
        console.error(`export failed: ${error.reason}`);
        return fail(500, { error: 'export_failed' });
      }
      if (code === 'VALIDATION' || code === 'PARSE') return fail(400, { error: 'bad_request' });
      if (code === 'NOT_FOUND') return fail(404, { error: 'not_found' });
      // never the message: it may hold a path (a GraphError's holds only the HTTP status)
      const detail = error instanceof GraphError ? ` ${error.status}` : '';
      console.error(`request failed: ${error instanceof Error ? error.name : 'unknown'}${detail}`);
      return fail(500, { error: 'internal_error' });
    })
    // Mock sign-in (no password; see modules/auth/sessions.ts): the first sign-in of an email adds it to the User table as
    // an inactive reviewer, and every sign-in sets its last login. An inactive user gets a session all the same, so the
    // page can say the account waits for an admin.
    .post(
      '/api/auth/login',
      async ({ body, request, set }): Promise<AuthSession | Response> => {
        const identity = loginIdentity(body);
        if (typeof identity === 'string') return fail(400, { error: identity });
        sessions.end(sessionToken(request.headers.get('cookie'))); // a new token on every sign-in
        const user = await users.signIn(identity.email, identity.name);
        set.headers['set-cookie'] = sessionCookie(sessions.create(user.id), new URL(request.url).protocol === 'https:');
        return { user: publicUser(user) };
      },
      { body: t.Object({ email: t.String({ maxLength: 320 }), name: t.String({ maxLength: 300 }) }) },
    )
    .post('/api/auth/logout', ({ request, set }) => {
      sessions.end(sessionToken(request.headers.get('cookie')));
      set.headers['set-cookie'] = sessionCookie(null, new URL(request.url).protocol === 'https:');
      return { ok: true };
    })
    .get('/api/auth/me', ({ request }): AuthSession | Response => {
      const user = signedIn.get(request);
      return user ? { user: publicUser(user) } : fail(401, { error: 'unauthenticated' });
    })
    // The User page: admins only, checked here and again by the store.
    .get('/api/users', async ({ request }): Promise<UserList | Response> => {
      const actor = admin(request);
      if (actor instanceof Response) return actor;
      return { users: (await users.list()).map(managedUser) };
    })
    .patch(
      '/api/users/:id',
      async ({ params, body, request }): Promise<ManagedUser | Response> => {
        const actor = admin(request);
        if (actor instanceof Response) return actor;
        if (body.role === undefined && body.active === undefined) return fail(400, { error: 'bad_request' });
        const row = await users.setAccess(actor.id, params.id, body);
        // an audit line with ids only: names and emails are personal data
        console.info(`access: user ${actor.id} set user ${row.id} to ${row.role}, ${row.active ? 'active' : 'inactive'}`);
        return managedUser(row);
      },
      {
        params: t.Object({ id: USER_ID }),
        body: t.Object({
          role: t.Optional(t.Union([t.Literal('reviewer'), t.Literal('admin')])),
          active: t.Optional(t.Boolean()),
        }),
      },
    )
    .get(
      '/api/health',
      (): Health => ({ extractor: extractor.status(), claude: claudeState(claude, extractor), rootName: source.rootName }),
    )
    .get('/api/folders', ({ query }) => source.list(query.path ?? ''), {
      query: t.Object({ path: t.Optional(PATH) }),
    })
    // How far the runs have read each subfolder of a folder, for the marks in the folder list (counts only, no names).
    .get(
      '/api/folders/marks',
      async ({ query }): Promise<{ marks: Record<string, FolderMark> }> => ({ marks: await jobs.folderMarks(query.path ?? '') }),
      { query: t.Object({ path: t.Optional(PATH) }) },
    )
    .get(
      '/api/files',
      async ({ query }) => {
        const body = await source.pdfBody(query.path);
        const name = query.path.split('/').pop() ?? 'kontrak.pdf';
        return new Response(body, {
          headers: {
            'content-type': 'application/pdf',
            'content-disposition': `inline; filename*=UTF-8''${encodeURIComponent(name)}`,
            'cache-control': 'no-store',
            'x-content-type-options': 'nosniff',
          },
        });
      },
      { query: t.Object({ path: PATH }) },
    )
    // The newest reading of a PDF in any user's run, for opening it from the folder page. POST like /niks: it carries
    // the full NIK, so the cross-origin check applies.
    .post('/api/files/result', async ({ body }): Promise<{ result: LatestResult | null }> => ({ result: await jobs.latest(body.path) }), {
      body: t.Object({ path: PATH }),
    })
    // How each PDF of a folder came out in its newest reading, for the marks in the folder list (no values, no NIK).
    // POST for the list of paths, which can be long.
    .post('/api/files/marks', async ({ body }): Promise<{ marks: Record<string, ReadMark> }> => ({ marks: await jobs.marks(body.paths) }), {
      body: t.Object({ paths: t.Array(PATH, { maxItems: MAX_MARKED_FILES }) }),
    })
    // The folder page's download: the Excel of a selection from each PDF's newest reading in any user's run. A selection
    // with PDFs no run has read is refused (409 `not_all_read`, with how many) unless `includeUnread`: the page reads
    // those first, then asks again with it, so PDFs that failed again are rows that say so.
    .post(
      '/api/files/export',
      async ({ body }) => {
        const { job, niks, unread } = await jobs.latestForExport({ folders: body.folders ?? [], files: body.files ?? [] });
        if (unread > 0 && !body.includeUnread) return fail(409, { error: 'not_all_read', unread, total: job.files.length });
        return sendWorkbook(job, niks);
      },
      {
        body: t.Object({
          folders: t.Optional(t.Array(PATH, { maxItems: MAX_SELECTED_FOLDERS })),
          files: t.Optional(t.Array(PATH, { maxItems: MAX_SELECTED_FILES })),
          includeUnread: t.Optional(t.Boolean()),
        }),
      },
    )
    .get('/api/jobs/current', ({ request }): CurrentRun => jobs.current(userId(request)))
    // every user's runs, newest first: who started each, what it covered, how it came out
    // a page of every user's runs that match the filters, newest first, and how many match
    .get(
      '/api/jobs',
      ({ query, request }): Promise<JobHistory> | Response => {
        const { offset = 0, limit = HISTORY_PAGE, q, status, outcome, mode, mine, from, to } = query;
        if ([from, to].some((at) => at !== undefined && Number.isNaN(Date.parse(at)))) return fail(400, { error: 'bad_request' });
        return jobs.history(offset, limit, { q, status, outcome, mode, mine, from, to }, userId(request));
      },
      {
        query: t.Object({
          offset: t.Optional(t.Numeric({ minimum: 0, maximum: 1_000_000 })),
          limit: t.Optional(t.Numeric({ minimum: 1, maximum: 100 })),
          q: t.Optional(t.String({ maxLength: 100 })),
          status: t.Optional(t.Union([t.Literal('active'), t.Literal('done'), t.Literal('cancelled'), t.Literal('interrupted')])),
          outcome: t.Optional(t.Union([t.Literal('check'), t.Literal('failed')])),
          mode: t.Optional(t.Union([t.Literal('ocr'), t.Literal('full_page')])),
          mine: t.Optional(t.BooleanString()),
          from: t.Optional(INSTANT),
          to: t.Optional(INSTANT),
        }),
      },
    )
    .post(
      '/api/jobs',
      async ({ body, request, set }) => {
        const selection = { folders: body.folders ?? [], files: body.files ?? [], mode: body.mode ?? 'ocr', unreadOnly: body.unreadOnly ?? false };
        if (selection.folders.length + selection.files.length === 0) return fail(400, { error: 'bad_request' });
        const row = signedIn.get(request);
        if (!row) return fail(401, { error: 'unauthenticated' });
        // the signed-in user is saved as the run's starter; the run waits in the queue behind the runs ahead of it
        const starter: RunUser = { id: row.id, name: row.name, email: row.email };
        const job = await jobs.start(selection, starter);
        set.status = 201;
        return job;
      },
      {
        body: t.Object({
          folders: t.Optional(t.Array(PATH, { maxItems: MAX_SELECTED_FOLDERS })),
          files: t.Optional(t.Array(PATH, { maxItems: MAX_SELECTED_FILES })),
          mode: t.Optional(t.Union([t.Literal('ocr'), t.Literal('full_page')])),
          unreadOnly: t.Optional(t.Boolean()),
        }),
      },
    )
    // any signed-in user may open any run (the history is shared); only its starter may cancel or retry it
    .get('/api/jobs/:id', ({ params }) => jobs.get(params.id), { params: t.Object({ id: t.String({ maxLength: 64 }) }) })
    .post('/api/jobs/:id/cancel', ({ params, request }) => jobs.cancel(params.id, userId(request)), {
      params: t.Object({ id: t.String({ maxLength: 64 }) }),
    })
    .post('/api/jobs/:id/retry', ({ params, body, request }) => jobs.retry(params.id, body.file, userId(request)), {
      params: t.Object({ id: t.String({ maxLength: 64 }) }),
      body: t.Object({ file: t.String({ minLength: 1, maxLength: 1000 }) }), // root-relative path of the PDF
    })
    .post('/api/jobs/:id/retry-failed', ({ params, request }) => jobs.retryFailed(params.id, userId(request)), {
      params: t.Object({ id: t.String({ maxLength: 64 }) }),
    })
    // POST, not GET, so the cross-origin check above applies before names and full NIKs leave the backend.
    .post(
      '/api/jobs/:id/export',
      async ({ params }) => {
        const { job, niks } = await jobs.forExport(params.id);
        return sendWorkbook(job, niks);
      },
      { params: t.Object({ id: t.String({ maxLength: 64 }) }) },
    )
    // POST, not GET, so the cross-origin check above applies before full NIKs leave the backend.
    .post('/api/jobs/:id/niks', async ({ params }) => ({ niks: await jobs.niks(params.id) }), {
      params: t.Object({ id: t.String({ maxLength: 64 }) }),
    });
}
