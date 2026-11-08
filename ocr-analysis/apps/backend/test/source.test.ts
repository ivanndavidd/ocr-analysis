import { beforeAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LocalFolderSource, SourceError } from '../src/modules/folders/source.ts';

let root: string;
let outside: string;
let source: LocalFolderSource;

beforeAll(() => {
  const base = mkdtempSync(join(tmpdir(), 'ocr-source-'));
  root = join(base, 'kontrak');
  outside = join(base, 'rahasia');
  mkdirSync(join(root, 'PT Bina Semesta', 'Januari 2041 - Maret 2041'), { recursive: true });
  mkdirSync(join(root, 'PT Bina Semesta', 'Kosong'), { recursive: true });
  mkdirSync(outside);
  const period = join(root, 'PT Bina Semesta', 'Januari 2041 - Maret 2041');
  writeFileSync(join(period, 'Budi Santoso - 1jan-31mar41.pdf'), '%PDF-1.4 a');
  writeFileSync(join(period, 'Andi - 1 Jan - 31 Mar 2041.PDF'), '%PDF-1.4 b');
  writeFileSync(join(period, 'catatan.txt'), 'not a pdf');
  writeFileSync(join(period, 'desktop.ini'), 'x');
  writeFileSync(join(outside, 'secret.pdf'), '%PDF-1.4 secret');
  symlinkSync(outside, join(root, 'jalan-pintas'), 'junction'); // a link that leads out of the root
  source = new LocalFolderSource(root);
});

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof SourceError) return error.code;
    throw error;
  }
  return 'no error';
}

describe('LocalFolderSource', () => {
  test('lists folders with PDF counts and the PDFs of the current folder', async () => {
    const vendor = await source.list('PT Bina Semesta');
    expect(vendor.crumbs).toEqual([{ name: 'PT Bina Semesta', path: 'PT Bina Semesta' }]);
    expect(vendor.folders).toEqual([
      { name: 'Januari 2041 - Maret 2041', path: 'PT Bina Semesta/Januari 2041 - Maret 2041', pdfCount: 2 },
      { name: 'Kosong', path: 'PT Bina Semesta/Kosong', pdfCount: 0 },
    ]);
    const period = await source.list('PT Bina Semesta/Januari 2041 - Maret 2041');
    expect(period.pdfs).toEqual(['Andi - 1 Jan - 31 Mar 2041.PDF', 'Budi Santoso - 1jan-31mar41.pdf']);
    const root = await source.list('');
    expect(root.crumbs).toEqual([]);
    expect(root.folders.find((f) => f.name === 'PT Bina Semesta')?.pdfCount).toBe(2); // counted through subfolders
  });

  test('gives each subfolder the PDFs its listing counts, for the folder marks', async () => {
    const period = 'PT Bina Semesta/Januari 2041 - Maret 2041';
    expect(await source.subfolderPdfs('PT Bina Semesta')).toEqual([
      {
        name: 'Januari 2041 - Maret 2041',
        path: period,
        pdfs: [`${period}/Andi - 1 Jan - 31 Mar 2041.PDF`, `${period}/Budi Santoso - 1jan-31mar41.pdf`],
        complete: true,
      },
      { name: 'Kosong', path: 'PT Bina Semesta/Kosong', pdfs: [], complete: true },
    ]);
    // through subfolders, as the listing counts; the link out of the root is no subfolder
    expect((await source.subfolderPdfs('')).map((f) => [f.path, f.pdfs.length])).toEqual([['PT Bina Semesta', 2]]);
    expect(await code(source.subfolderPdfs('jalan-pintas'))).toBe('bad_path');
    expect(await code(source.subfolderPdfs('../rahasia'))).toBe('bad_path');
    expect(await code(source.subfolderPdfs('Tidak Ada'))).toBe('not_found');
    expect(await code(source.subfolderPdfs(`${period}/Budi Santoso - 1jan-31mar41.pdf`))).toBe('not_found');
  });

  test('collects PDFs through subfolders without following links out of the root', async () => {
    const period = 'PT Bina Semesta/Januari 2041 - Maret 2041';
    expect(await source.pdfPaths(['PT Bina Semesta'], 100)).toEqual([
      `${period}/Andi - 1 Jan - 31 Mar 2041.PDF`,
      `${period}/Budi Santoso - 1jan-31mar41.pdf`,
    ]);
    expect(await source.pdfPaths([''], 100)).toEqual(await source.pdfPaths(['PT Bina Semesta'], 100)); // no secret.pdf
    expect(await source.pdfPaths(['PT Bina Semesta', period], 100)).toHaveLength(2);
    expect((await source.pdfPaths([''], 1)).length).toBe(2); // stops just past the limit
    expect(await code(source.pdfPaths(['jalan-pintas'], 100))).toBe('bad_path');

    const mixed = mkdtempSync(join(tmpdir(), 'ocr-mixed-'));
    mkdirSync(join(mixed, 'a'));
    writeFileSync(join(mixed, 'a', 'c.pdf'), '%PDF-1.4');
    writeFileSync(join(mixed, 'b.pdf'), '%PDF-1.4');
    expect(await new LocalFolderSource(mixed).pdfPaths([''], 100)).toEqual(['b.pdf', 'a/c.pdf']); // own PDFs first
    expect(await code(source.pdfPaths([`${period}/Budi Santoso - 1jan-31mar41.pdf`], 100))).toBe('not_found');
  });

  test('takes single PDFs inside the root only', async () => {
    const budi = 'PT Bina Semesta/Januari 2041 - Maret 2041/Budi Santoso - 1jan-31mar41.pdf';
    expect(await source.pdfFiles([budi, budi.replaceAll('/', '\\')])).toEqual([budi]);
    expect(await code(source.pdfFiles(['PT Bina Semesta/Januari 2041 - Maret 2041/catatan.txt']))).toBe('not_pdf');
    expect(await code(source.pdfFiles(['jalan-pintas/secret.pdf']))).toBe('bad_path');
    expect(await code(source.pdfFiles(['PT Bina Semesta/Kosong/Siapa.pdf']))).toBe('not_found');
  });

  test('reads a PDF into memory and enforces the size limit', async () => {
    const path = 'PT Bina Semesta/Januari 2041 - Maret 2041/Budi Santoso - 1jan-31mar41.pdf';
    expect(new TextDecoder().decode(await source.readPdf(path, 1024))).toBe('%PDF-1.4 a');
    expect(await code(source.readPdf(path, 3))).toBe('too_large');
  });

  test.each([
    ['../rahasia', 'bad_path'],
    ['PT Bina Semesta/../../rahasia', 'bad_path'],
    ['C:/Windows', 'bad_path'],
    ['jalan-pintas', 'bad_path'],
    ['a\0b', 'bad_path'],
    ['Tidak Ada', 'not_found'],
  ])('refuses %p with %s', async (path, expected) => {
    expect(await code(source.list(path))).toBe(expected);
  });

  test('serves only PDFs inside the root', async () => {
    expect(await code(source.pdfFile('jalan-pintas/secret.pdf'))).toBe('bad_path');
    expect(await code(source.pdfFile('PT Bina Semesta/Januari 2041 - Maret 2041/catatan.txt'))).toBe('not_pdf');
  });
});
