import type { DateRange, DocumentPeriod, TokenUsage } from '@contract-ocr/shared-types';
import { type Lang, lang, locale, t } from './i18n.ts';

// Numbers, dates and times in the current language: `12.475` and `6 Nov 2026, 11.02` in Indonesian, `12,475` and
// `6 Nov 2026, 11:02` in English. One set of formatters per language, made on first use.

interface Formats {
  count: Intl.NumberFormat;
  date: Intl.DateTimeFormat; // a calendar date, read as UTC
  time: Intl.DateTimeFormat;
  dateTime: Intl.DateTimeFormat;
  day: Intl.DateTimeFormat;
}
const made = new Map<Lang, Formats>();

function formats(): Formats {
  const key = lang();
  let found = made.get(key);
  if (!found) {
    const tag = locale();
    found = {
      count: new Intl.NumberFormat(tag),
      date: new Intl.DateTimeFormat(tag, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }),
      time: new Intl.DateTimeFormat(tag, { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      dateTime: new Intl.DateTimeFormat(tag, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }),
      day: new Intl.DateTimeFormat(tag, { day: 'numeric', month: 'short', year: 'numeric' }),
    };
    made.set(key, found);
  }
  return found;
}

export const formatCount = (count: number) => formats().count.format(count);

/** Every token counted once: fresh input, input written to and read from the cache, and output. */
export const totalTokens = (used: TokenUsage) => used.input + used.cacheWrite + used.cacheRead + used.output;

/** `Total 41.200 token (masuk 3.200 · dari cache 30.000 · keluar 8.000)`: input written to the cache counts as input. */
export function formatTokens(used: TokenUsage): string {
  const input = used.input + used.cacheWrite;
  return t().format.tokens(formatCount(totalTokens(used)), formatCount(input), formatCount(used.cacheRead), formatCount(used.output));
}

/** `2041-01-01` → `1 Jan 2041`. These are calendar dates, so they are read as UTC to avoid a day shift. */
export function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? iso : formats().date.format(date);
}

export const formatRange = (range: DateRange | null) =>
  range ? `${formatDate(range.start)} – ${formatDate(range.end)}` : '—';

export const formatTime = (iso: string) => formats().time.format(new Date(iso));

/** The local day of a moment: `6 Nov 2026`. */
export const formatDay = (iso: string) => formats().day.format(new Date(iso));

/** A moment with its date, in local time: `6 Nov 2026, 11.02`. */
export const formatDateTime = (iso: string) => formats().dateTime.format(new Date(iso));

/** What the regex did find when it did not find both dates: `mulai 15 Apr 2047 · 24 bulan`. */
export function partialPeriod(period: Pick<DocumentPeriod, 'start' | 'end' | 'durationMonths'>): string {
  const m = t().format;
  const parts: string[] = [];
  if (period.start) parts.push(m.starts(formatDate(period.start)));
  if (period.end) parts.push(m.ends(formatDate(period.end)));
  if (period.durationMonths) parts.push(m.months(period.durationMonths));
  return parts.join(' · ') || '—';
}

export const percent = (value: number) => `${Math.round(value * 100)}%`;

/** A NIK, full or masked, in groups of four as on a KTP: `**** **** **** 0001`. */
export const formatNik = (nik: string) => nik.replace(/(.{4})(?=.)/g, '$1 ');
