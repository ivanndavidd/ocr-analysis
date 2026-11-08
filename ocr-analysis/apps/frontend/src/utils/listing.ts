import type { FolderEntry } from '@contract-ocr/shared-types';

// Search and sort for the folder page. Folders always stay above PDFs, as in OneDrive and Explorer.

export type SortKey = 'name' | 'count';
export type SortDir = 'asc' | 'desc';
export interface Sort {
  key: SortKey;
  dir: SortDir;
}

export const DEFAULT_SORT: Sort = { key: 'name', dir: 'asc' };
const STORAGE_KEY = 'contract-ocr:folder-sort';

// Same order as the backend listing: `2 Feb` before `10 Feb`, case and accents ignored.
const collator = new Intl.Collator('id', { numeric: true, sensitivity: 'base' });
const fold = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

/** Every word of the query appears in the name, ignoring case and accents: `budi mar` finds `Budi Santoso - 1mar.pdf`. */
export function matches(name: string, query: string): boolean {
  const target = fold(name);
  return fold(query)
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => target.includes(word));
}

export function sortFolders(folders: FolderEntry[], sort: Sort): FolderEntry[] {
  const sign = sort.dir === 'asc' ? 1 : -1;
  return [...folders].sort((a, b) =>
    sort.key === 'count'
      ? sign * (a.pdfCount - b.pdfCount) || collator.compare(a.name, b.name)
      : sign * collator.compare(a.name, b.name),
  );
}

/** PDFs have no count, so they follow the name order and stay A to Z while folders are sorted by count. */
export function sortFiles(names: string[], sort: Sort): string[] {
  const sign = sort.key === 'name' && sort.dir === 'desc' ? -1 : 1;
  return [...names].sort((a, b) => sign * collator.compare(a, b));
}

/** Clicking the sorted column flips it; another column starts with A to Z for names and most PDFs first for counts. */
export const nextSort = (current: Sort, key: SortKey): Sort =>
  current.key === key
    ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'count' ? 'desc' : 'asc' };

// The sort is a per-browser convenience only; storage can be blocked, so failures are ignored.
export function loadSort(): Sort {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<Sort> | null;
    if ((saved?.key === 'name' || saved?.key === 'count') && (saved.dir === 'asc' || saved.dir === 'desc')) {
      return { key: saved.key, dir: saved.dir };
    }
  } catch {
    // blocked or malformed: use the default
  }
  return DEFAULT_SORT;
}

export function saveSort(sort: Sort): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sort));
  } catch {
    // storage blocked: the order still applies on this page
  }
}

/** `2 folder dan 3 PDF`, `1 folder`, `3 PDF`. */
export function selectionLabel(folders: number, files: number): string {
  return [folders ? `${folders} folder` : '', files ? `${files} PDF` : ''].filter(Boolean).join(' dan ');
}
