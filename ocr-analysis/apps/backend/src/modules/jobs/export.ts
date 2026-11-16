import { type FieldValue, type Job, type JobFile, noteText } from '@contract-ocr/shared-types';
import { BULAN_SINGKAT } from './fields.ts';

// The workbook spec `contract-extractor xlsx` takes (apps/extractor/src/contract_extractor/output/workbook.py).
export interface WorkbookCell {
  value: string | number | null;
  check?: string; // why the cell needs a look: it is coloured and carries this as a comment
}
export interface WorkbookSpec {
  columns: { header: string; type?: 'text' | 'date' | 'number' }[];
  sheets: { name: string; rows: WorkbookCell[][] }[];
}

export const COLUMNS: WorkbookSpec['columns'] = [
  { header: 'NIK KTP' },
  { header: 'NAMA VENDOR' },
  { header: 'ENTITAS' },
  { header: 'NAMA KARYAWAN' },
  { header: 'POSISI' },
  { header: 'TANGGAL JOIN AWAL', type: 'date' },
  { header: 'START PERIODE', type: 'date' },
  { header: 'END PERIODE', type: 'date' },
  { header: 'MONTH', type: 'number' },
  { header: 'SIGN' },
  { header: 'FILE' },
];

export const ALL_SHEET = 'ALL';
const NO_VENDOR_SHEET = 'Tanpa Vendor';
const EMPTY: WorkbookCell = { value: null };

const FAILED: Record<string, string> = {
  timeout: 'waktu baca habis',
  worker_crashed: 'pembaca berhenti',
  bad_reply: 'jawaban pembaca rusak',
  extractor_unavailable: 'pembaca tidak tersedia',
  too_large: 'file terlalu besar',
  not_pdf: 'bukan PDF',
  not_found: 'file tidak ditemukan lagi',
};

const cell = (value: string | number | null, check?: string | null): WorkbookCell => (check ? { value, check } : { value });
// The Excel stays Indonesian whatever language the page shows: its columns follow HR's Indonesian template.
const excel = (field: FieldValue): WorkbookCell => cell(field.value, field.check && noteText(field.check, 'id'));

/** The vendor is the first folder under the root: `root/<vendor>/<kontrak range>/<pdf>`. */
export function vendorOf(path: string): string {
  const at = path.indexOf('/');
  return at < 0 ? '' : path.slice(0, at);
}

/** A file's Excel row: the row's fields (the same values and checks the results page shows), with the full NIK in
 *  place of the masked one (owner decision 2026-11-02: the data output carries it whole). */
function fileRow(file: JobFile, niks: ReadonlyMap<string, string>): WorkbookCell[] {
  const vendor = vendorOf(file.path);
  const vendorCell = vendor ? cell(vendor) : cell(null, 'File tidak berada di dalam folder vendor');
  const row = file.state === 'done' ? file.row : undefined;
  if (!row) {
    const why =
      file.state === 'error'
        ? `File tidak terbaca: ${FAILED[file.error ?? ''] ?? file.error ?? 'kesalahan'}`
        : 'File belum diproses';
    const missing = cell(null, why);
    return [missing, vendorCell, EMPTY, missing, missing, EMPTY, missing, missing, missing, missing, cell(file.path)];
  }
  const { fields } = row;
  const nik = fields.nik.value === null ? null : (niks.get(file.path) ?? fields.nik.value);
  return [
    cell(nik, fields.nik.check && noteText(fields.nik.check, 'id')),
    excel(fields.vendor),
    excel(fields.entity),
    excel(fields.name),
    excel(fields.position),
    EMPTY, // TANGGAL JOIN AWAL: not in the contract
    excel(fields.start),
    excel(fields.end),
    excel(fields.months),
    excel(fields.signature),
    cell(file.path),
  ];
}

const FORBIDDEN = /[[\]:*?/\\]/g;

/** A valid, unique Excel sheet name: at most 31 characters, none of []:*?/\, no apostrophe at either end. */
function sheetName(vendor: string, taken: Set<string>): string {
  const base = vendor.replace(FORBIDDEN, ' ').replace(/\s+/g, ' ').trim().replace(/^'+|'+$/g, '') || NO_VENDOR_SHEET;
  for (let n = 1; ; n++) {
    const suffix = n === 1 ? '' : ` (${n})`;
    const name = base.slice(0, 31 - suffix.length).trimEnd() + suffix;
    if (!taken.has(name.toLocaleLowerCase())) {
      taken.add(name.toLocaleLowerCase());
      return name;
    }
  }
}

/** The "data output" workbook of a job: every file on ALL, then one sheet per vendor folder. `niks` holds the full
 *  NIK by file path; the workbook is PII and is handled like the contracts themselves. */
export function workbookSpec(job: Job, niks: ReadonlyMap<string, string> = new Map()): WorkbookSpec {
  const byVendor = new Map<string, WorkbookCell[][]>();
  const all: WorkbookCell[][] = [];
  for (const file of job.files) {
    const row = fileRow(file, niks);
    all.push(row);
    const vendor = vendorOf(file.path);
    const rows = byVendor.get(vendor) ?? [];
    rows.push(row);
    byVendor.set(vendor, rows);
  }
  const taken = new Set([ALL_SHEET.toLocaleLowerCase()]);
  const vendors = [...byVendor.keys()].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b, 'id')));
  return {
    columns: COLUMNS,
    sheets: [
      { name: ALL_SHEET, rows: all },
      ...vendors.map((vendor) => ({ name: sheetName(vendor, taken), rows: byVendor.get(vendor) ?? [] })),
    ],
  };
}

const UNSAFE_IN_FILENAME = /[<>:"/\\|?*\u0000-\u001f]/g; // what Windows refuses in a file name
const MAX_SCOPE = 80;

/** `2 Nov 2026 13.45` in WIB, whatever the server's own zone; a dot, since Windows refuses `:` in a file name. */
function stamp(iso: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Jakarta',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new Date(iso))
      .map((p) => [p.type, p.value]),
  );
  return `${Number(parts.day)} ${BULAN_SINGKAT[Number(parts.month) - 1]} ${parts.year} ${parts.hour}.${parts.minute}`;
}

/** What the job covered: one folder by its path, several folders of one vendor, or the number of vendors. Folder names
 *  are vendor and period names, never an employee's. */
function scope(job: Job): string {
  if (job.folders.length === 1) return job.folders[0] ? job.folders[0].split('/').join(' - ') : 'Semua vendor';
  const vendors = new Set(job.files.map((f) => vendorOf(f.path)).filter(Boolean));
  const [only] = vendors;
  if (vendors.size === 1 && only) return `${only} (${job.folders.length} folder)`;
  return `${vendors.size} vendor`;
}

/** `Hasil OCR PKWT - PT Bumi Dinamika - Februari 2032 - Mei 2032 - 2 Nov 2026 13.45.xlsx` */
export function exportFileName(job: Job): string {
  let what = scope(job).replace(UNSAFE_IN_FILENAME, ' ').replace(/\s+/g, ' ').trim();
  if (what.length > MAX_SCOPE) what = `${what.slice(0, MAX_SCOPE).trimEnd()}…`;
  return `Hasil OCR PKWT - ${what || 'Semua vendor'} - ${stamp(job.finishedAt ?? job.startedAt)}.xlsx`;
}
