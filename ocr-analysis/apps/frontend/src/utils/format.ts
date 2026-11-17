import type { DateRange, DocumentPeriod, TokenUsage } from '@contract-ocr/shared-types';

const COUNT = new Intl.NumberFormat('id-ID');
const DATE = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export const formatCount = (count: number) => COUNT.format(count);

/** Every token counted once: fresh input, input written to and read from the cache, and output. */
export const totalTokens = (used: TokenUsage) => used.input + used.cacheWrite + used.cacheRead + used.output;

/** `Total 41.200 token (masuk 3.200 · dari cache 30.000 · keluar 8.000)`: input written to the cache counts as input. */
export function formatTokens(used: TokenUsage): string {
  const input = used.input + used.cacheWrite;
  const parts = [`masuk ${COUNT.format(input)}`, `dari cache ${COUNT.format(used.cacheRead)}`, `keluar ${COUNT.format(used.output)}`];
  return `Total ${COUNT.format(totalTokens(used))} token (${parts.join(' · ')})`;
}
const TIME = new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

/** `2041-01-01` → `1 Jan 2041`. These are calendar dates, so they are read as UTC to avoid a day shift. */
export function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? iso : DATE.format(date);
}

export const formatRange = (range: DateRange | null) =>
  range ? `${formatDate(range.start)} – ${formatDate(range.end)}` : '—';

export const formatTime = (iso: string) => TIME.format(new Date(iso));

const DATE_TIME = new Intl.DateTimeFormat('id-ID', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});
const DAY = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', year: 'numeric' });
/** The local day of a moment: `6 Nov 2026`. */
export const formatDay = (iso: string) => DAY.format(new Date(iso));

/** A moment with its date, in local time: `6 Nov 2026, 11.02`. */
export const formatDateTime = (iso: string) => DATE_TIME.format(new Date(iso));

/** What the regex did find when it did not find both dates: `mulai 15 Apr 2047 · 24 bulan`. */
export function partialPeriod(period: Pick<DocumentPeriod, 'start' | 'end' | 'durationMonths'>): string {
  const parts: string[] = [];
  if (period.start) parts.push(`mulai ${formatDate(period.start)}`);
  if (period.end) parts.push(`selesai ${formatDate(period.end)}`);
  if (period.durationMonths) parts.push(`${period.durationMonths} bulan`);
  return parts.join(' · ') || '—';
}

export const percent = (value: number) => `${Math.round(value * 100)}%`;

/** A NIK, full or masked, in groups of four as on a KTP: `**** **** **** 0001`. */
export const formatNik = (nik: string) => nik.replace(/(.{4})(?=.)/g, '$1 ');
