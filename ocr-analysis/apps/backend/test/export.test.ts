import { describe, expect, test } from 'bun:test';
import { join } from 'node:path';
import { type ContractRow, type Job, type JobFile, noteText } from '@contract-ocr/shared-types';
import { COLUMNS, type WorkbookCell, exportFileName, vendorOf, workbookSpec } from '../src/modules/jobs/export.ts';
import { type RowFacts, fieldsOf, tanggal, wholeMonths } from '../src/modules/jobs/fields.ts';
import { WorkbookError, processWorkbookWriter } from '../src/modules/jobs/workbook.ts';

const NIK = '3199990101900001'; // fictitious: regency and district 99 exist nowhere
const col = (header: string) => COLUMNS.findIndex((c) => c.header === header);

function contractRow(overrides: Partial<RowFacts> = {}): ContractRow {
  const facts: RowFacts = {
    file: 'Budi Santoso - 1jan-31mar26 - SNA.pdf',
    reader: 'pattern',
    readByFallback: false,
    claudeCalls: [],
    tokens: null,
    vendorFromFolder: 'IMECON',
    vendorInDocument: true,
    vendorBasis: 'text',
    vendorFromDocument: null,
    entityFromFilename: 'SNA',
    nameFromFilename: 'Budi Santoso',
    nameFromDocument: null,
    nameInDocument: true,
    nameMatch: null,
    readConfidence: { name: null, period: null, nik: null },
    nik: { status: 'found', masked: '************0001', birthDate: 'match', gender: 'match', fromOcr: false, claude: null },
    position: { status: 'found', value: 'Kasir', confidence: 1, agreement: null, source: 'pattern' },
    filenameStandard: true,
    periodFromFilename: { start: '2026-01-01', end: '2026-03-31' },
    periodFromDocument: { start: '2026-01-01', end: '2026-03-31' },
    documentPeriod: { status: 'found', start: '2026-01-01', end: '2026-03-31', durationMonths: 3 },
    periodMatches: true,
    signature: 'signed',
    signatureByClaude: null,
    decision: 'review',
    reasons: [],
    detail: { identityPages: [1], signaturePage: 2, pageKinds: {}, ocrConfidence: {}, signatureSource: null, vendorNote: null },
    ...overrides,
  };
  return { ...facts, fields: fieldsOf(facts) };
}

function file(
  path: string,
  row: ContractRow | null = contractRow({ vendorFromFolder: vendorOf(path) || null }),
  extra: Partial<JobFile> = {},
): JobFile {
  const at = path.lastIndexOf('/');
  return {
    path,
    folder: at < 0 ? '' : path.slice(0, at),
    name: path.slice(at + 1),
    state: row ? 'done' : 'error',
    ...(row ? { row } : { error: 'timeout' }),
    ...extra,
  };
}

function job(...files: JobFile[]): Job {
  return { id: 'j', folders: [], claude: false, mode: 'ocr', status: 'done', startedAt: '2026-11-02T08:00:00Z', finishedAt: null, files, startedBy: null };
}

/** The ALL sheet's cells of the first file, by column header. */
function cells(row: ContractRow): Record<string, WorkbookCell> {
  const [first] = workbookSpec(job(file('IMECON/Jan 2026 - Mar 2026/x.pdf', row))).sheets[0]?.rows ?? [];
  return Object.fromEntries(COLUMNS.map((c, i) => [c.header, first?.[i] ?? { value: 'missing' }]));
}

describe('data output workbook', () => {
  test('has the agreed columns, an ALL sheet, then one sheet per vendor folder', () => {
    expect(COLUMNS.map((c) => c.header)).toEqual([
      'NIK KTP', 'NAMA VENDOR', 'ENTITAS', 'NAMA KARYAWAN', 'POSISI', 'TANGGAL JOIN AWAL',
      'START PERIODE', 'END PERIODE', 'MONTH', 'SIGN', 'FILE',
    ]);
    const spec = workbookSpec(
      job(file('IMECON/Jan/a.pdf'), file('Bravo/Feb/b.pdf'), file('IMECON/Mar/c.pdf'), file('lepas.pdf')),
    );
    expect(spec.sheets.map((s) => [s.name, s.rows.length])).toEqual([
      ['ALL', 4],
      ['Bravo', 1],
      ['IMECON', 2],
      ['Tanpa Vendor', 1],
    ]);
    expect(spec.sheets[2]?.rows.map((r) => r[col('FILE')]?.value)).toEqual(['IMECON/Jan/a.pdf', 'IMECON/Mar/c.pdf']);
    expect(spec.sheets[3]?.rows[0]?.[col('NAMA VENDOR')]?.check).toBeTruthy();
  });

  test('sheet names are valid in Excel and unique', () => {
    const long = 'PT Sangat Panjang Sekali Namanya Untuk Lembar Excel';
    const spec = workbookSpec(
      job(file('A:B/x.pdf'), file('A?B/x.pdf'), file(`${long}/x.pdf`), file(`${long} Dua/x.pdf`), file('all/x.pdf')),
    );
    const names = spec.sheets.map((s) => s.name);
    expect(names.every((n) => n.length <= 31 && !/[[\]:*?/\\]/.test(n))).toBe(true);
    expect(new Set(names.map((n) => n.toLowerCase())).size).toBe(names.length);
    expect(names).toContain('A B (2)');
    expect(names).toContain('all (2)');
  });

  test('a clean row fills every column it can and colours nothing', () => {
    const row = cells(contractRow());
    expect(Object.fromEntries(Object.entries(row).map(([k, c]) => [k, c.value]))).toEqual({
      'NIK KTP': '************0001',
      'NAMA VENDOR': 'IMECON',
      ENTITAS: 'SNA',
      'NAMA KARYAWAN': 'Budi Santoso',
      POSISI: 'Kasir',
      'TANGGAL JOIN AWAL': null,
      'START PERIODE': '2026-01-01',
      'END PERIODE': '2026-03-31',
      MONTH: 3,
      SIGN: 'YES',
      FILE: 'IMECON/Jan 2026 - Mar 2026/x.pdf',
    });
    expect(Object.values(row).filter((c) => c.check)).toEqual([]);
  });

  test('NAMA VENDOR is the vendor folder, coloured when the document does not show it', () => {
    expect(cells(contractRow())['NAMA VENDOR']).toEqual({ value: 'IMECON' });
    const missing = cells(contractRow({ vendorInDocument: false }))['NAMA VENDOR'];
    expect(missing).toEqual({ value: 'IMECON', check: 'Nama folder vendor tidak ditemukan di dokumen' });
    const other = cells(contractRow({ vendorInDocument: false, vendorBasis: 'claude', vendorFromDocument: 'PT Lain Jaya' }));
    expect(other['NAMA VENDOR']?.check).toContain('PT Lain Jaya');
    expect(cells(contractRow({ vendorInDocument: null }))['NAMA VENDOR']?.check).toBe('Belum dicocokkan dengan dokumen');
  });

  test('ENTITAS comes from the filename, and is empty and coloured when the filename has none', () => {
    const missing = cells(contractRow({ entityFromFilename: null })).ENTITAS;
    expect(missing?.value).toBeNull();
    expect(missing?.check).toContain('Nama - Masa Kontrak - ENTITAS');
  });

  test('carries the full NIK the backend holds, and the masked one only when it holds none', () => {
    const files = [file('IMECON/a.pdf'), file('IMECON/b.pdf')];
    const [a, b] = workbookSpec(job(...files), new Map([['IMECON/a.pdf', NIK]])).sheets[0]?.rows ?? [];
    expect(a?.[col('NIK KTP')]).toEqual({ value: NIK });
    expect(b?.[col('NIK KTP')]).toEqual({ value: '************0001' });
  });

  test('a doubtful full NIK keeps its value and its colour', () => {
    const row = contractRow({ nik: { ...contractRow().nik, fromOcr: true, gender: 'absent' } });
    const [first] = workbookSpec(job(file('IMECON/a.pdf', row)), new Map([['IMECON/a.pdf', NIK]])).sheets[0]?.rows ?? [];
    expect(first?.[col('NIK KTP')]).toEqual({ value: NIK, check: expect.any(String) });
  });

  test.each([
    [{ status: 'conflict', masked: null }, null],
    [{ status: 'invalid', masked: null }, null],
    [{ status: 'missing', masked: null }, null],
    [{ birthDate: 'mismatch' }, '************0001'],
    [{ fromOcr: true, gender: 'absent' }, '************0001'],
  ] as const)('a doubtful NIK %o is coloured', (nik, value) => {
    const cell = cells(contractRow({ nik: { ...contractRow().nik, ...nik } }))['NIK KTP'];
    expect(cell?.value).toBe(value);
    expect(cell?.check).toBeTruthy();
  });

  test('a NIK read from a scan whose birth date and gender both agree is not coloured', () => {
    expect(cells(contractRow({ nik: { ...contractRow().nik, fromOcr: true } }))['NIK KTP']?.check).toBeUndefined();
  });

  test('a NIK picked out of more than 16 digits is coloured even when both checks agree', () => {
    const row = contractRow({ nik: { ...contractRow().nik, fromOcr: true }, reasons: ['nik_digits_repaired'] });
    expect(cells(row)['NIK KTP']).toEqual({
      value: '************0001',
      check: 'Terbaca lebih dari 16 angka di baris NIK; cocokkan angkanya',
    });
  });

  test.each([
    [{ status: 'conflict', value: null, confidence: null, agreement: null, source: null }, null],
    [{ status: 'missing', value: null, confidence: null, agreement: null, source: null }, null],
    [{ status: 'found', value: 'Kasir', confidence: 0.55, agreement: null, source: 'pattern' }, 'Kasir'],
    [{ status: 'found', value: 'Kasir', confidence: 0.95, agreement: 'different', source: 'claude' }, 'Kasir'],
  ] as const)('a doubtful position %o is coloured', (position, value) => {
    const cell = cells(contractRow({ position }))['POSISI'];
    expect(cell?.value).toBe(value);
    expect(cell?.check).toBeTruthy();
  });

  test('a name from a filename off the standard is kept but coloured', () => {
    const cell = cells(contractRow({ filenameStandard: false }))['NAMA KARYAWAN'];
    expect(cell).toEqual({ value: 'Budi Santoso', check: expect.any(String) });
    expect(cells(contractRow({ nameFromFilename: null }))['NAMA KARYAWAN']?.value).toBeNull();
  });

  test('a name the document does not print, or that was never compared with it, is kept but coloured', () => {
    expect(cells(contractRow({ nameInDocument: false }))['NAMA KARYAWAN']).toEqual({
      value: 'Budi Santoso',
      check: expect.stringContaining('tidak ada di halaman identitas'),
    });
    expect(cells(contractRow({ nameInDocument: null }))['NAMA KARYAWAN']).toEqual({
      value: 'Budi Santoso',
      check: expect.stringContaining('Belum dicocokkan'),
    });
  });

  test('a period missing from the document falls back to the filename, coloured', () => {
    const row = cells(
      contractRow({ documentPeriod: { status: 'partial', start: '2026-01-01', end: null, durationMonths: null } }),
    );
    expect(row['START PERIODE']).toEqual({ value: '2026-01-01' });
    expect(row['END PERIODE']).toEqual({ value: '2026-03-31', check: expect.stringContaining('nama file') });
    expect(row.MONTH).toEqual({ value: 3, check: expect.any(String) });
  });

  test('a document date that differs from the filename is kept and coloured', () => {
    const row = cells(
      contractRow({ documentPeriod: { status: 'found', start: '2026-01-01', end: '2026-06-30', durationMonths: null } }),
    );
    expect(row['END PERIODE']).toEqual({ value: '2026-06-30', check: 'Nama file menyebut 31 Mar 2026' });
    expect(row.MONTH?.value).toBe(6);
  });

  test('a document date with no filename date to compare it with is kept and coloured', () => {
    const row = cells(contractRow({ periodFromFilename: null, filenameStandard: false }));
    expect(row['START PERIODE']).toEqual({ value: '2026-01-01', check: expect.stringContaining('Hanya dari dokumen') });
  });

  test('conflicting dates leave the cells empty and coloured', () => {
    const row = cells(
      contractRow({ documentPeriod: { status: 'conflict', start: null, end: null, durationMonths: null } }),
    );
    for (const header of ['START PERIODE', 'END PERIODE', 'MONTH']) {
      expect(row[header]?.value).toBeNull();
      expect(row[header]?.check).toBeTruthy();
    }
  });

  test('a stated length that disagrees with the dates is coloured', () => {
    const row = cells(
      contractRow({ documentPeriod: { status: 'found', start: '2026-01-01', end: '2026-03-31', durationMonths: 6 } }),
    );
    expect(row.MONTH).toEqual({ value: 6, check: expect.stringContaining('3 bulan') });
  });

  test.each([
    ['signed', 'YES', false],
    ['unsigned', 'NO', false],
    ['uncertain', null, true],
    [null, null, true],
  ] as const)('signature %s is %s', (signature, value, checked) => {
    const cell = cells(contractRow({ signature }))['SIGN'];
    expect(cell?.value).toBe(value);
    expect(Boolean(cell?.check)).toBe(checked);
  });

  test('a signature read without a clear margin keeps its value but is coloured with its page', () => {
    expect(cells(contractRow({ reasons: ['signature_not_clear'] }))['SIGN']).toEqual({
      value: 'YES',
      check: 'Kurang jelas; lihat sekilas halaman 2',
    });
  });

  test('a signature read on a guessed page keeps its value but is coloured, even when Claude decided it', () => {
    expect(cells(contractRow({ reasons: ['signature_page_guessed', 'signature_from_llm'] }))['SIGN']).toEqual({
      value: 'YES',
      check: 'Halaman tanda tangan tidak dikenali; dibaca dari halaman 2, cek halaman itu',
    });
  });

  test('a file that failed keeps its vendor and path, and colours the data it could not give', () => {
    const [failed] = workbookSpec(job(file('IMECON/Jan/x.pdf', null))).sheets[0]?.rows ?? [];
    expect(failed?.[col('NAMA VENDOR')]).toEqual({ value: 'IMECON' });
    expect(failed?.[col('FILE')]).toEqual({ value: 'IMECON/Jan/x.pdf' });
    expect(failed?.[col('NAMA KARYAWAN')]).toEqual({ value: null, check: expect.stringContaining('waktu baca habis') });
    expect(failed?.[col('ENTITAS')]).toEqual({ value: null });
  });

  test.each([
    ['2026-01-01', '2026-03-31', 3],
    ['2026-01-01', '2026-12-31', 12],
    ['2026-01-15', '2026-07-14', 6],
    ['2026-01-01', '2026-04-01', 3],
    ['2026-01-31', '2026-02-27', 1],
    ['2026-01-01', '2026-03-15', null],
    ['2026-03-31', '2026-01-01', null],
  ] as const)('%s to %s is %p whole months', (start, end, months) => {
    expect(wholeMonths(start, end)).toBe(months);
  });
});

describe('fields shown on the page', () => {
  test('are the Excel row: same values and the same notes, the NIK masked', () => {
    const row = contractRow({ nik: { ...contractRow().nik, fromOcr: true, gender: 'absent' }, signature: 'uncertain' });
    const [first] = workbookSpec(job(file('IMECON/a.pdf', row)), new Map([['IMECON/a.pdf', NIK]])).sheets[0]?.rows ?? [];
    const pairs = [
      ['nik', 'NIK KTP'],
      ['name', 'NAMA KARYAWAN'],
      ['position', 'POSISI'],
      ['start', 'START PERIODE'],
      ['end', 'END PERIODE'],
      ['months', 'MONTH'],
      ['signature', 'SIGN'],
    ] as const;
    // the Excel carries the page's note in Indonesian
    for (const [key, header] of pairs) {
      const check = row.fields[key].check;
      expect(first?.[col(header)]?.check ?? null).toBe(check && noteText(check, 'id'));
    }
    expect(row.fields.nik.value).toBe('************0001');
    expect(first?.[col('NIK KTP')]?.value).toBe(NIK);
  });

  test('a value without a check says why it can be trusted', () => {
    const { fields } = contractRow();
    for (const field of Object.values(fields)) {
      expect(field.check).toBeNull();
      expect(field.basis).toBeTruthy();
    }
    expect(noteText(fields.start.basis!, 'id')).toBe('Sama di dokumen dan di nama file');
  });

  test('dates in notes are written as people write them', () => {
    expect(tanggal('2026-03-31')).toBe('31 Mar 2026');
    expect(tanggal('2037-08-01')).toBe('1 Agu 2037');
  });
});

describe('download name', () => {
  const at = (folders: string[], ...paths: string[]): Job => ({
    ...job(...paths.map((p) => file(p))),
    folders,
    finishedAt: '2026-11-02T06:45:00Z', // 13.45 WIB
  });

  test.each([
    [['PT Bumi Dinamika/Februari 2032 - Mei 2032'], ['PT Bumi Dinamika/Februari 2032 - Mei 2032/a.pdf'], 'PT Bumi Dinamika - Februari 2032 - Mei 2032'],
    [['PT Bina Semesta'], ['PT Bina Semesta/Jan/a.pdf'], 'PT Bina Semesta'],
    [['PT Bina Semesta/Jan', 'PT Bina Semesta/Feb'], ['PT Bina Semesta/Jan/a.pdf', 'PT Bina Semesta/Feb/b.pdf'], 'PT Bina Semesta (2 folder)'],
    [['PT A', 'PT B/Jan'], ['PT A/x/a.pdf', 'PT B/Jan/b.pdf'], '2 vendor'],
    [[''], ['PT A/x/a.pdf'], 'Semua vendor'],
  ])('%p is named after what it covered, in WIB', (folders, paths, what) => {
    expect(exportFileName(at(folders, ...paths))).toBe(`Hasil OCR PKWT - ${what} - 2 Nov 2026 13.45.xlsx`);
  });

  test('characters Windows refuses never reach the name, and a long scope is cut', () => {
    expect(exportFileName(at(['PT A:B?/Jan*'], 'PT A:B?/Jan*/a.pdf'))).toBe('Hasil OCR PKWT - PT A B - Jan - 2 Nov 2026 13.45.xlsx');
    expect(exportFileName(at(['PT A\\B'], 'x/a.pdf'))).toBe('Hasil OCR PKWT - PT A B - 2 Nov 2026 13.45.xlsx');
    expect(exportFileName(at(['x'.repeat(300)], 'x/a.pdf')).length).toBeLessThan(130);
  });

  test('falls back to the start time while no finish time is known', () => {
    expect(exportFileName({ ...at(['PT A'], 'PT A/a.pdf'), finishedAt: null, startedAt: '2026-01-31T17:30:00Z' })).toBe(
      'Hasil OCR PKWT - PT A - 1 Feb 2026 00.30.xlsx',
    );
  });
});

describe('workbook writer process', () => {
  const FAKE = [process.execPath, join(import.meta.dir, 'fixtures', 'fake-xlsx.ts')];
  const spec = workbookSpec(job(file('IMECON/x.pdf')));

  test('sends the spec on stdin and returns stdout', async () => {
    const bytes = await processWorkbookWriter(FAKE, 10_000)(spec);
    expect(new TextDecoder().decode(bytes)).toBe('xlsx:ALL|IMECON');
  });

  test('a writer that fails or hangs is reported, not returned', async () => {
    await expect(processWorkbookWriter([...FAKE, 'fail'], 10_000)(spec)).rejects.toEqual(new WorkbookError('failed'));
    await expect(processWorkbookWriter([...FAKE, 'slow'], 500)(spec)).rejects.toEqual(new WorkbookError('timeout'));
  });
});
