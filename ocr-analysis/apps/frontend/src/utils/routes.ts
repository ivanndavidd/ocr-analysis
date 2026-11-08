// Hash routes, so the backend never needs a catch-all route for the single page.

// `query` of the history: its search, filters and page (see utils/history.ts)
export type Route = { page: 'browse'; path: string } | { page: 'job'; id: string } | { page: 'history'; query: string };

export const folderHref = (path: string) => (path ? `#/folder/${encodeURIComponent(path)}` : '#/');
export const jobHref = (id: string) => `#/job/${encodeURIComponent(id)}`;
/** The shared run history, a page of its own beside the folders. */
export const HISTORY_HREF = '#/riwayat';
export const historyHref = (query = '') => (query ? `${HISTORY_HREF}?${query}` : HISTORY_HREF);

export function parseRoute(hash: string): Route {
  const rest = hash.replace(/^#\/?/, '');
  if (rest === 'riwayat' || rest.startsWith('riwayat?')) return { page: 'history', query: rest.slice('riwayat?'.length) };
  try {
    if (rest.startsWith('job/')) return { page: 'job', id: decodeURIComponent(rest.slice(4)) };
    if (rest.startsWith('folder/')) return { page: 'browse', path: decodeURIComponent(rest.slice(7)) };
  } catch {
    // a malformed escape in a hand-typed URL: fall back to the root folder
  }
  return { page: 'browse', path: '' };
}

/** Root-relative path of a file in a job, as the backend's /api/files expects it. */
export const filePath = (folder: string, name: string) => (folder ? `${folder}/${name}` : name);
