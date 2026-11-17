import type {
  ApiError,
  AuthSession,
  CurrentRun,
  FolderListing,
  FolderMark,
  Health,
  HistoryFilter,
  Job,
  JobHistory,
  LatestResult,
  LoginRequest,
  ManagedUser,
  ReadMark,
  ReadMode,
  UserAccessChange,
  UserList,
} from '@contract-ocr/shared-types';

/** Fired on `window` when the backend answers 401: the session ended (sign-out elsewhere, expiry, backend restart). */
export const SIGNED_OUT_EVENT = 'contract-ocr:signed-out';
/** Fired on `window` when the backend answers 403 because the user's access changed while signed in (deactivated, or
 *  no longer an admin): the page asks who is signed in again. */
export const ACCESS_CHANGED_EVENT = 'contract-ocr:access-changed';
const ACCESS_CODES = new Set(['account_inactive', 'admin_only']);

/** A refused request. `code` is the backend's fixed error code ('network' when the backend could not be reached). */
export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly jobId?: string,
    readonly counts?: { unread: number; total: number }, // `not_all_read`: PDFs no run has read, of how many
  ) {
    super(code);
    this.name = 'ApiFailure';
  }
}

type Method = 'GET' | 'POST' | 'PATCH';

async function send(url: string, init: { method?: Method; body?: unknown } = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: init.method ?? 'GET',
      headers: init.body === undefined ? {} : { 'content-type': 'application/json' },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: 'no-store',
      credentials: 'same-origin',
    });
  } catch {
    throw new ApiFailure(0, 'network');
  }
  if (!response.ok) {
    let body: Partial<ApiError> = {};
    try {
      body = await response.json();
    } catch {
      // not JSON, e.g. the dev proxy's error page while the backend is down
    }
    if (response.status === 401) dispatchEvent(new Event(SIGNED_OUT_EVENT));
    if (response.status === 403 && ACCESS_CODES.has(body.error ?? '')) dispatchEvent(new Event(ACCESS_CHANGED_EVENT));
    const counts = body.unread !== undefined && body.total !== undefined ? { unread: body.unread, total: body.total } : undefined;
    throw new ApiFailure(response.status, body.error ?? (response.status >= 500 ? 'network' : 'internal_error'), body.jobId, counts);
  }
  return response;
}

async function request<T>(url: string, init: { method?: Method; body?: unknown } = {}): Promise<T> {
  return (await (await send(url, init)).json()) as T;
}

const enc = encodeURIComponent;

/** The `filename*` the backend sent, if it is a plain .xlsx name (no path, no control characters). */
export function downloadName(disposition: string | null): string {
  const fallback = 'Hasil OCR PKWT.xlsx';
  const raw = /filename\*=UTF-8''([^;]+)/i.exec(disposition ?? '')?.[1];
  if (!raw) return fallback;
  let name: string;
  try {
    name = decodeURIComponent(raw);
  } catch {
    return fallback;
  }
  return /^[^\\/:*?"<>|\u0000-\u001f]{1,200}\.xlsx$/.test(name) ? name : fallback;
}
const fileUrl = (path: string) => `/api/files?path=${enc(path)}`;

/** What the user ticked: folders (with their subfolders) and single PDFs, as root-relative paths. */
export interface Selection {
  folders: string[];
  files: string[];
  mode?: ReadMode; // the backend reads with OCR when left out
  unreadOnly?: boolean; // only the PDFs no run has read yet
}

export const api = {
  /** Who is signed in; fails with 401 `unauthenticated` when nobody is. */
  me: () => request<AuthSession>('/api/auth/me'),
  /** Mock sign-in (no password) until the website sits behind Apps Gateway; adds the email to the User table. */
  login: (identity: LoginRequest) => request<AuthSession>('/api/auth/login', { method: 'POST', body: identity }),
  logout: () => request<{ ok: true }>('/api/auth/logout', { method: 'POST' }),
  /** Every user, latest sign-in first (admins only). */
  users: () => request<UserList>('/api/users'),
  /** An admin changes another user's role or active state (never their own). */
  setUserAccess: (id: number, change: UserAccessChange) =>
    request<ManagedUser>(`/api/users/${enc(String(id))}`, { method: 'PATCH', body: change }),
  health: () => request<Health>('/api/health'),
  folders: (path: string) => request<FolderListing>(`/api/folders?path=${enc(path)}`),
  /** How far the runs have read each subfolder of a folder, by its path. */
  folderMarks: (path: string) => request<{ marks: Record<string, FolderMark> }>(`/api/folders/marks?path=${enc(path)}`),
  /** The signed-in user's own run while it is queued or running, and how many runs are queued or running in all. */
  currentJob: () => request<CurrentRun>('/api/jobs/current'),
  /** A page of every user's runs that match the filters, newest first, and how many match; the backend searches,
   *  filters and pages (its page size unless `limit` says otherwise). */
  history: (offset = 0, limit?: number, filter: HistoryFilter = {}) => {
    const params = new URLSearchParams({ offset: String(offset) });
    if (limit) params.set('limit', String(limit));
    for (const [key, value] of Object.entries(filter)) if (value !== undefined && value !== '') params.set(key, String(value));
    return request<JobHistory>(`/api/jobs?${params}`);
  },
  startJob: (selection: Selection) => request<Job>('/api/jobs', { method: 'POST', body: selection }),
  /** A PDF for the in-app viewer. Fetched rather than linked, so its name (an employee's) never reaches the address
   *  bar or browser history. Typed as PDF whatever the file holds, so the browser never renders it as a page. */
  pdf: async (path: string) =>
    new Blob([await (await send(fileUrl(path))).arrayBuffer()], { type: 'application/pdf' }),
  /** The newest reading of a PDF in any user's run, with its full NIK (a POST, like `niks`); null when none read it. */
  latestResult: (path: string) =>
    request<{ result: LatestResult | null }>('/api/files/result', { method: 'POST', body: { path } }),
  /** How each of these PDFs came out in its newest reading, by path; PDFs no run has read are left out. */
  marks: (paths: string[]) =>
    request<{ marks: Record<string, ReadMark> }>('/api/files/marks', { method: 'POST', body: { paths } }),
  job: (id: string) => request<Job>(`/api/jobs/${enc(id)}`),
  cancel: (id: string) => request<Job>(`/api/jobs/${enc(id)}/cancel`, { method: 'POST' }),
  retry: (id: string, path: string) =>
    request<Job>(`/api/jobs/${enc(id)}/retry`, { method: 'POST', body: { file: path } }),
  /** Every failed or cancelled file of the job again; files already read are left as they are. */
  retryFailed: (id: string) => request<Job>(`/api/jobs/${enc(id)}/retry-failed`, { method: 'POST' }),
  /** The job's "data output" workbook (ALL plus one sheet per vendor folder), with the name the backend gave it. */
  exportXlsx: async (id: string) => {
    const response = await send(`/api/jobs/${enc(id)}/export`, { method: 'POST' });
    return { blob: await response.blob(), name: downloadName(response.headers.get('content-disposition')) };
  },
  /** The Excel of a selection from each PDF's newest reading in any user's run. Fails with `not_all_read` (and its
   *  counts) when some PDFs have none, unless `includeUnread`: then those are rows that say they were not read. */
  exportLatest: async (selection: Pick<Selection, 'folders' | 'files'>, includeUnread = false) => {
    const response = await send('/api/files/export', { method: 'POST', body: { ...selection, includeUnread } });
    return { blob: await response.blob(), name: downloadName(response.headers.get('content-disposition')) };
  },
  /** The full NIK of every file that has one, by path; rows only carry the last four digits. */
  niks: (id: string) => request<{ niks: Record<string, string> }>(`/api/jobs/${enc(id)}/niks`, { method: 'POST' }),
};

export const failureCode = (error: unknown) => (error instanceof ApiFailure ? error.code : 'internal_error');
