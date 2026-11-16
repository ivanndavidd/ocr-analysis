import { describe, expect, test } from 'bun:test';
import { type ContractFields, LANGS, NOTE_TEXTS, type Note, type NoteKey, note, noteDate, noteText } from '@contract-ocr/shared-types';
import { type RowFacts, type SavedFields, fieldsOf, keyedFields } from '../src/modules/jobs/fields.ts';
import { inIndonesian } from './fixtures/notes.ts';

// Fictitious names and companies only.
const SAMPLE = { page: 2, company: 'PT Contoh Jaya', name: 'Budi Uji', date: '2026-03-31', stated: 3, counted: 2, text: 'catatan lama' };
const KEYS = Object.keys(NOTE_TEXTS.id) as NoteKey[];

function facts(overrides: Partial<RowFacts> = {}): RowFacts {
  return {
    file: 'Budi Uji - 1jan-31mar26 - SNA.pdf',
    reader: 'pattern',
    readByFallback: false,
    claudeCalls: [],
    tokens: null,
    vendorFromFolder: 'PT Contoh Jaya',
    vendorInDocument: true,
    vendorBasis: 'text',
    vendorFromDocument: null,
    entityFromFilename: 'SNA',
    nameFromFilename: 'Budi Uji',
    nameFromDocument: null,
    nameInDocument: true,
    nameMatch: null,
    readConfidence: { name: null, period: null, nik: null },
    nik: { status: 'found', masked: '************0001', birthDate: 'match', gender: 'match', fromOcr: false, claude: null },
    position: { status: 'found', value: 'Kasir', confidence: 1, agreement: null, source: 'pattern' },
    filenameStandard: true,
    periodFromFilename: { start: '2026-01-01', end: '2026-03-31' },
    periodFromDocument: { start: '2026-01-01', end: '2026-02-28' },
    documentPeriod: { status: 'found', start: '2026-01-01', end: '2026-02-28', durationMonths: 3 },
    periodMatches: false,
    signature: 'signed',
    signatureByClaude: null,
    decision: 'review',
    reasons: ['signature_not_clear'],
    detail: { identityPages: [1], signaturePage: 2, pageKinds: {}, ocrConfidence: {}, signatureSource: null, vendorNote: null },
    ...overrides,
  };
}

describe('cell notes in two languages', () => {
  test('every note key exists in both languages and renders a sentence', () => {
    for (const lang of LANGS) expect(Object.keys(NOTE_TEXTS[lang]).sort()).toEqual([...KEYS].sort());
    for (const key of KEYS) {
      for (const lang of LANGS) {
        const text = noteText({ key, params: SAMPLE }, lang);
        expect(typeof text).toBe('string');
        expect(text.trim().length).toBeGreaterThan(0);
      }
      // English is written, not copied: only a note saved as text reads the same in both
      if (key !== 'legacy') expect(noteText({ key, params: SAMPLE }, 'en')).not.toBe(noteText({ key, params: SAMPLE }, 'id'));
    }
  });

  test('the Indonesian wording is the one HR knows; English is natural', () => {
    const at2 = note('signature.unclear', { page: 2 });
    expect(noteText(at2, 'id')).toBe('Kurang jelas; lihat sekilas halaman 2');
    expect(noteText(at2, 'en')).toBe('Unclear; take a quick look at page 2');
    expect(noteText(note('signature.unclear', { page: null }), 'id')).toBe('Kurang jelas; lihat sekilas');
    const date = note('period.filename_says', { date: '2037-08-01' });
    expect([noteText(date, 'id'), noteText(date, 'en')]).toEqual(['Nama file menyebut 1 Agu 2037', 'The filename says 1 Aug 2037']);
    const months = note('months.differ', { stated: 1, counted: 2 });
    expect(noteText(months, 'id')).toBe('Dokumen menyebut 1 bulan, tanggalnya 2 bulan');
    expect(noteText(months, 'en')).toBe('The document says 1 month, its dates give 2 months');
    expect([noteDate('2026-03-31', 'id'), noteDate('2026-03-31', 'en'), noteDate('bukan tanggal', 'en')]).toEqual([
      '31 Mar 2026',
      '31 Mar 2026',
      'bukan tanggal',
    ]);
  });

  test('a note without parameters carries only its key; one with parameters carries them', () => {
    expect(note('nik.conflict')).toEqual({ key: 'nik.conflict' });
    expect(note('vendor.claude_reads', { company: 'PT Contoh Jaya' })).toEqual({
      key: 'vendor.claude_reads',
      params: { company: 'PT Contoh Jaya' },
    });
  });

  test('a key this version does not know shows as the key, never as an object property', () => {
    for (const key of ['nik.renamed_later', 'constructor', '__proto__', 'toString']) {
      expect(noteText({ key } as unknown as Note, 'en')).toBe(key);
    }
    // parameters lost from a hand-edited row: still a sentence
    expect(noteText({ key: 'signature.uncertain' }, 'id')).toBe('Belum pasti; lihat');
  });
});

describe('rows saved before the notes had keys', () => {
  const row = facts();
  const fields = fieldsOf(row);
  const asSaved = (current: ContractFields): SavedFields => inIndonesian({ fields: current }).fields;

  test('get their keys back where the current rules give the same text, so they follow the language', () => {
    const keyed = keyedFields({ ...row, fields: asSaved(fields) });
    expect(keyed).toEqual(fields);
    expect(noteText(keyed.signature.check!, 'en')).toBe('Unclear; take a quick look at page 2');
    expect(keyed.end.check).toEqual(note('period.filename_says', { date: '2026-03-31' }));
    expect(noteText(keyed.end.check!, 'en')).toBe('The filename says 31 Mar 2026');
  });

  test('keep a text no current rule gives as it was written, and never judge the values again', () => {
    const saved = asSaved(fields);
    saved.signature = { value: 'NO', check: 'Tanda tangan karyawan tidak terlihat', basis: null };
    saved.position = { value: 'Kasir', check: null, basis: 'Tertulis di halaman identitas' };
    const keyed = keyedFields({ ...row, fields: saved });
    expect(keyed.signature).toEqual({ value: 'NO', check: note('legacy', { text: 'Tanda tangan karyawan tidak terlihat' }), basis: null });
    expect(noteText(keyed.signature.check!, 'en')).toBe('Tanda tangan karyawan tidak terlihat');
    expect(keyed.position).toEqual({ value: 'Kasir', check: null, basis: note('position.printed') });
  });

  test('leave keyed notes alone, and survive facts an older backend did not save', () => {
    expect(keyedFields({ ...row, fields })).toEqual(fields);
    const broken = { fields: asSaved(fields) } as unknown as RowFacts & { fields: SavedFields };
    expect(keyedFields(broken).nik.basis).toEqual(note('legacy', { text: 'Cocok dengan tanggal lahir dan jenis kelamin di kontrak' }));
  });
});
