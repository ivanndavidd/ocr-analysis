import type { HistoryFilter, HistoryOutcome, HistoryStatus, ReadMode } from '@contract-ocr/shared-types';
import { MODES } from './claude.ts';
import { t } from './i18n.ts';

/** What the history page lists: its search, filters and page. It lives in the address (`#/riwayat?q=…&page=2`), so a
 *  run opened from the list comes back to the same list. */
export interface HistoryView {
  q: string;
  status: HistoryStatus | '';
  outcome: HistoryOutcome | '';
  mode: ReadMode | '';
  mine: boolean;
  from: string; // yyyy-mm-dd, a local day
  to: string; // yyyy-mm-dd, a local day, included
  page: number; // from 1
  size: number; // runs per page
}

export const PAGE_SIZES = [20, 50, 100] as const; // the backend takes up to 100
export const MAX_SEARCH = 100; // the backend's limit
export const DEFAULT_VIEW: HistoryView = { q: '', status: '', outcome: '', mode: '', mine: false, from: '', to: '', page: 1, size: 20 };

// The filters' choices; their labels are in the current language (utils/messages).
export const STATUS_OPTIONS: readonly HistoryStatus[] = ['active', 'done', 'cancelled', 'interrupted'];
export const OUTCOME_OPTIONS: readonly HistoryOutcome[] = ['check', 'failed'];
export const MODE_OPTIONS: readonly ReadMode[] = MODES;
export const statusOptionLabel = (status: HistoryStatus) => t().history.statuses[status];
export const outcomeOptionLabel = (outcome: HistoryOutcome) => t().history.outcomes[outcome];

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Midnight at the start of a local day. */
const startOf = (day: string) => new Date(`${day}T00:00:00`);

/** `2026-11-06`: the local day of a moment, as a date field holds it. */
export function dayOf(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const allowed = <T extends string>(value: string | null, options: readonly T[]): T | '' =>
  options.some((option) => option === value) ? (value as T) : '';

/** The view an address asks for; anything it cannot use falls back to the default. */
export function parseView(query: string): HistoryView {
  const params = new URLSearchParams(query);
  const day = (key: string) => {
    const value = params.get(key) ?? '';
    return DAY.test(value) && !Number.isNaN(startOf(value).getTime()) ? value : '';
  };
  const page = Number(params.get('page'));
  const size = Number(params.get('size'));
  return {
    q: (params.get('q') ?? '').slice(0, MAX_SEARCH),
    status: allowed(params.get('status'), STATUS_OPTIONS),
    outcome: allowed(params.get('outcome'), OUTCOME_OPTIONS),
    mode: allowed(params.get('mode'), MODE_OPTIONS),
    mine: params.get('mine') === '1',
    from: day('from'),
    to: day('to'),
    page: Number.isInteger(page) && page >= 1 ? page : 1,
    size: (PAGE_SIZES as readonly number[]).includes(size) ? size : DEFAULT_VIEW.size,
  };
}

/** The address query of a view: only what differs from the default, always in the same order. */
export function viewQuery(view: HistoryView): string {
  const params = new URLSearchParams();
  const q = view.q.trim();
  if (q) params.set('q', q);
  if (view.status) params.set('status', view.status);
  if (view.outcome) params.set('outcome', view.outcome);
  if (view.mode) params.set('mode', view.mode);
  if (view.mine) params.set('mine', '1');
  if (view.from) params.set('from', view.from);
  if (view.to) params.set('to', view.to);
  if (view.page > 1) params.set('page', String(view.page));
  if (view.size !== DEFAULT_VIEW.size) params.set('size', String(view.size));
  return params.toString();
}

/** The filters as the backend takes them: the days become moments, from the start of `from` to the end of `to`. */
export function viewFilter(view: HistoryView): HistoryFilter {
  const filter: HistoryFilter = {};
  const q = view.q.trim();
  if (q) filter.q = q;
  if (view.status) filter.status = view.status;
  if (view.outcome) filter.outcome = view.outcome;
  if (view.mode) filter.mode = view.mode;
  if (view.mine) filter.mine = true;
  if (view.from) filter.from = startOf(view.from).toISOString();
  if (view.to) {
    const end = startOf(view.to);
    end.setDate(end.getDate() + 1);
    filter.to = end.toISOString();
  }
  return filter;
}

/** Whether the search or any filter narrows the list. */
export const narrows = (view: HistoryView) =>
  Boolean(view.q.trim() || view.status || view.outcome || view.mode || view.mine || view.from || view.to);

/** The pager's buttons: the first and last page and the pages around the current one; null marks a gap. */
export function pageList(page: number, pages: number): (number | null)[] {
  const shown = [...new Set([1, page - 1, page, page + 1, pages])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const list: (number | null)[] = [];
  for (const n of shown) {
    const last = list.at(-1);
    if (typeof last === 'number' && n - last === 2) list.push(n - 1); // a gap of one page shows that page
    else if (typeof last === 'number' && n - last > 2) list.push(null);
    list.push(n);
  }
  return list;
}
