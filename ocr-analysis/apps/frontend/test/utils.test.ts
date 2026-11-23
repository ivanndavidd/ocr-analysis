import { describe, expect, test } from 'bun:test';
import { type ClaudeCallKind, type ContractFields, type ContractRow, type FieldValue, type JobFile, type ManagedUser, note } from '@contract-ocr/shared-types';
import { downloadName } from '../src/services/api.ts';
import { displayValue, fieldPage, fullNik, groupsToCheck, periodCell, rowState, toCheck } from '../src/utils/fields.ts';
import { formatDate, formatDateTime, formatDay, formatNik, formatRange, formatTokens, partialPeriod, totalTokens } from '../src/utils/format.ts';
import {
  markLabel,
  outcomeLabel,
  claudeTokens,
  folderDone,
  folderMarkTitle,
  folderPart,
  groupByFolder,
  isActive,
  jobHome,
  jobTitle,
  markTitle,
  markWhen,
  readBy,
  relativeTo,
  runState,
  vendorOf,
} from '../src/utils/jobs.ts';
import { t } from '../src/utils/i18n.ts';
import { errorLabel, reasonLabel } from '../src/utils/labels.ts';
import { matches, nextSort, selectionLabel, sortFiles, sortFolders } from '../src/utils/listing.ts';
import {
  claudeHint,
  flowLabel,
  MODES,
  modeLabel,
  callsLabel,
  claudeConsent,
  claudeNotice,
  flowDescription,
  jobFlowLabel,
  loadMode,
  pageBlocked,
  pageConsent,
  pageDescription,
  pageFlow,
  pageNotice,
  saveMode,
} from '../src/utils/claude.ts';
import { HISTORY_HREF, USERS_HREF, filePath, folderHref, historyHref, jobHref, parseRoute } from '../src/utils/routes.ts';
import {
  DEFAULT_VIEW,
  type HistoryView,
  MODE_OPTIONS,
  dayOf,
  narrows,
  pageList,
  parseView,
  viewFilter,
  viewQuery,
} from '../src/utils/history.ts';
import { TEST_ACCOUNTS } from '../src/utils/testAccounts.ts';
import { NO_FILTER, changeQuestion, filterUsers, initials, lockReason, userCounts } from '../src/utils/users.ts';

describe('routes', () => {
  test('folder and job links round-trip, including spaces and slashes', () => {
    const path = 'PT Bina Semesta/Januari 2041 - Maret 2041';
    expect(parseRoute(folderHref(path))).toEqual({ page: 'browse', path });
    expect(parseRoute(jobHref('abc-123'))).toEqual({ page: 'job', id: 'abc-123' });
    expect(parseRoute(HISTORY_HREF)).toEqual({ page: 'history', query: '' });
    expect(parseRoute(USERS_HREF)).toEqual({ page: 'users' });
    expect(parseRoute(historyHref('q=PT+Uji&page=2'))).toEqual({ page: 'history', query: 'q=PT+Uji&page=2' });
    expect(historyHref('')).toBe(HISTORY_HREF);
  });

  test.each(['', '#', '#/', '#/unknown', '#/folder/%E0%A4%A'])('%p falls back to the root folder', (hash) => {
    expect(parseRoute(hash)).toEqual({ page: 'browse', path: '' });
  });

  test('file paths are root-relative', () => {
    expect(filePath('', 'a.pdf')).toBe('a.pdf');
    expect(filePath('PT A/Jan', 'a.pdf')).toBe('PT A/Jan/a.pdf');
  });
});

describe('format', () => {
  test('calendar dates do not shift with the time zone', () => {
    expect(formatDate('2041-01-01')).toBe('1 Jan 2041');
    expect(formatRange({ start: '2041-01-01', end: '2041-03-31' })).toBe('1 Jan 2041 – 31 Mar 2041');
    expect(formatRange(null)).toBe('—');
  });

  test('tokens read as one total with the cache apart', () => {
    const used = { input: 40, cacheWrite: 2000, cacheRead: 30000, output: 9100 };
    expect(totalTokens(used)).toBe(41_140);
    expect(formatTokens(used)).toBe('Total 41.140 token (masuk 2.040 · dari cache 30.000 · keluar 9.100)');
  });

  test('a partial period shows whatever was found', () => {
    expect(partialPeriod({ start: '2047-04-15', end: null, durationMonths: 24 })).toBe('mulai 15 Apr 2047 · 24 bulan');
    expect(partialPeriod({ start: null, end: '2047-04-15', durationMonths: null })).toBe('selesai 15 Apr 2047');
    expect(partialPeriod({ start: null, end: null, durationMonths: null })).toBe('—');
  });

  test('a NIK is grouped in fours like on a KTP, masked or not', () => {
    expect(formatNik('************0001')).toBe('**** **** **** 0001');
    expect(formatNik('3199990101900001')).toBe('3199 9901 0190 0001');
  });
});

describe('labels', () => {
  test('every queue refusal and run status has its own wording', () => {
    const codes = ['job_running', 'user_has_job', 'not_your_job'];
    const labels = codes.map(errorLabel);
    expect(labels.some((label, i) => label === codes[i])).toBe(false);
    expect(new Set(labels).size).toBe(codes.length);
    expect(Object.keys(t().labels.jobStatus).sort()).toEqual(['cancelled', 'done', 'interrupted', 'queued', 'running']);
  });

  test('tokens of a result taken from another run count there, not in this run', () => {
    const used = { input: 10, cacheWrite: 0, cacheRead: 0, output: 5 };
    const row = { tokens: used, claudeCalls: [{ kind: 'text', tokens: used }] } as unknown as ContractRow;
    const own: JobFile = { path: 'a/x.pdf', folder: 'a', name: 'x.pdf', state: 'done', row };
    const taken: JobFile = { ...own, path: 'a/y.pdf', name: 'y.pdf', readIn: { jobId: 'j', by: 'Penguji Lain' } };
    expect(claudeTokens([own, taken])).toEqual({ used, files: 1, calls: 1 });
    expect(claudeTokens([taken])).toBeNull();
  });

  test("a run's state reads as a sentence, with the runs ahead of a queued one", () => {
    expect(runState({ status: 'queued', queueAhead: 2 })).toBe('menunggu giliran (2 proses di depan)');
    expect(runState({ status: 'queued' })).toBe('menunggu giliran');
    expect(runState({ status: 'interrupted' })).toBe('terputus');
    expect([isActive({ status: 'queued' }), isActive({ status: 'running' }), isActive({ status: 'interrupted' })]).toEqual([true, true, false]);
  });

  test('every sign-in refusal the backend sends has its own wording', () => {
    const codes = ['unauthenticated', 'bad_email', 'bad_name'];
    const labels = codes.map(errorLabel);
    expect(labels.some((label, i) => label === codes[i])).toBe(false);
    expect(new Set(labels).size).toBe(codes.length);
  });

  test('known codes are translated and unknown codes pass through', () => {
    expect(errorLabel('timeout')).toBe('Terlalu lama diproses');
    expect(errorLabel('not_pdf_magic')).toBe('Isi file bukan PDF');
    expect(reasonLabel('kode_baru')).toBe('kode_baru');
    expect(reasonLabel('nik_birth_date_mismatch')).toBe('NIK tidak cocok dengan tanggal lahir');
    // a Claude failure carries its kind after a colon; the label is the same for every kind
    expect(reasonLabel('extractor_error:api:RateLimitError')).toBe(reasonLabel('extractor_error'));
    expect(reasonLabel('extractor_error')).toContain('pola teks');
  });
});

describe('reading flow', () => {
  test('one flow: the patterns, with Claude helping when the backend has it', () => {
    expect([flowLabel('on'), flowLabel('off')]).toEqual(['Pola teks + Claude', 'Pola teks']);
    expect(flowDescription('on')).toContain('potongan gambar baris NIK');
    expect(flowDescription('on')).toContain('hanya bila nama, periode, posisi, atau vendor belum bisa dipastikan');
    expect(reasonLabel('extractor_not_needed')).toContain('tidak dikirim ke Claude');
    expect(flowDescription('off')).not.toContain('Claude');
    expect(flowDescription(null)).toBe(flowDescription('off'));
    expect(claudeHint('unavailable')).toContain('pola teks saja');
    expect(claudeHint('on')).toBeNull();
  });

  test('what was sent for a file is listed in order', () => {
    const used = { input: 10, cacheWrite: 0, cacheRead: 9000, output: 2500 };
    expect(callsLabel([{ kind: 'text', tokens: used }, { kind: 'nik', tokens: null }, { kind: 'signature', tokens: null }])).toBe(
      'teks halaman identitas (11.510 token), potongan baris NIK, potongan area tanda tangan',
    );
    expect(callsLabel([])).toBe('');
    expect([jobFlowLabel(true), jobFlowLabel(false)]).toEqual(['dibantu Claude', 'pola teks saja']);
    expect(callsLabel([{ kind: 'page', tokens: null }])).toBe('halaman identitas (NIK ditutup)');
    expect(callsLabel([{ kind: 'pages', tokens: null }])).toBe('halaman pertama dan terakhir utuh'); // old trial runs
  });
});

describe('read mode', () => {
  test('the OCR mode comes first and is what a new tab starts with; the old no-OCR trial cannot be picked', () => {
    expect(MODES).toEqual(['ocr', 'claude_page']);
    expect([modeLabel('ocr'), modeLabel('claude_page')]).toEqual(['Dengan OCR', 'Claude baca halaman']);
    saveMode('claude_page'); // no session storage under bun test: nothing is kept, and nothing throws
    expect(loadMode()).toBe('ocr');
    expect(jobFlowLabel(true, 'claude_page')).toBe('Claude baca halaman identitas');
    expect(jobFlowLabel(true, 'ocr')).toBe('dibantu Claude');
    // runs of the old trial keep a label of their own in the history
    expect([modeLabel('full_page'), jobFlowLabel(true, 'full_page')]).toEqual(['Halaman utuh (uji lama)', 'halaman utuh ke Claude (uji lama)']);
    expect(MODE_OPTIONS).toEqual(['ocr', 'claude_page', 'full_page']);
  });

  test('Claude reading the page says the NIK is blacked out and what still goes, and needs Claude ready', () => {
    const consent = pageConsent(3);
    for (const part of ['Dari 3 PDF', 'halaman identitas', 'NIK ditutup', 'alamat', 'tanggal lahir', 'biaya']) {
      expect(consent).toContain(part);
    }
    expect(consent).not.toContain('UTUH');
    expect(pageDescription()).toContain('tidak dikirim'); // an unlocated NIK keeps the image on this laptop
    expect(pageFlow()).toContain('NIK ditutup');
    expect(pageBlocked('on')).toBeNull();
    expect(pageBlocked('starting')).toContain('Tunggu');
    for (const state of ['off', 'unavailable', null] as const) expect(pageBlocked(state)).toContain('Dengan OCR');
    expect(reasonLabel('read_identity_image')).toContain('NIK ditutup');
    expect(reasonLabel('identity_image_not_sent')).toContain('tidak dikirim');
    expect(reasonLabel('read_full_pages')).toContain('uji lama');
    expect(errorLabel('claude_failed')).toContain('Claude gagal'); // failed files saved by the old trial
    expect(errorLabel('claude_unavailable')).toContain('Claude baca halaman');
    expect(errorLabel('mode_retired')).toContain('uji lama');
  });
});

describe('jobs', () => {
  test('a PDF opened from the folder page says who read it, and when or that the run is still going', () => {
    const run = { jobId: 'a', by: 'Penguji A', mode: 'ocr' as const, status: 'done' as const, startedAt: '2026-11-06T03:00:00.000Z', finishedAt: '2026-11-06T04:02:00.000Z' };
    const ended = formatDateTime(run.finishedAt);
    expect(ended).toContain('2026'); // the date is there, not only the time
    expect(readBy(run)).toBe(`Penguji A · ${ended}`);
    expect(readBy({ ...run, mode: 'claude_page' })).toBe(`Penguji A · ${ended} · Claude baca halaman`);
    expect(readBy({ ...run, mode: 'full_page' })).toBe(`Penguji A · ${ended} · Halaman utuh (uji lama)`);
    expect(readBy({ ...run, status: 'running', finishedAt: null, by: null })).toBe('Tanpa nama · proses masih berjalan');
    expect(readBy({ ...run, status: 'interrupted', finishedAt: null })).toBe(`Penguji A · ${formatDateTime(run.startedAt)}`);

    // the same reading as a mark in the folder list: that it was read, and the day; how it came out and who on hover
    const mark = { outcome: 'needs_check' as const, run };
    expect(markLabel()).toBe('Sudah di-OCR');
    expect(outcomeLabel(mark.outcome)).toBe('Perlu dicek');
    expect(markWhen(mark)).toBe(formatDay(run.finishedAt));
    expect(markWhen({ ...mark, run: { ...run, status: 'queued', finishedAt: null } })).toBe('proses berjalan');
    expect(markTitle({ ...mark, outcome: 'ready' })).toBe(`Hasil OCR terakhir: Siap dipakai, Penguji A · ${ended}`);
  });

  test('a folder gets the mark once every PDF in it has been read, and says how far it got before that', () => {
    expect(folderDone({ total: 9, read: 9, check: 2 })).toBe(true);
    expect(folderDone({ total: 9, read: 8, check: 0 })).toBe(false);
    expect(folderDone({ total: 0, read: 0, check: 0 })).toBe(false);
    expect(folderDone(undefined)).toBe(false);
    expect(folderPart({ total: 9, read: 3, check: 1 })).toBe('3/9 di-OCR');
    expect(folderMarkTitle({ total: 9, read: 9, check: 2 })).toBe('Semua 9 PDF sudah di-OCR. Hasil OCR terakhir: 7 Siap dipakai, 2 Perlu dicek');
    expect(folderMarkTitle({ total: 9, read: 9, check: 0 })).toBe('Semua 9 PDF sudah di-OCR. Hasil OCR terakhir: 9 Siap dipakai');
    expect(folderMarkTitle({ total: 9, read: 3, check: 3 })).toBe('3 dari 9 PDF sudah di-OCR, 6 belum. Hasil OCR terakhir: 3 Perlu dicek');
  });

  const file = (path: string) => {
    const at = path.lastIndexOf('/');
    return { path, folder: path.slice(0, Math.max(at, 0)), name: path.slice(at + 1), state: 'waiting' as const };
  };

  test("a job's Claude tokens are summed over every call of every file that went to Claude", () => {
    const read = (tokens: ContractRow['tokens'], claudeCalls: ContractRow['claudeCalls'] = []) => ({
      ...file('a.pdf'),
      state: 'done' as const,
      row: { tokens, claudeCalls } as ContractRow,
    });
    const one = { input: 10, cacheWrite: 2000, cacheRead: 0, output: 500 };
    const two = { input: 5, cacheWrite: 0, cacheRead: 2000, output: 700 };
    const calls = (...kinds: ClaudeCallKind[]) => kinds.map((kind) => ({ kind, tokens: null }));
    const unreported = read(null, calls('text')); // sent, but the provider reported no tokens
    expect(claudeTokens([read(one, calls('text', 'nik')), read(null), read(two, calls('text')), unreported, file('b.pdf')])).toEqual({
      used: { input: 15, cacheWrite: 2000, cacheRead: 2000, output: 1200 },
      files: 3,
      calls: 4,
    });
    expect(claudeTokens([read(null), file('b.pdf')])).toBeNull();
  });

  test('title and back link for one folder and for a selection', () => {
    const one = { folders: ['PT A/Jan 2026 - Mar 2026'] };
    expect(jobTitle(one, 'root')).toBe('Jan 2026 - Mar 2026');
    expect(jobHome(one)).toBe('PT A/Jan 2026 - Mar 2026');
    const many = { folders: ['PT A/Jan', 'PT A/Feb'] };
    expect(jobTitle(many, 'root')).toBe('2 folder');
    expect(jobHome(many)).toBe('PT A');
    expect(jobTitle({ folders: [''] }, 'root')).toBe('root');
  });

  test('folders are named relative to the job home', () => {
    expect(relativeTo('PT A/Jan', 'PT A', 'root')).toBe('Jan');
    expect(relativeTo('PT A/Jan/x', 'PT A', 'root')).toBe('Jan/x');
    expect(relativeTo('PT A', 'PT A', 'root')).toBe('PT A');
    expect(relativeTo('PT AB/Jan', 'PT A', 'root')).toBe('PT AB/Jan');
    expect(relativeTo('PT A', '', 'root')).toBe('PT A');
    expect(relativeTo('', '', 'root')).toBe('root');
  });

  test('files are grouped by their folder in order', () => {
    const groups = groupByFolder([file('A/1.pdf'), file('A/2.pdf'), file('B/x/3.pdf'), file('4.pdf')]);
    expect(groups.map((g) => [g.folder, g.files.length])).toEqual([
      ['A', 2],
      ['B/x', 1],
      ['', 1],
    ]);
  });
});

describe('folder listing', () => {
  const folder = (name: string, pdfCount: number) => ({ name, path: `PT A/${name}`, pdfCount });
  const folders = [folder('Mei 2029', 9), folder('Agustus 2036', 13), folder('januari 2030', 13), folder('Kosong', 0)];

  test('search matches every word, ignoring case and accents', () => {
    expect(matches('Budi Santoso - 1jan-31mar26.pdf', 'budi MAR')).toBe(true);
    expect(matches('Budi Santoso - 1jan-31mar26.pdf', 'budi siti')).toBe(false);
    expect(matches('José Pérez.pdf', 'jose perez')).toBe(true);
    expect(matches('anything', '  ')).toBe(true);
  });

  test('folders sort by name or PDF count, PDFs by name only', () => {
    const names = (sorted: { name: string }[]) => sorted.map((f) => f.name);
    expect(names(sortFolders(folders, { key: 'name', dir: 'asc' }))).toEqual(['Agustus 2036', 'januari 2030', 'Kosong', 'Mei 2029']);
    expect(names(sortFolders(folders, { key: 'name', dir: 'desc' }))).toEqual(['Mei 2029', 'Kosong', 'januari 2030', 'Agustus 2036']);
    // ties keep A to Z whichever way the count goes
    expect(names(sortFolders(folders, { key: 'count', dir: 'desc' }))).toEqual(['Agustus 2036', 'januari 2030', 'Mei 2029', 'Kosong']);
    expect(names(sortFolders(folders, { key: 'count', dir: 'asc' }))).toEqual(['Kosong', 'Mei 2029', 'Agustus 2036', 'januari 2030']);
    const pdfs = ['b 10.pdf', 'b 2.pdf', 'A.pdf'];
    expect(sortFiles(pdfs, { key: 'name', dir: 'asc' })).toEqual(['A.pdf', 'b 2.pdf', 'b 10.pdf']);
    expect(sortFiles(pdfs, { key: 'name', dir: 'desc' })).toEqual(['b 10.pdf', 'b 2.pdf', 'A.pdf']);
    expect(sortFiles(pdfs, { key: 'count', dir: 'desc' })).toEqual(['A.pdf', 'b 2.pdf', 'b 10.pdf']);
  });

  test('clicking a header flips it or starts the new column at its natural order', () => {
    expect(nextSort({ key: 'name', dir: 'asc' }, 'name')).toEqual({ key: 'name', dir: 'desc' });
    expect(nextSort({ key: 'name', dir: 'asc' }, 'count')).toEqual({ key: 'count', dir: 'desc' });
    expect(nextSort({ key: 'count', dir: 'desc' }, 'name')).toEqual({ key: 'name', dir: 'asc' });
  });

  test('a selection is described in words', () => {
    expect(selectionLabel(2, 3)).toBe('2 folder dan 3 PDF');
    expect(selectionLabel(1, 0)).toBe('1 folder');
    expect(selectionLabel(0, 3)).toBe('3 PDF');
  });
});

describe('run confirmation', () => {
  test('warns of what goes to Claude and its cost only when Claude helps', () => {
    for (const state of ['on', 'starting'] as const) {
      const consent = claudeConsent(state, 3);
      expect(consent).toContain('Dari 3 PDF');
      expect(consent).toContain('potongan area tanda tangan');
      expect(consent).toContain('biaya');
    }
    expect([claudeConsent('off', 3), claudeConsent('unavailable', 3), claudeConsent(null, 3)]).toEqual([null, null, null]);
  });

  test('the one-line notices stay visible when the details are folded away', () => {
    for (const state of ['on', 'starting'] as const) {
      const notice = claudeNotice(state);
      expect(notice).toContain('Claude');
      expect(notice).toContain('biaya');
    }
    expect([claudeNotice('off'), claudeNotice('unavailable'), claudeNotice(null)]).toEqual([null, null, null]);
    for (const part of ['halaman identitas', 'NIK ditutup', 'alamat', 'tanggal lahir', 'lokal']) {
      expect(pageNotice()).toContain(part);
    }
  });
});

describe('vendor', () => {
  test('is the first folder under the root', () => {
    expect(vendorOf('PT Bumi Dinamika/Februari 2032 - Mei 2032/Budi - 7feb-6mei32.pdf')).toBe('PT Bumi Dinamika');
    expect(vendorOf('PT A/x.pdf')).toBe('PT A');
    expect(vendorOf('lepas.pdf')).toBe('');
  });

  test('pattern and Claude reasons have labels', () => {
    for (const code of [
      'period_conflict',
      'page_selection_low_margin',
      'signature_not_clear',
      'name_not_in_source',
      'identity_page_not_found',
      'signature_page_not_found',
      'signature_page_guessed',
      'nik_digits_repaired',
      'nik_from_llm',
      'nik_llm_differs',
      'signature_from_llm',
      'signature_llm_agrees',
      'signature_llm_differs',
      'signature_llm_sees_none',
    ]) {
      expect(reasonLabel(code)).not.toBe(code);
    }
  });
});

describe('download name', () => {
  const NAME = 'Hasil OCR PKWT - PT Bumi Dinamika - 2 Nov 2026 13.45.xlsx';

  test('takes the UTF-8 name the backend sent', () => {
    const header = `attachment; filename="Hasil OCR PKWT.xlsx"; filename*=UTF-8''${encodeURIComponent(NAME)}`;
    expect(downloadName(header)).toBe(NAME);
  });

  test.each([
    [null],
    ['attachment; filename="Hasil OCR PKWT.xlsx"'],
    ["attachment; filename*=UTF-8''..%2F..%2Fevil.xlsx"],
    ["attachment; filename*=UTF-8''report.exe"],
    ["attachment; filename*=UTF-8''%E0%A4%A"],
  ])('falls back on %p', (header) => {
    expect(downloadName(header)).toBe('Hasil OCR PKWT.xlsx');
  });
});

describe('fields', () => {
  // notes saved as text render the same in either language, which keeps these fixtures readable
  const ok = (value: string | number | null): FieldValue => ({ value, check: null, basis: note('legacy', { text: 'cocok' }) });
  const doubt = (value: string | number | null): FieldValue => ({ value, check: note('legacy', { text: 'cek' }), basis: null });
  const fields: ContractFields = {
    nik: ok('************0001'),
    vendor: ok('PT Contoh Sejahtera'),
    entity: ok('SNA'),
    name: ok('Budi Santoso'),
    position: doubt(null),
    start: ok('2026-01-01'),
    end: doubt('2026-03-31'),
    months: ok(3),
    signature: ok('YES'),
  };
  const row = {
    fields,
    detail: { identityPages: [1, 2], signaturePage: 5, pageKinds: {}, ocrConfidence: {}, signatureSource: null, vendorNote: null },
  } as unknown as ContractRow;
  const file = (extra: Partial<JobFile>): JobFile => ({ path: 'a.pdf', folder: '', name: 'a.pdf', state: 'done', ...extra });

  test('a row is ready only when no value needs a check', () => {
    expect(toCheck(row)).toEqual(['position', 'end']);
    expect(rowState(file({ row }))).toBe('check');
    const clean = { ...row, fields: { ...fields, position: ok('Kasir'), end: ok('2026-03-31') } };
    expect(rowState(file({ row: clean }))).toBe('ready');
    expect(rowState(file({ state: 'error', error: 'timeout' }))).toBe('failed');
    expect(rowState(file({ state: 'reading' }))).toBe('pending');
  });

  test('the three period columns are filtered as one', () => {
    expect([...groupsToCheck(row)]).toEqual(['position', 'period']);
  });

  test('the table shows the period as one range, to check when any of its three values is', () => {
    expect(periodCell(row)).toEqual({ range: '1 Jan 2026 – 31 Mar 2026', months: '3 bulan', checks: ['cek'], basis: 'cocok' });
    const none = { ...row, fields: { ...fields, start: ok(null), end: ok(null), months: ok(null) } };
    expect(periodCell(none)).toMatchObject({ range: null, months: null, checks: [] });
    const half = { ...row, fields: { ...fields, end: doubt(null), months: { value: null, check: note('legacy', { text: 'lain' }), basis: null } } };
    expect(periodCell(half)).toMatchObject({ range: '1 Jan 2026 – ?', checks: ['cek', 'lain'] });
  });

  test('values read the way the Excel means them', () => {
    expect(displayValue('nik', fields.nik)).toBe('**** **** **** 0001');
    expect(displayValue('start', fields.start)).toBe('1 Jan 2026');
    expect(displayValue('months', fields.months)).toBe('3 bulan');
    expect(displayValue('signature', fields.signature)).toBe('Ada');
    expect(displayValue('signature', ok('NO'))).toBe('Tidak ada');
    expect(displayValue('position', fields.position)).toBeNull();
  });

  test('the full NIK replaces the masked one only while it is the NIK the row shows', () => {
    const nik = (masked: string | null) => ({ ...row, nik: { status: 'found', masked } }) as unknown as ContractRow;
    expect(fullNik(nik('************0001'), '3199990101900001')).toBe('3199990101900001');
    expect(fullNik(nik('************0002'), '3199990101900001')).toBeNull(); // a retry read another NIK
    expect(fullNik(nik(null), '3199990101900001')).toBeNull(); // a retry found none
    expect(fullNik(nik('************0001'), undefined)).toBeNull(); // not loaded yet
    expect(fullNik(undefined, '3199990101900001')).toBeNull();
  });

  test('each value points at the page it came from', () => {
    expect(fieldPage('nik', row)).toBe(1);
    expect(fieldPage('end', row)).toBe(2);
    expect(fieldPage('signature', row)).toBe(5);
    expect(fieldPage('entity', row)).toBeNull(); // only in the filename
    expect(fieldPage('vendor', row)).toBe(1); // printed with the parties, on the identity page
  });
});

describe('quick sign-in', () => {
  test('the test accounts are distinct, well-formed and fictitious', () => {
    expect(TEST_ACCOUNTS.length).toBeGreaterThan(1);
    expect(new Set(TEST_ACCOUNTS.map((a) => a.email.toLowerCase())).size).toBe(TEST_ACCOUNTS.length);
    for (const { email, name } of TEST_ACCOUNTS) {
      expect(email).toMatch(/^penguji\.[a-z0-9.]+@gdn-commerce\.com$/); // the sign-in accepts it; plainly a tester
      expect(name).toMatch(/^Penguji /);
    }
  });

  test('an avatar shows up to two initials', () => {
    expect(initials('Penguji Dua')).toBe('PD');
    expect(initials('  penguji   tiga lagi ')).toBe('PT');
    expect(initials('Andi')).toBe('A');
  });
});

describe('history list', () => {
  const view: HistoryView = {
    q: 'PT Uji/April',
    status: 'done',
    outcome: 'check',
    mode: 'full_page',
    mine: true,
    from: '2026-11-01',
    to: '2026-11-06',
    page: 3,
    size: 50,
  };

  test('the search, filters and page survive the address, which holds only what differs from the default', () => {
    expect(parseView(viewQuery(view))).toEqual(view);
    expect(viewQuery(DEFAULT_VIEW)).toBe('');
    expect(viewQuery({ ...DEFAULT_VIEW, q: '   ' })).toBe('');
    expect(viewQuery({ ...DEFAULT_VIEW, q: ' Budi ', page: 2 })).toBe('q=Budi&page=2');
    expect(parseView('')).toEqual(DEFAULT_VIEW);
  });

  test('an address it cannot use falls back to the default, field by field', () => {
    const bad = 'status=bogus&outcome=x&mode=pdf&mine=yes&from=2026-13-45&to=kemarin&page=-2&size=7';
    expect(parseView(bad)).toEqual(DEFAULT_VIEW);
    expect(parseView(`q=${'x'.repeat(150)}&page=1.5&size=100`)).toEqual({ ...DEFAULT_VIEW, q: 'x'.repeat(100), size: 100 });
  });

  test('the days become moments: from the start of the first day to the end of the last, in local time', () => {
    expect(viewFilter(DEFAULT_VIEW)).toEqual({});
    const filter = viewFilter({ ...view, q: '  PT Uji/April ' });
    expect(filter).toMatchObject({ q: 'PT Uji/April', status: 'done', outcome: 'check', mode: 'full_page', mine: true });
    const from = new Date(filter.from!);
    const to = new Date(filter.to!);
    expect([dayOf(from), from.getHours(), from.getMinutes()]).toEqual(['2026-11-01', 0, 0]);
    expect([dayOf(to), to.getHours(), to.getMinutes()]).toEqual(['2026-11-07', 0, 0]); // the 6th is included
    expect(narrows(DEFAULT_VIEW)).toBe(false);
    expect(narrows({ ...DEFAULT_VIEW, page: 4, size: 100 })).toBe(false);
    expect(narrows({ ...DEFAULT_VIEW, mine: true })).toBe(true);
  });

  test('the pager shows the first and last page and the pages around the current one', () => {
    expect(pageList(1, 1)).toEqual([1]);
    expect(pageList(1, 10)).toEqual([1, 2, null, 10]);
    expect(pageList(4, 10)).toEqual([1, 2, 3, 4, 5, null, 10]);
    expect(pageList(6, 10)).toEqual([1, null, 5, 6, 7, null, 10]);
    expect(pageList(10, 10)).toEqual([1, null, 9, 10]);
    expect(pageList(2, 3)).toEqual([1, 2, 3]);
  });
});

describe('users page', () => {
  // fictitious test users
  const user = (id: number, name: string, extra: Partial<ManagedUser> = {}): ManagedUser => ({
    id,
    email: `${name.toLowerCase().replace(/ /g, '.')}@gdn-commerce.com`,
    name,
    role: 'reviewer',
    active: false,
    createdAt: '2026-11-16T01:00:00.000Z',
    lastLoginAt: null,
    adminFromConfig: false,
    ...extra,
  });
  const list = [
    user(1, 'Penguji Admin', { role: 'admin', active: true, adminFromConfig: true }),
    user(2, 'Penguji Dua', { active: true }),
    user(3, 'Penguji Tiga'),
    user(4, 'Rina Uji', { role: 'admin', active: true }),
  ];

  test('searches name and email in any case, and filters by status and role', () => {
    expect(filterUsers(list, NO_FILTER)).toHaveLength(4);
    expect(filterUsers(list, { ...NO_FILTER, q: '  PENGUJI ' }).map((u) => u.id)).toEqual([1, 2, 3]);
    expect(filterUsers(list, { ...NO_FILTER, q: 'rina.uji@' }).map((u) => u.id)).toEqual([4]);
    expect(filterUsers(list, { ...NO_FILTER, status: 'inactive' }).map((u) => u.id)).toEqual([3]);
    expect(filterUsers(list, { ...NO_FILTER, status: 'active', role: 'reviewer' }).map((u) => u.id)).toEqual([2]);
    expect(filterUsers(list, { q: 'tiga', status: 'active', role: 'all' })).toEqual([]);
  });

  test('counts users, active, inactive and admins', () => {
    expect(userCounts(list)).toEqual({ total: 4, active: 3, inactive: 1, admins: 2 });
    expect(userCounts([])).toEqual({ total: 0, active: 0, inactive: 0, admins: 0 });
  });

  test("locks the admin's own row and ADMIN_EMAILS admins, nothing else", () => {
    expect(lockReason(list[3]!, 4)).toContain('sendiri');
    expect(lockReason(list[0]!, 4)).toContain('ADMIN_EMAILS');
    expect(lockReason(list[1]!, 4)).toBeNull();
    expect(lockReason(list[3]!, 1)).toBeNull();
  });

  test('asks before a role change or deactivation, not before activation', () => {
    expect(changeQuestion(list[2]!, { active: true })).toBeNull();
    expect(changeQuestion(list[1]!, { active: false })?.title).toBe('Nonaktifkan user?');
    expect(changeQuestion(list[1]!, { role: 'admin' })?.body).toContain('Penguji Dua');
    expect(changeQuestion(list[3]!, { role: 'reviewer' })?.confirm).toBe('Ya, jadikan reviewer');
  });

  test('labels the new error codes', () => {
    for (const code of ['account_inactive', 'admin_only', 'cannot_change_self', 'admin_from_config', 'user_not_found']) {
      expect(errorLabel(code)).not.toBe(code);
    }
  });
});
