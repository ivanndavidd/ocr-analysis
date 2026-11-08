import type { ContractFields, ContractRow, FieldValue } from '@contract-ocr/shared-types';

// One rule set for what is doubtful in a contract's data: the results page colours these cells and the Excel colours
// the same cells with the same note, so a person checks once and finds the same thing in both.

/** Everything a row knows except its fields, which are derived from the rest. */
export type RowFacts = Omit<ContractRow, 'fields'>;

const LOW_CONFIDENCE = 0.8; // an OCR-read title, or Claude's own confidence, below this is shown but coloured
const unsure = (confidence: number | null) => confidence !== null && confidence < LOW_CONFIDENCE;
export const BULAN_SINGKAT = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

const sure = (value: string | number | null, basis: string): FieldValue => ({ value, check: null, basis });
const doubt = (value: string | number | null, check: string): FieldValue => ({ value, check, basis: null });

/** `2026-03-31` → `31 Mar 2026`, as people write it. */
export function tanggal(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return y && m && d ? `${d} ${BULAN_SINGKAT[m - 1] ?? m} ${y}` : iso;
}

/** The reason the no-OCR mode adds: every value came from Claude reading the whole first and last page. */
export const FULL_PAGES = 'read_full_pages';
const fullPages = (row: RowFacts) => row.reasons.includes(FULL_PAGES);

/** The no-OCR mode: Claude read the NIK from the whole first page, with the birth date and gender printed there; the
 *  NIK is trusted when both confirm it and Claude was sure of its digits. */
function nikFromPage(row: RowFacts): FieldValue {
  const { status, masked, birthDate, gender } = row.nik;
  if (status === 'invalid') return doubt(null, 'NIK yang dibaca Claude bukan NIK yang valid');
  if (!masked) return doubt(null, 'Claude tidak menemukan NIK di halaman pertama');
  if (birthDate === 'mismatch' || gender === 'mismatch') {
    return doubt(masked, 'Dibaca Claude; tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak');
  }
  const confidence = row.readConfidence.nik;
  if (birthDate === 'match' && gender === 'match' && confidence !== null && !unsure(confidence)) {
    return sure(masked, 'Dibaca Claude dari halaman pertama; cocok dengan tanggal lahir dan jenis kelamin di kontrak');
  }
  return doubt(masked, 'Dibaca Claude dari halaman pertama; cocokkan angkanya');
}

/** The local reading first; Claude reads a crop of the NIK line only when that reading is unsure, and its NIK passes
 *  the same birth-date and gender checks before it is used. */
function nik(row: RowFacts): FieldValue {
  if (fullPages(row)) return nikFromPage(row);
  const { status, masked, birthDate, gender, fromOcr, claude } = row.nik;
  if (status === 'conflict') return doubt(null, 'Ada dua NIK berbeda di dokumen');
  const bothMatch = birthDate === 'match' && gender === 'match';
  if (claude === 'used' && masked) {
    return bothMatch
      ? sure(masked, 'Dibaca Claude dari potongan baris NIK; cocok dengan tanggal lahir dan jenis kelamin di kontrak')
      : doubt(masked, 'Dibaca Claude dari potongan baris NIK; cocokkan angkanya');
  }
  if (claude === 'different') {
    return masked
      ? doubt(masked, 'Claude membaca angka lain di baris NIK; cocokkan angkanya')
      : doubt(null, 'NIK yang dibaca Claude tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak');
  }
  if (status === 'invalid') return doubt(null, 'Angka setelah label NIK bukan NIK yang valid');
  if (!masked) return doubt(null, 'Tidak ditemukan di halaman identitas');
  if (birthDate === 'mismatch' || gender === 'mismatch') {
    return doubt(masked, 'Tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak');
  }
  if (fromOcr && !bothMatch) return doubt(masked, 'Dibaca dari scan; cocokkan angkanya');
  if (row.reasons.includes('nik_digits_repaired')) {
    return doubt(masked, 'Terbaca lebih dari 16 angka di baris NIK; cocokkan angkanya');
  }
  if (bothMatch) return sure(masked, 'Cocok dengan tanggal lahir dan jenis kelamin di kontrak');
  if (claude === 'same') return sure(masked, 'Claude membaca angka yang sama dari potongan baris NIK');
  return sure(masked, 'Dari teks digital PDF, bukan hasil scan');
}

/** NAMA VENDOR is the vendor folder (`root/<vendor>/<masa kontrak>/<pdf>`), the outsourcing company that employs the
 *  employee; the contract always prints it, so the folder's name is looked up there first, and only when it is not
 *  printed is it compared with the employer company Claude read. */
function vendor(row: RowFacts): FieldValue {
  const named = row.vendorFromFolder;
  if (!named) return doubt(null, 'File tidak berada di dalam folder vendor');
  if (row.vendorInDocument === null) return doubt(named, 'Belum dicocokkan dengan dokumen');
  const byClaude = row.vendorBasis === 'claude' && row.vendorFromDocument;
  if (!row.vendorInDocument) {
    if (byClaude) return doubt(named, `Claude membaca "${row.vendorFromDocument}" sebagai perusahaan pemberi kerja`);
    return doubt(named, 'Nama folder vendor tidak ditemukan di dokumen');
  }
  return sure(named, byClaude ? 'Sama dengan perusahaan pemberi kerja yang dibaca Claude' : 'Tercetak di dokumen');
}

/** ENTITAS is only ever in the filename (`Nama - Masa Kontrak - ENTITAS`); the document is not checked for it. */
function entity(row: RowFacts): FieldValue {
  if (!row.entityFromFilename) return doubt(null, 'Nama file tidak menyebut entitas ("Nama - Masa Kontrak - ENTITAS")');
  return sure(row.entityFromFilename, 'Dari nama file');
}

/** Claude's reading checks the filename's name against the employee party of the document. When both name the same
 *  person the filename's spelling is kept, as with the patterns, since OCR can misspell a scanned name that Claude
 *  then copies faithfully. */
function nameFromClaude(row: RowFacts, read: string): FieldValue {
  const named = row.filenameStandard ? row.nameFromFilename : null;
  if (!named) return doubt(read, 'Dibaca Claude dari dokumen; nama file tidak berformat "Nama - Masa Kontrak"');
  if (row.nameMatch === 'different') return doubt(read, `Nama file menyebut "${named}"`);
  if (row.nameMatch === 'similar') return doubt(named, `Claude membaca "${read}" di dokumen`);
  if (unsure(row.readConfidence.name)) return doubt(named, 'Claude kurang yakin membaca nama karyawan');
  return sure(named, 'Sama dengan nama karyawan yang dibaca Claude dari dokumen');
}

function name(row: RowFacts): FieldValue {
  if (row.nameFromDocument) return nameFromClaude(row, row.nameFromDocument);
  const value = row.nameFromFilename;
  if (!value) return doubt(null, 'Tidak terbaca dari nama file');
  if (!row.filenameStandard) return doubt(value, 'Nama file tidak berformat "Nama - Masa Kontrak"');
  if (row.nameInDocument === false) return doubt(value, 'Nama dari nama file tidak ada di halaman identitas');
  if (row.nameInDocument === null) return doubt(value, 'Belum dicocokkan dengan dokumen');
  return sure(value, 'Nama dari nama file tercetak di halaman identitas');
}

function position(row: RowFacts): FieldValue {
  const { status, value, confidence, agreement, source } = row.position;
  if (status === 'conflict') return doubt(null, 'Ada dua posisi berbeda di dokumen');
  if (!value) return doubt(null, 'Tidak ditemukan di halaman identitas');
  if (agreement === 'different') return doubt(value, 'Pembacaan pola teks berbeda; cek di dokumen');
  if (unsure(confidence)) {
    return doubt(value, source === 'claude' ? 'Claude kurang yakin membaca posisi ini' : 'Dibaca dari scan yang kurang jelas');
  }
  if (agreement === 'same') return sure(value, 'Dibaca Claude dan sama dengan pembacaan pola teks');
  return sure(value, source === 'claude' ? 'Dibaca Claude dari halaman identitas' : 'Tertulis di halaman identitas');
}

const DAY_MS = 86_400_000;
const parseIso = (iso: string) => new Date(`${iso}T00:00:00Z`);
const isoOf = (date: Date) => date.toISOString().slice(0, 10);

function addMonths(start: Date, months: number): Date {
  const y = start.getUTCFullYear();
  const m = start.getUTCMonth() + months;
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(start.getUTCDate(), last)));
}

/** Whole months from start to end, counting `1 Jan - 31 Des` and `1 Jan - 1 Jan` both as 12; null otherwise. */
export function wholeMonths(start: string, end: string): number | null {
  const s = parseIso(start);
  const e = parseIso(end);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime()) || e <= s) return null;
  const guess = (e.getUTCFullYear() - s.getUTCFullYear()) * 12 + e.getUTCMonth() - s.getUTCMonth();
  for (const n of [guess, guess + 1]) {
    if (n < 1) continue;
    const next = addMonths(s, n);
    if (isoOf(next) === end || isoOf(new Date(next.getTime() - DAY_MS)) === end) return n;
  }
  return null;
}

/** Start, end and months: the document's dates, trusted when the filename names the same; the filename's when the
 *  document has none; empty when the document holds conflicting dates. */
function period(row: RowFacts): Pick<ContractFields, 'start' | 'end' | 'months'> {
  const doc = row.documentPeriod;
  const named = row.periodFromFilename;
  const one = (fromDoc: string | null, fromName: string | null): FieldValue => {
    if (fromDoc) {
      if (!fromName) return doubt(fromDoc, 'Hanya dari dokumen; nama file tidak menyebut tanggal');
      if (fromName !== fromDoc) return doubt(fromDoc, `Nama file menyebut ${tanggal(fromName)}`);
      return sure(fromDoc, 'Sama di dokumen dan di nama file');
    }
    if (doc.status === 'conflict') return doubt(null, 'Ada beberapa tanggal berbeda di dokumen');
    if (fromName) return doubt(fromName, 'Tidak ada di dokumen; diambil dari nama file');
    return doubt(null, 'Tidak ada di dokumen maupun nama file');
  };
  let start = one(doc.start, named?.start ?? null);
  let end = one(doc.end, named?.end ?? null);
  if (unsure(row.readConfidence.period)) {
    const check = 'Claude kurang yakin membaca tanggal ini';
    if (doc.start) start = doubt(doc.start, check);
    if (doc.end) end = doubt(doc.end, check);
  }

  const datesDoubtful = Boolean(start.check || end.check);
  const counted =
    typeof start.value === 'string' && typeof end.value === 'string' ? wholeMonths(start.value, end.value) : null;
  let months: FieldValue;
  if (doc.durationMonths !== null && counted !== null && doc.durationMonths !== counted) {
    months = doubt(doc.durationMonths, `Dokumen menyebut ${doc.durationMonths} bulan, tanggalnya ${counted} bulan`);
  } else if (doc.durationMonths !== null) {
    months = datesDoubtful
      ? doubt(doc.durationMonths, 'Tanggal mulai atau selesai perlu dicek')
      : sure(doc.durationMonths, counted !== null ? 'Tertulis di dokumen dan sama dengan tanggalnya' : 'Tertulis di dokumen');
  } else if (counted !== null) {
    months = datesDoubtful
      ? doubt(counted, 'Dihitung dari tanggal yang perlu dicek')
      : sure(counted, 'Dihitung dari tanggal mulai dan selesai');
  } else if (doc.status === 'conflict') {
    months = doubt(null, 'Dokumen menyebut lama kontrak yang berbeda-beda');
  } else if (typeof start.value === 'string' && typeof end.value === 'string') {
    months = doubt(null, 'Masa kontrak tidak genap per bulan');
  } else {
    months = doubt(null, 'Masa kontrak tidak lengkap');
  }
  return { start, end, months };
}

/** The no-OCR mode: Claude alone looked at the last page, and decided only above the same cutoffs as for a crop. */
function signatureFromPage(row: RowFacts, where: string): FieldValue {
  if (row.signature === 'signed') return sure('YES', `Claude melihat tanda tangan karyawan di${where}`);
  if (row.signature === 'unsigned') return sure('NO', `Claude melihat tempat tanda tangan karyawan kosong di${where}`);
  return doubt(null, `Claude belum yakin; lihat${where}`);
}

function signature(row: RowFacts): FieldValue {
  const page = row.detail.signaturePage;
  const where = page ? ` halaman ${page}` : '';
  if (fullPages(row)) return signatureFromPage(row, where);
  if (row.reasons.includes('signature_llm_differs')) {
    return doubt(null, `Pemeriksaan lokal tidak menemukan coretan, tetapi Claude melihat tanda tangan; lihat${where}`);
  }
  if (row.reasons.includes('signature_llm_sees_none')) {
    return doubt(null, `Pemeriksaan lokal melihat coretan, tetapi Claude tidak melihat tanda tangan; lihat${where}`);
  }
  if (row.signature === 'signed' || row.signature === 'unsigned') {
    const value = row.signature === 'signed' ? 'YES' : 'NO';
    if (row.reasons.includes('signature_page_guessed')) {
      return doubt(value, `Halaman tanda tangan tidak dikenali; dibaca dari${where}, cek halaman itu`);
    }
    if (row.reasons.includes('signature_from_llm')) {
      const seen = row.signature === 'signed' ? 'melihat coretan tanda tangan' : 'melihat tempat tanda tangan kosong';
      return sure(value, `Pemeriksaan lokal ragu; Claude ${seen} di potongan area tanda tangan`);
    }
    if (row.reasons.includes('signature_not_clear')) return doubt(value, `Kurang jelas; lihat sekilas${where}`);
    return sure(
      value,
      row.signature === 'signed' ? 'Ada coretan tanda tangan di atas nama karyawan' : 'Tempat tanda tangan karyawan kosong',
    );
  }
  if (!page) return doubt(null, 'Halaman tanda tangan tidak ditemukan');
  return doubt(null, `Belum pasti; lihat${where}`);
}

/** The Excel row of a contract, with what a person should check. The NIK is the masked one. */
export function fieldsOf(row: RowFacts): ContractFields {
  return {
    nik: nik(row),
    vendor: vendor(row),
    entity: entity(row),
    name: name(row),
    position: position(row),
    ...period(row),
    signature: signature(row),
  };
}
