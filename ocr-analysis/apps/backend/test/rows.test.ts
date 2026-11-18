import { describe, expect, test } from 'bun:test';
import type { ExtractorFileResult, ParsedFilename } from '../src/modules/workers/protocol.ts';
import { maskNik, toRow as readRow } from '../src/modules/jobs/rows.ts';
import { inIndonesian } from './fixtures/notes.ts';

// The rows' notes are keys the page renders in the reader's language; these tests read them in Indonesian, the wording
// HR knows and the Excel carries.
const toRow = (...args: Parameters<typeof readRow>) => inIndonesian(readRow(...args));

const FILENAME: ParsedFilename = { name: 'Budi', start: '2045-04-16', end: '2047-04-15', duration_months: null, flags: [] };

function result(periode_regex: ExtractorFileResult['log']['periode_regex']): ExtractorFileResult {
  return {
    file: 'Budi - 16apr45-15apr47.pdf',
    status_ttd: 'signed',
    keputusan: 'review_priority',
    flags: [],
    catatan_vendor: null,
    log: { p_id: [1], p_ttd: 2, jenis_halaman: {}, ocr_conf: {}, periode_regex, tanda_tangan: null, alasan: [] },
  };
}

const regex = (start: string | null, end: string | null, durasi_bulan: number | null = null, ambigu = false) => ({
  tanggal_mulai: start,
  tanggal_selesai: end,
  durasi_bulan,
  ambigu,
});

describe('document period', () => {
  test.each([
    [regex('2045-04-16', '2047-04-15', 24), 'found', true],
    [regex('2045-04-16', null, null, true), 'conflict', null],
    [regex('2045-04-16', '2047-04-15', null, true), 'conflict', true], // dates kept, but a length conflicted
    [regex('2047-04-15', null, 24), 'partial', null],
    [regex(null, null, 24), 'partial', null],
    [regex(null, null), 'missing', null],
    [null, 'missing', null],
  ] as const)('%o is %s', (periode, status, matches) => {
    const row = toRow(result(periode), FILENAME);
    expect(row.documentPeriod.status).toBe(status);
    expect(row.periodMatches).toBe(matches);
  });

  test('keeps a lone date and the length so the page can show them', () => {
    expect(toRow(result(regex('2047-04-15', null, 24)), FILENAME).documentPeriod).toEqual({
      status: 'partial',
      start: '2047-04-15',
      end: null,
      durationMonths: 24,
    });
  });
});

describe('NIK', () => {
  const NIK = '3199990101900001'; // fictitious: regency and district 99 exist nowhere
  const withNik = (nik: unknown, log: ExtractorFileResult['log']['nik'], alasan: string[] = []) => {
    const base = result(null);
    return { ...base, nik, log: { ...base.log, nik: log, alasan } } as ExtractorFileResult;
  };
  const found = { status: 'ditemukan', tanggal_lahir: 'cocok', jenis_kelamin: 'tidak_cocok' } as const;

  test('a row shows only the last four digits and the checks', () => {
    const row = toRow(withNik(NIK, found, ['nik_from_ocr']), FILENAME);
    expect(row.nik).toEqual({
      status: 'found',
      masked: '************0001',
      birthDate: 'match',
      gender: 'mismatch',
      fromOcr: true,
      claude: null,
      secondReader: null,
    });
    expect(JSON.stringify(row)).not.toContain(NIK);
    expect(maskNik(NIK)).toBe('************0001');
  });

  test.each([
    ['not 16 digits', 'abc', found, 'invalid'],
    ['a 17-digit number', `${NIK}9`, found, 'invalid'],
    ['conflicting NIKs', null, { status: 'bertentangan', tanggal_lahir: 'tidak_ada', jenis_kelamin: 'tidak_ada' }, 'conflict'],
    ['no NIK log from an older worker', null, undefined, 'missing'],
  ] as const)('%s', (_, nik, log, status) => {
    const row = toRow(withNik(nik, log), FILENAME);
    expect([row.nik.status, row.nik.masked]).toEqual([status, null]);
  });
});

describe('name in document', () => {
  test.each([
    [{ nama: 'cocok' }, true],
    [{ nama: 'mirip' }, true],
    [{ nama: 'tidak_cocok' }, false],
    [{}, null],
    [undefined, null],
  ] as const)('%o is %p', (validasi, expected) => {
    const base = result(null);
    const row = toRow({ ...base, log: { ...base.log, validasi_nama_file: validasi } } as ExtractorFileResult, FILENAME);
    expect(row.nameInDocument).toBe(expected);
    expect(row.nameOnSignaturePage).toBe(false);
  });

  test('a name confirmed in the employee column of the signature page says so', () => {
    const base = result(null);
    const validasi = { nama: 'cocok', periode: 'cocok', nama_halaman: 'tanda_tangan' } as const;
    const row = toRow({ ...base, log: { ...base.log, validasi_nama_file: validasi } } as ExtractorFileResult, FILENAME);
    expect([row.nameInDocument, row.nameOnSignaturePage]).toEqual([true, true]);
    expect(row.fields.name).toEqual({
      value: row.nameFromFilename,
      check: null,
      basis: 'Nama dari nama file tercetak di kolom karyawan halaman tanda tangan',
    });
  });
});

describe('position', () => {
  const withPosition = (posisi: unknown, status?: string, confidence?: number) => {
    const base = result(null);
    return {
      ...base,
      posisi,
      confidence: confidence === undefined ? {} : { posisi: confidence },
      log: { ...base.log, posisi: status ? { status, sumber: null } : undefined },
    } as ExtractorFileResult;
  };

  test('a title the extractor found comes with its OCR confidence', () => {
    expect(toRow(withPosition(' Staff Gudang ', 'ditemukan', 0.72), FILENAME).position).toEqual({
      status: 'found',
      value: 'Staff Gudang',
      confidence: 0.72,
      agreement: null,
      fromUnclearScan: false,
      fixedByClaude: false,
      source: 'pattern',
      secondReader: null,
    });
  });

  test.each([
    ['conflicting titles', null, 'bertentangan', 'conflict'],
    ['nothing found', null, 'tidak_ditemukan', 'missing'],
    ['no position log from an older worker', undefined, undefined, 'missing'],
    ['a value too long to be a title', 'x'.repeat(61), 'ditemukan', 'missing'],
  ] as const)('%s', (_, posisi, status, expected) => {
    expect(toRow(withPosition(posisi, status), FILENAME).position).toEqual({
      status: expected,
      value: null,
      confidence: null,
      agreement: null,
      source: null,
    });
  });
});

describe('second local reader', () => {
  const NIK = '3199990101900001'; // fictitious: regency and district 99 exist nowhere
  const scanned = (pembaca_kedua: 'sama' | 'beda' | 'tidak_terbaca', alasan: string[]) => {
    const base = result(null);
    const nik = { status: 'ditemukan', tanggal_lahir: 'cocok', jenis_kelamin: 'tidak_ada', pembaca_kedua } as const;
    return { ...base, nik: NIK, log: { ...base.log, nik, alasan } } as ExtractorFileResult;
  };

  test('a scanned NIK both engines read the same is trusted', () => {
    const row = toRow(scanned('sama', []), FILENAME); // the extractor drops nik_from_ocr once both engines agree
    expect(row.nik.secondReader).toBe('same');
    expect(row.fields.nik).toEqual({
      value: '************0001',
      check: null,
      basis: 'Dua mesin OCR lokal (Tesseract dan RapidOCR) membaca angka yang sama dari scan',
    });
  });

  test('other digits from the second engine keep the NIK coloured, and say so', () => {
    const row = toRow(scanned('beda', ['nik_from_ocr']), FILENAME);
    expect(row.nik.secondReader).toBe('different');
    expect(row.fields.nik.check).toBe('Dibaca dari scan; mesin OCR kedua membaca lain, cocokkan angkanya');
    expect(toRow(scanned('tidak_terbaca', ['nik_from_ocr']), FILENAME).fields.nik.check).toBe(
      'Dibaca dari scan; cocokkan angkanya',
    );
  });

  const title = (pembaca_kedua: 'sama' | 'beda' | undefined) => {
    const base = result(null);
    const posisi = { status: 'ditemukan', sumber: 'label', pembaca_kedua } as const;
    return { ...base, posisi: 'Staff Gudang', confidence: { posisi: 0.62 }, log: { ...base.log, posisi } } as ExtractorFileResult;
  };

  test('an unclear title both engines read the same is trusted; another reading keeps it coloured', () => {
    const same = toRow(title('sama'), FILENAME);
    expect(same.position.secondReader).toBe('same');
    expect(same.fields.position).toEqual({
      value: 'Staff Gudang',
      check: null,
      basis: 'Dua mesin OCR lokal (Tesseract dan RapidOCR) membaca posisi yang sama',
    });
    expect(toRow(title('beda'), FILENAME).fields.position.check).toBe(
      'Dibaca dari scan yang kurang jelas; mesin OCR kedua membaca lain, cocokkan angkanya',
    );
    expect(toRow(title(undefined), FILENAME).fields.position.check).toBe('Dibaca dari scan yang kurang jelas');
  });
});

describe('entity', () => {
  test('is the trimmed text after the period in the filename', () => {
    const row = toRow(result(regex('2045-04-16', '2047-04-15')), { ...FILENAME, entity: ' PT Sinar Niaga ' });
    expect(row.entityFromFilename).toBe('PT Sinar Niaga');
    expect(row.fields.entity).toEqual({ value: 'PT Sinar Niaga', check: null, basis: 'Dari nama file' });
  });

  test('is empty and checked when the filename has none, or the worker predates the field', () => {
    for (const filename of [{ ...FILENAME, entity: null }, FILENAME]) {
      const row = toRow(result(regex('2045-04-16', '2047-04-15')), filename);
      expect(row.entityFromFilename).toBeNull();
      expect(row.fields.entity.value).toBeNull();
      expect(row.fields.entity.check).toBeTruthy();
    }
  });
});

describe('read by Claude', () => {
  const STANDARD: ParsedFilename = { name: 'Budi Santoso', start: '2045-04-16', end: '2047-04-15', duration_months: null, flags: [] };

  function claude(changes: Partial<ExtractorFileResult> = {}, log: Partial<ExtractorFileResult['log']> = {}) {
    const base = result(regex('2045-04-01', '2047-04-15'));
    return {
      ...base,
      nama_karyawan: 'BUDI SANTOSO',
      confidence: { nama: 0.97, periode: 0.95 },
      ...changes,
      log: {
        ...base.log,
        pembaca: 'llm',
        periode_llm: { tanggal_mulai: '2045-04-16', tanggal_selesai: '2047-04-15', durasi_bulan: 24 },
        validasi_nama_file: { nama: 'cocok', periode: 'cocok' },
        ...log,
      },
    } as ExtractorFileResult;
  }

  test('the name and period are Claude\'s, not the regex\'s, and agree with the filename', () => {
    const row = toRow(claude(), STANDARD);
    expect(row.reader).toBe('claude');
    // Claude confirms the filename's name belongs to the employee; the filename's spelling goes to the Excel.
    expect(row.nameFromDocument).toBe('BUDI SANTOSO');
    expect(row.fields.name).toEqual({
      value: 'Budi Santoso',
      check: null,
      basis: 'Sama dengan nama karyawan yang dibaca Claude dari dokumen',
    });
    expect(row.periodFromDocument).toEqual({ start: '2045-04-16', end: '2047-04-15' });
    expect(row.fields.start.check).toBeNull();
    expect(row.fields.months).toMatchObject({ value: 24, check: null });
  });

  test('a name the filename does not share, or Claude is unsure of, is coloured', () => {
    const other = toRow(claude({}, { validasi_nama_file: { nama: 'tidak_cocok' } }), STANDARD);
    expect(other.fields.name).toMatchObject({ value: 'BUDI SANTOSO', check: 'Nama file menyebut "Budi Santoso"' });
    const similar = toRow(claude({ nama_karyawan: 'Budi Sant0so' }, { validasi_nama_file: { nama: 'mirip' } }), STANDARD);
    expect(similar.fields.name).toMatchObject({ value: 'Budi Santoso', check: 'Claude membaca "Budi Sant0so" di dokumen' });
    const unsure = toRow(claude({ confidence: { nama: 0.6, periode: 0.6 } }), STANDARD);
    expect(unsure.fields.name.check).toBe('Claude kurang yakin membaca nama karyawan');
    expect(unsure.fields.start).toMatchObject({ value: '2045-04-16', check: 'Claude kurang yakin membaca tanggal ini' });
    const nonstandard = toRow(claude(), { ...STANDARD, start: null, end: null, flags: ['filename_nonstandard'] });
    expect(nonstandard.fields.name.check).toContain('nama file tidak berformat');
  });

  test('without a verified name from Claude the filename\'s name is used, as with the patterns', () => {
    const row = toRow(claude({ nama_karyawan: null }), STANDARD);
    expect(row.nameFromDocument).toBeNull();
    expect(row.fields.name).toMatchObject({ value: 'Budi Santoso', check: null });
  });

  test('a file Claude did not read (an error, or OCR too poor to send) keeps the pattern reading', () => {
    const row = toRow(claude({}, { pembaca: 'pola' }), STANDARD);
    expect(row.reader).toBe('pattern');
    expect(row.nameFromDocument).toBeNull();
    expect(row.periodFromDocument).toEqual({ start: '2045-04-01', end: '2047-04-15' }); // the regex's
  });

  test('a title Claude and the patterns read differently is coloured', () => {
    const row = toRow(
      claude({ posisi: 'Staff Gudang', confidence: { posisi: 0.95 } }, { posisi: { status: 'ditemukan', sumber: 'label', cocok_llm: false } }),
      STANDARD,
    );
    expect(row.position).toEqual({
      status: 'found',
      value: 'Staff Gudang',
      confidence: 0.95,
      agreement: 'different',
      fromUnclearScan: false,
      fixedByClaude: false,
      source: 'claude',
      secondReader: null,
    });
    expect(row.fields.position.check).toBe('Pembacaan pola teks berbeda; cek di dokumen');
  });

  test('Claude agreeing with a title from an unclear scan does not make it sure', () => {
    // Claude reads the same OCR text, so its agreement repeats the misread (2026-11-11: `Cleaning Service 1`)
    const agreed = (ocr_ragu: boolean | undefined) =>
      toRow(
        claude(
          { posisi: 'Cleaning Service 1', confidence: { posisi: 0.95 } },
          { posisi: { status: 'ditemukan', sumber: 'label', cocok_llm: true, ocr_ragu } },
        ),
        STANDARD,
      );
    const echoed = agreed(true);
    expect(echoed.position).toMatchObject({ agreement: 'same', fromUnclearScan: true, source: 'claude' });
    expect(echoed.fields.position).toEqual({
      value: 'Cleaning Service 1',
      check: 'Dibaca dari scan yang kurang jelas; Claude membaca teks OCR yang sama, cocokkan di dokumen',
      basis: null,
    });
    const confirmed = { check: null, basis: 'Dibaca Claude dan sama dengan pembacaan pola teks' };
    expect(agreed(false).fields.position).toMatchObject(confirmed); // a clear local reading Claude confirms
    expect(agreed(undefined).fields.position).toMatchObject(confirmed); // results saved before 2026-11-11
  });

  test('a title Claude fixed for an OCR misread is coloured', () => {
    const fixed = (koreksi_llm: boolean | undefined) =>
      toRow(
        claude(
          { posisi: 'Kurir', confidence: { posisi: 0.9 } },
          { posisi: { status: 'ditemukan', sumber: 'label', cocok_llm: false, koreksi_llm } },
        ),
        STANDARD,
      );
    const row = fixed(true);
    expect(row.position).toMatchObject({ value: 'Kurir', fixedByClaude: true, source: 'claude' });
    expect(row.fields.position).toEqual({
      value: 'Kurir',
      check: 'Claude memperbaiki salah baca OCR pada jabatan ini; cocokkan di dokumen',
      basis: null,
    });
    expect(fixed(false).position.fixedByClaude).toBe(false);
    expect(fixed(undefined).fields.position.check).toBe('Pembacaan pola teks berbeda; cek di dokumen');
  });
});

describe('pattern checks and Claude helping', () => {
  const withLog = (log: Partial<ExtractorFileResult['log']>, extra: Partial<ExtractorFileResult> = {}) => {
    const base = result(regex(null, null));
    return { ...base, ...extra, log: { ...base.log, ...log } } as ExtractorFileResult;
  };

  test('a period the regex missed counts once both filename dates are printed', () => {
    const row = toRow(withLog({ validasi_nama_file: { nama: 'cocok', periode: 'cocok' } }), FILENAME);
    expect(row.documentPeriod).toEqual({ status: 'found', start: '2045-04-16', end: '2047-04-15', durationMonths: null });
    expect(row.periodMatches).toBe(true);
    expect([row.fields.start.check, row.fields.end.check]).toEqual([null, null]);

    const conflicting = toRow(withLog({ periode_regex: regex(null, null, null, true), validasi_nama_file: { periode: 'cocok' } }), FILENAME);
    expect(conflicting.documentPeriod.status).toBe('conflict'); // the regex's veto stands
    expect(toRow(withLog({}), FILENAME).documentPeriod.status).toBe('missing');
  });

  test('a date the second OCR read, completing the period, says so', () => {
    const validasi = { nama: 'cocok', periode: 'cocok', periode_dari: 'pembaca_kedua' } as const;
    const row = toRow(withLog({ periode_regex: regex('2045-04-16', null), validasi_nama_file: validasi }), FILENAME);
    expect(row.periodFromSecondReader).toBe(true);
    expect(row.documentPeriod).toEqual({ status: 'found', start: '2045-04-16', end: '2047-04-15', durationMonths: null });
    const basis = 'Dibaca ulang mesin OCR kedua; sama dengan nama file';
    expect([row.fields.start, row.fields.end]).toEqual([
      { value: '2045-04-16', check: null, basis },
      { value: '2047-04-15', check: null, basis },
    ]);
    expect(toRow(withLog({ validasi_nama_file: { nama: 'cocok', periode: 'cocok' } }), FILENAME).periodFromSecondReader).toBe(false);
  });

  test('the vendor folder is checked against the document', () => {
    const found = toRow(withLog({ validasi_vendor: 'cocok' }), FILENAME, ' PT Contoh Sejahtera ');
    expect([found.vendorFromFolder, found.vendorInDocument]).toEqual(['PT Contoh Sejahtera', true]);
    expect(found.fields.vendor).toEqual({ value: 'PT Contoh Sejahtera', check: null, basis: 'Tercetak di dokumen' });
    const missing = toRow(withLog({ validasi_vendor: 'tidak_cocok' }), FILENAME, 'PT Contoh Sejahtera');
    expect(missing.fields.vendor.check).toBe('Nama folder vendor tidak ditemukan di dokumen');
    expect(toRow(withLog({}), FILENAME).fields.vendor).toEqual({
      value: null,
      check: 'File tidak berada di dalam folder vendor',
      basis: null,
    });
  });

  test('a vendor the page text does not print is decided by the employer Claude read', () => {
    const vendorLog = { validasi_vendor: 'tidak_cocok', vendor_dari: 'llm' } as const;
    const row = toRow(
      withLog({ pembaca: 'llm', llm_cadangan: true, ...vendorLog }, { vendor_dokumen: 'PT Lain Jaya' }),
      FILENAME,
      'PT Contoh Sejahtera',
    );
    expect([row.reader, row.readByFallback, row.vendorBasis, row.vendorFromDocument]).toEqual([
      'claude',
      true,
      'claude',
      'PT Lain Jaya',
    ]);
    expect(row.fields.vendor.check).toBe('Claude membaca "PT Lain Jaya" sebagai perusahaan pemberi kerja');
    const same = toRow(withLog({ validasi_vendor: 'cocok', vendor_dari: 'llm' }, { vendor_dokumen: 'PT Contoh' }), FILENAME, 'PT Contoh');
    expect(same.fields.vendor.basis).toBe('Sama dengan perusahaan pemberi kerja yang dibaca Claude');
    // printed on the page: the lookup decided, whatever Claude read
    const printed = toRow(withLog({ validasi_vendor: 'cocok', vendor_dari: 'teks' }, { vendor_dokumen: 'PT Lain' }), FILENAME, 'PT Contoh');
    expect([printed.vendorBasis, printed.fields.vendor.basis]).toEqual(['text', 'Tercetak di dokumen']);
    expect(toRow(withLog({ pembaca: 'pola' }), FILENAME).readByFallback).toBe(false);
  });

  test("Claude's calls and the NIK it read from a crop reach the row", () => {
    const nik = '3199990101900001'; // fictitious
    const used = {
      ...result(null),
      nik,
      log: {
        ...result(null).log,
        panggilan_llm: ['teks', 'nik', 'ttd', 'odd'],
        token_panggilan: [{ masuk: 10, cache_baca: 9000, keluar: 2500 }, null, { masuk: 4, keluar: 300 }, null],
        nik: { status: 'ditemukan', tanggal_lahir: 'cocok', jenis_kelamin: 'cocok', llm: 'dipakai' },
        alasan: ['nik_from_llm'],
      },
    } as ExtractorFileResult;
    const row = toRow(used, FILENAME);
    expect(row.claudeCalls).toEqual([
      { kind: 'text', tokens: { input: 10, cacheWrite: 0, cacheRead: 9000, output: 2500 } },
      { kind: 'nik', tokens: null }, // the provider reported none
      { kind: 'signature', tokens: { input: 4, cacheWrite: 0, cacheRead: 0, output: 300 } },
    ]);
    expect(row.nik.claude).toBe('used');
    expect(row.fields.nik).toEqual({
      value: '************0001',
      check: null,
      basis: 'Dibaca Claude dari potongan baris NIK; cocok dengan tanggal lahir dan jenis kelamin di kontrak',
    });
    const unconfirmed = { ...used, log: { ...used.log, nik: { ...used.log.nik!, jenis_kelamin: 'tidak_ada' } } } as ExtractorFileResult;
    expect(toRow(unconfirmed, FILENAME).fields.nik.check).toBe('Dibaca Claude dari potongan baris NIK; cocokkan angkanya');
    const differs = { ...used, log: { ...used.log, nik: { ...used.log.nik!, llm: 'beda' } } } as ExtractorFileResult;
    expect(toRow(differs, FILENAME).fields.nik.check).toBe('Claude membaca angka lain di baris NIK; cocokkan angkanya');
    expect(toRow(result(null), FILENAME).claudeCalls).toEqual([]);
  });

  test("a signature Claude settled from a crop says so, and a disagreement is coloured", () => {
    const look = (status_ttd: 'signed' | 'uncertain', reason: string) =>
      ({
        ...result(null),
        status_ttd,
        log: { ...result(null).log, tanda_tangan: { sumber: null, llm: 'signed', llm_conf: 0.96 }, alasan: [reason] },
      }) as ExtractorFileResult;
    const settled = toRow(look('signed', 'signature_from_llm'), FILENAME);
    expect(settled.signatureByClaude).toEqual({ status: 'signed', confidence: 0.96 });
    expect(settled.fields.signature).toEqual({
      value: 'YES',
      check: null,
      basis: 'Pemeriksaan lokal ragu; Claude melihat coretan tanda tangan di potongan area tanda tangan',
    });
    // the ink check called it not clear and Claude saw the same: sure, with no check
    expect(toRow(look('signed', 'signature_llm_agrees'), FILENAME).fields.signature).toEqual({
      value: 'YES',
      check: null,
      basis: 'Pemeriksaan lokal kurang jelas; Claude juga melihat coretan tanda tangan di potongan area tanda tangan',
    });
    expect(toRow(look('signed', 'signature_not_clear'), FILENAME).fields.signature.check).toContain('Kurang jelas');
    const disputed = toRow(look('uncertain', 'signature_llm_differs'), FILENAME);
    expect(disputed.fields.signature.value).toBeNull();
    expect(disputed.fields.signature.check).toContain('Claude melihat tanda tangan');
    const unseen = toRow(look('uncertain', 'signature_llm_sees_none'), FILENAME);
    expect(unseen.fields.signature.value).toBeNull();
    expect(unseen.fields.signature.check).toContain('Claude tidak melihat tanda tangan');
    expect(toRow(result(null), FILENAME).signatureByClaude).toBeNull();
  });

  test('old no-OCR trial rows: a NIK Claude read from the whole first page is trusted only when the page confirms it', () => {
    const nik = '3199990101900001'; // fictitious
    const read = (checks: { tanggal_lahir: string; jenis_kelamin: string }, confidence = 0.95, status = 'ditemukan') =>
      withLog(
        { pembaca: 'llm', alasan: ['read_full_pages'], nik: { status, ...checks } as ExtractorFileResult['log']['nik'] },
        { nik: status === 'ditemukan' ? nik : null, confidence: { nik: confidence } },
      );
    const both = { tanggal_lahir: 'cocok', jenis_kelamin: 'cocok' };
    const row = toRow(read(both), FILENAME);
    expect(row.readConfidence.nik).toBe(0.95);
    expect(row.fields.nik).toEqual({
      value: '************0001',
      check: null,
      basis: 'Dibaca Claude dari halaman pertama; cocok dengan tanggal lahir dan jenis kelamin di kontrak',
    });
    const check = (r: ExtractorFileResult) => toRow(r, FILENAME).fields.nik;
    expect(check(read(both, 0.6)).check).toBe('Dibaca Claude dari halaman pertama; cocokkan angkanya');
    expect(check(read({ ...both, jenis_kelamin: 'tidak_ada' })).check).toBe('Dibaca Claude dari halaman pertama; cocokkan angkanya');
    expect(check(read({ ...both, tanggal_lahir: 'tidak_cocok' })).check).toContain('tidak cocok dengan tanggal lahir');
    expect(check(read(both, 0.95, 'tidak_ditemukan'))).toEqual({
      value: null,
      check: 'Claude tidak menemukan NIK di halaman pertama',
      basis: null,
    });
    expect(check(read(both, 0.95, 'tidak_valid')).check).toBe('NIK yang dibaca Claude bukan NIK yang valid');
    // outside the old no-OCR trial the NIK's confidence is the OCR's or a crop's, and is not shown as Claude's
    expect(toRow(withLog({}, { nik, confidence: { nik: 0.5 } }), FILENAME).readConfidence.nik).toBeNull();
  });

  test('old no-OCR trial rows: the signature is what Claude saw on the last page, and its one call is counted', () => {
    const look = (status_ttd: 'signed' | 'unsigned' | 'uncertain') =>
      withLog(
        {
          pembaca: 'llm',
          p_ttd: 5,
          alasan: status_ttd === 'uncertain' ? ['read_full_pages', 'signature_uncertain'] : ['read_full_pages'],
          tanda_tangan: { sumber: 'llm', llm: status_ttd === 'uncertain' ? 'unclear' : status_ttd, llm_conf: 0.9 },
          panggilan_llm: ['halaman'],
          token_panggilan: [{ masuk: 12, cache_tulis: 13000, keluar: 2200 }],
        },
        { status_ttd },
      );
    const signed = toRow(look('signed'), FILENAME);
    expect(signed.fields.signature).toEqual({ value: 'YES', check: null, basis: 'Claude melihat tanda tangan karyawan di halaman 5' });
    expect(signed.claudeCalls).toEqual([{ kind: 'pages', tokens: { input: 12, cacheWrite: 13000, cacheRead: 0, output: 2200 } }]);
    expect(toRow(look('unsigned'), FILENAME).fields.signature).toEqual({
      value: 'NO',
      check: null,
      basis: 'Claude melihat tempat tanda tangan karyawan kosong di halaman 5',
    });
    expect(toRow(look('uncertain'), FILENAME).fields.signature).toEqual({
      value: null,
      check: 'Claude belum yakin; lihat halaman 5',
      basis: null,
    });
  });

  test("Claude reading the identity page: the page call is counted, and the name, period and title are Claude's", () => {
    const row = toRow(
      withLog(
        {
          pembaca: 'llm',
          alasan: ['read_identity_image'],
          periode_llm: { tanggal_mulai: '2045-04-16', tanggal_selesai: '2047-04-15', durasi_bulan: 24 },
          validasi_nama_file: { nama: 'cocok', periode: 'cocok' },
          posisi: { status: 'ditemukan', sumber: 'llm' },
          panggilan_llm: ['halaman_identitas', 'ttd'],
          token_panggilan: [{ masuk: 20, cache_tulis: 3000, keluar: 400 }, null],
        },
        { nama_karyawan: 'BUDI', posisi: 'Staff Gudang', confidence: { nama: 0.95, periode: 0.95, posisi: 0.9, nik: 0.4 } },
      ),
      FILENAME,
    );
    expect(row.claudeCalls).toEqual([
      { kind: 'page', tokens: { input: 20, cacheWrite: 3000, cacheRead: 0, output: 400 } },
      { kind: 'signature', tokens: null },
    ]);
    expect(row.reader).toBe('claude');
    expect(row.fields.name).toEqual({ value: 'Budi', check: null, basis: 'Sama dengan nama karyawan yang dibaca Claude dari dokumen' });
    expect([row.fields.start, row.fields.end].map((f) => [f.value, f.check])).toEqual([
      ['2045-04-16', null],
      ['2047-04-15', null],
    ]);
    expect(row.fields.position).toEqual({ value: 'Staff Gudang', check: null, basis: 'Dibaca Claude dari halaman identitas' });
    expect(row.readConfidence.nik).toBeNull(); // the NIK stays the local reading, not Claude's
  });

  test("Claude's tokens reach the row as whole counts", () => {
    const used = { masuk: 40, cache_tulis: 2000, cache_baca: 0, keluar: 900 };
    expect(toRow(withLog({ pembaca: 'llm', token_llm: used }), FILENAME).tokens).toEqual({
      input: 40,
      cacheWrite: 2000,
      cacheRead: 0,
      output: 900,
    });
    const odd = { masuk: -1, cache_tulis: 1.5, keluar: '9' as unknown as number };
    expect(toRow(withLog({ token_llm: odd }), FILENAME).tokens).toEqual({ input: 0, cacheWrite: 0, cacheRead: 0, output: 0 });
    expect(toRow(withLog({}), FILENAME).tokens).toBeNull();
  });
});
