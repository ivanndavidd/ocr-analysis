import { afterEach, describe, expect, test } from 'bun:test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { note } from '@contract-ocr/shared-types';
import { callsLabel, claudeConsent, flowLabel, jobFlowLabel, modeLabel } from '../src/utils/claude.ts';
import { checkNote, displayValue, periodCell } from '../src/utils/fields.ts';
import { formatCount, formatDate, formatDateTime, formatTokens, partialPeriod } from '../src/utils/format.ts';
import { DEFAULT_LANG, type Lang, bindLang, isLang, lang, loadLang, saveLang, t } from '../src/utils/i18n.ts';
import { folderMarkTitle, runState } from '../src/utils/jobs.ts';
import { errorLabel, reasonLabel } from '../src/utils/labels.ts';
import { selectionLabel } from '../src/utils/listing.ts';
import { en } from '../src/utils/messages/en.ts';
import { id } from '../src/utils/messages/id.ts';
import { changeQuestion } from '../src/utils/users.ts';

// Every text of the website in both languages, and none left in the components themselves.

type Tree = { [key: string]: unknown };

/** Every leaf of a catalog by its dotted path: strings, functions, and arrays (whose items are checked as leaves). */
function leaves(tree: Tree, prefix = ''): Map<string, unknown> {
  const found = new Map<string, unknown>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [inner, leaf] of leaves(value as Tree, path)) found.set(inner, leaf);
    } else {
      found.set(path, value);
    }
  }
  return found;
}

/** What a leaf shows: a string as it is, a function called with sample values, an array's items joined. */
function rendered(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'function') return String((value as (...args: unknown[]) => unknown)(2, 3, 'Penguji', 'Uji'));
  if (Array.isArray(value)) return value.map((item) => JSON.stringify(item)).join(' ');
  return String(value);
}

const ID = leaves(id);
const EN = leaves(en);

// Texts that are rightly the same in both languages: names, codes, and words English shares with Indonesian here.
const SAME_IN_BOTH = new Set([
  'common.product',
  'common.folder',
  'language.label',
  'language.names.id',
  'language.names.en',
  'login.quickNoteAfter',
  'inactive.email',
  'users.status',
  'users.columnUser',
  'users.roles.admin',
  'users.roles.reviewer',
  'history.user',
  'fields.labels.nik',
  'fields.labels.vendor',
  'fields.groups.nik',
  'fields.groups.vendor',
  'labels.decision.review',
  'check.readByClaude',
]);

describe('every text exists in both languages', () => {
  test('the same keys, the same kind of value, and the same number of list items', () => {
    expect([...EN.keys()].sort()).toEqual([...ID.keys()].sort());
    for (const [path, value] of ID) {
      const other = EN.get(path);
      expect([path, typeof other, Array.isArray(other)]).toEqual([path, typeof value, Array.isArray(value)]);
      if (Array.isArray(value)) expect([path, (other as unknown[]).length]).toEqual([path, value.length]);
    }
  });

  test('every text renders to a real sentence in both languages', () => {
    for (const catalog of [ID, EN]) {
      for (const [path, value] of catalog) {
        const text = rendered(value);
        expect([path, text.trim().length > 0]).toEqual([path, true]);
        expect([path, /undefined|NaN|\[object /.test(text)]).toEqual([path, false]);
      }
    }
  });

  test('English is written, not left in Indonesian', () => {
    const copied = [...ID].filter(([path, value]) => !SAME_IN_BOTH.has(path) && rendered(EN.get(path)) === rendered(value));
    expect(copied.map(([path]) => path)).toEqual([]);
    // and the allowance is not stale: each listed text still is the same in both
    for (const path of SAME_IN_BOTH) expect([path, rendered(EN.get(path))]).toEqual([path, rendered(ID.get(path))]);
  });
});

describe('no text is written into a component', () => {
  const SRC = join(import.meta.dir, '..', 'src');
  const files = readdirSync(SRC, { recursive: true, encoding: 'utf8' })
    .filter((file) => file.endsWith('.svelte'))
    .map((file) => join(SRC, file));
  // Not prose: environment variables, a path and a command shown as code, and the Excel's own (Indonesian) column name.
  const ALLOWED = new Set(['TESSERACT_CMD', 'EXTRACTOR_PYTHON', 'apps/backend/.env', 'bun run dev', '· Excel:']);

  /** The markup without its script, style and comments, every `{…}` expression taken out. */
  function markup(source: string): string {
    const stripped = source
      .replace(/<script[\s\S]*?<\/script>/g, '')
      .replace(/<style[\s\S]*?<\/style>/g, '')
      .replace(/<!--[\s\S]*?-->/g, '');
    let out = '';
    let depth = 0;
    for (const char of stripped) {
      if (char === '{') depth += 1;
      else if (char === '}') depth = Math.max(0, depth - 1);
      else if (depth === 0) out += char;
    }
    return out;
  }

  test('every visible word comes from the catalogs', () => {
    expect(files.length).toBeGreaterThan(8);
    const found: string[] = [];
    for (const file of files) {
      const html = markup(readFileSync(file, 'utf8')).replace(/&[a-z]+;/g, ' '); // &nbsp; and the like are not words
      for (const [, text] of html.matchAll(/>([^<>]+)</g)) {
        const words = text!.replace(/\s+/g, ' ').trim();
        if (/\p{L}/u.test(words) && !ALLOWED.has(words)) found.push(`${file}: ${words}`);
      }
      for (const [, attribute, value] of html.matchAll(/\s(placeholder|title|aria-label|alt|label|data-label)="([^"]*)"/g)) {
        if (/\p{L}/u.test(value!)) found.push(`${file}: ${attribute}="${value}"`);
      }
    }
    expect(found).toEqual([]);
  });
});

describe('switching the language', () => {
  const use = (next: Lang) => bindLang(() => next);
  afterEach(() => use(DEFAULT_LANG));

  test('Indonesian is the default and keeps the wording HR knows', () => {
    expect(lang()).toBe('id');
    expect(t().login.title).toBe('Masuk');
    expect(errorLabel('admin_only')).toBe('Hanya admin yang bisa membuka halaman User');
  });

  test('every label, error and reason follows the language', () => {
    use('en');
    expect(t().login.title).toBe('Sign in');
    expect(errorLabel('admin_only')).toBe('Only admins can open the Users page');
    expect(errorLabel('no_such_code')).toBe('no_such_code');
    expect(reasonLabel('extractor_error:timeout')).toBe('One Claude call failed; that part was read with the text patterns only');
    expect([flowLabel('on'), modeLabel('claude_page'), modeLabel('full_page'), jobFlowLabel(true)]).toEqual([
      'Text patterns + Claude',
      'Claude reads page',
      'Whole pages (old trial)',
      'helped by Claude',
    ]);
    expect(callsLabel([{ kind: 'page', tokens: null }])).toBe('identity page (NIK blacked out)');
    expect(claudeConsent('on', 1)).toStartWith('Of the 1 PDF,');
    expect(callsLabel([{ kind: 'nik', tokens: { input: 1000, cacheWrite: 10, cacheRead: 0, output: 1 } }])).toBe('NIK line crop (1,011 tokens)');
    expect(runState({ status: 'queued', queueAhead: 1 })).toBe('queued (1 run ahead)');
    expect(selectionLabel(1, 2)).toBe('1 folder and 2 PDFs');
    expect(folderMarkTitle({ total: 9, read: 3, check: 3 })).toBe('3 of 9 PDFs read, 6 not yet. Latest OCR results: 3 Needs a check');
    expect(changeQuestion({ id: 2, name: 'Penguji Dua' } as never, { active: false })?.title).toBe('Deactivate this user?');
  });

  test('dates and numbers follow the language', () => {
    expect([formatDate('2045-01-01'), formatCount(12475)]).toEqual(['1 Jan 2045', '12.475']);
    expect(formatDateTime('2026-11-06T04:02:00Z')).toMatch(/\d{2}\.\d{2}$/);
    use('en');
    expect([formatDate('2045-01-01'), formatCount(12475)]).toEqual(['1 Jan 2045', '12,475']);
    expect(formatDateTime('2026-11-06T04:02:00Z')).toMatch(/\d{2}:\d{2}$/);
    expect(formatTokens({ input: 3000, cacheWrite: 200, cacheRead: 30000, output: 8000 })).toBe(
      'Total 41,200 tokens (in 3,200 · from cache 30,000 · out 8,000)',
    );
    expect(partialPeriod({ start: '2047-04-15', end: null, durationMonths: 1 })).toBe('starts 15 Apr 2047 · 1 month');
  });

  test("the backend's cell notes render in the reader's language", () => {
    const field = { value: '2026-03-31', check: note('period.filename_says', { date: '2026-02-28' }), basis: null };
    expect(checkNote(field)).toBe('Nama file menyebut 28 Feb 2026');
    use('en');
    expect(checkNote(field)).toBe('The filename says 28 Feb 2026');
    expect(displayValue('signature', { value: 'YES', check: null, basis: null })).toBe('Signed');
    expect(displayValue('months', { value: 1, check: null, basis: null })).toBe('1 month');
    const sure = { value: '2026-01-01', check: null, basis: note('period.same') };
    const row = { fields: { start: sure, end: field, months: { value: 2, check: null, basis: null } } } as never;
    expect(periodCell(row)).toMatchObject({ checks: ['The filename says 28 Feb 2026'], basis: 'The same in the document and in the filename' });
  });

  test('the choice is remembered on this browser, and anything odd falls back to Indonesian', () => {
    const store = new Map<string, string>();
    const original = globalThis.localStorage;
    globalThis.localStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    } as Storage;
    try {
      expect(loadLang()).toBe('id');
      saveLang('en');
      expect(loadLang()).toBe('en');
      store.set('contract-ocr:lang', 'fr');
      expect(loadLang()).toBe('id');
      expect([isLang('en'), isLang('id'), isLang('EN'), isLang(null)]).toEqual([true, true, false, false]);
    } finally {
      globalThis.localStorage = original;
    }
  });
});
