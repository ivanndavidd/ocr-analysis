import type { ContractRow, FieldKey, FieldValue, JobFile } from '@contract-ocr/shared-types';
import { formatDate, formatNik } from './format.ts';
import { noteLabel, t } from './i18n.ts';

/** The Excel's data columns, in its order. */
export const FIELDS: FieldKey[] = ['nik', 'vendor', 'entity', 'name', 'position', 'start', 'end', 'months', 'signature'];

export const fieldLabel = (key: FieldKey) => t().fields.labels[key];

/** The same column in the Excel, so a person can find it there. The Excel follows HR's Indonesian template in either
 *  language, so these stay as its headers. */
export const EXCEL_HEADER: Record<FieldKey, string> = {
  nik: 'NIK KTP',
  vendor: 'NAMA VENDOR',
  entity: 'ENTITAS',
  name: 'NAMA KARYAWAN',
  position: 'POSISI',
  start: 'START PERIODE',
  end: 'END PERIODE',
  months: 'MONTH',
  signature: 'SIGN',
};

/** Field filters: the three period columns are checked together, against the same page. */
export type FieldGroup = 'nik' | 'vendor' | 'entity' | 'name' | 'position' | 'period' | 'signature';
export const FIELD_GROUPS: FieldGroup[] = ['nik', 'vendor', 'entity', 'name', 'position', 'period', 'signature'];
export const groupLabel = (group: FieldGroup) => t().fields.groups[group];
const GROUP_OF: Record<FieldKey, FieldGroup> = {
  nik: 'nik',
  vendor: 'vendor',
  entity: 'entity',
  name: 'name',
  position: 'position',
  start: 'period',
  end: 'period',
  months: 'period',
  signature: 'signature',
};

/** What the person has to do with a file: nothing, check yellow values, retry it, or wait. */
export type RowState = 'ready' | 'check' | 'failed' | 'pending';

export const toCheck = (row: ContractRow): FieldKey[] => FIELDS.filter((key) => row.fields[key].check !== null);

export function rowState(file: JobFile): RowState {
  if (file.state === 'error') return 'failed';
  if (file.state !== 'done' || !file.row) return 'pending';
  return toCheck(file.row).length ? 'check' : 'ready';
}

export const groupsToCheck = (row: ContractRow): Set<FieldGroup> => new Set(toCheck(row).map((key) => GROUP_OF[key]));

/** The full NIK the backend gave for a file, while it is still the one its row shows (a retried file can read
 *  another, and the page only refreshes its NIKs when a row has one it does not know). */
export function fullNik(row: ContractRow | undefined, nik: string | undefined): string | null {
  const masked = row?.nik.masked;
  return masked && nik && nik.length === masked.length && nik.endsWith(masked.slice(-4)) ? nik : null;
}

/** A value as the page shows it; null when there is none. */
export function displayValue(key: FieldKey, field: FieldValue): string | null {
  const { value } = field;
  if (value === null || value === '') return null;
  if (key === 'nik') return formatNik(String(value));
  if (key === 'start' || key === 'end') return formatDate(String(value));
  if (key === 'months') return t().fields.months(String(value));
  if (key === 'signature') return value === 'YES' ? t().fields.signed : value === 'NO' ? t().fields.unsigned : String(value);
  return String(value);
}

/** A field's yellow note in the current language; null when the value needs no check. */
export const checkNote = (field: FieldValue): string | null => (field.check ? noteLabel(field.check) : null);
/** Why a value without a check can be trusted, in the current language; null when there is no such note. */
export const basisNote = (field: FieldValue): string | null => (field.basis ? noteLabel(field.basis) : null);

/** The results table's one period cell: the three Excel columns as a date range with its length under it, to check
 *  when any of them is, with each distinct reason once. */
export function periodCell(row: ContractRow): { range: string | null; months: string | null; checks: string[]; basis: string | null } {
  const { start, end, months } = row.fields;
  const from = displayValue('start', start);
  const to = displayValue('end', end);
  const checks = [...new Set([start, end, months].map(checkNote).filter((check) => check !== null))];
  return {
    range: from || to ? `${from ?? '?'} – ${to ?? '?'}` : null,
    months: displayValue('months', months),
    checks,
    basis: basisNote(start) ?? basisNote(end),
  };
}

/** The page to look at for a field: the identity page for who the person is and who employs them, the period page
 *  for the dates, the signature page for the signature; none for the entity, which only the filename holds. */
export function fieldPage(key: FieldKey, row: ContractRow): number | null {
  const { identityPages, signaturePage } = row.detail;
  if (key === 'entity') return null;
  if (key === 'signature') return signaturePage;
  if (key === 'start' || key === 'end' || key === 'months') return identityPages.at(-1) ?? null;
  return identityPages[0] ?? null;
}
