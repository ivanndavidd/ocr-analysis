import { type ContractFields, type ContractRow, type FieldValue, type Note, note, noteDate, noteText } from '@contract-ocr/shared-types';

// One rule set for what is doubtful in a contract's data: the results page colours these cells and the Excel colours
// the same cells with the same note, so a person checks once and finds the same thing in both. A note is a key with its
// parameters (packages/shared-types/src/notes.ts): the page renders it in the reader's language, the Excel in Indonesian.

/** Everything a row knows except its fields, which are derived from the rest. */
export type RowFacts = Omit<ContractRow, 'fields'>;

const LOW_CONFIDENCE = 0.8; // an OCR-read title, or Claude's own confidence, below this is shown but coloured
const unsure = (confidence: number | null) => confidence !== null && confidence < LOW_CONFIDENCE;
export const BULAN_SINGKAT = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

const sure = (value: string | number | null, basis: Note): FieldValue => ({ value, check: null, basis });
const doubt = (value: string | number | null, check: Note): FieldValue => ({ value, check, basis: null });

/** `2026-03-31` → `31 Mar 2026`, as people write it. */
export const tanggal = (iso: string): string => noteDate(iso, 'id');

/** The reason the no-OCR mode adds: every value came from Claude reading the whole first and last page. */
export const FULL_PAGES = 'read_full_pages';
const fullPages = (row: RowFacts) => row.reasons.includes(FULL_PAGES);

/** The no-OCR mode: Claude read the NIK from the whole first page, with the birth date and gender printed there; the
 *  NIK is trusted when both confirm it and Claude was sure of its digits. */
function nikFromPage(row: RowFacts): FieldValue {
  const { status, masked, birthDate, gender } = row.nik;
  if (status === 'invalid') return doubt(null, note('nik.page_invalid'));
  if (!masked) return doubt(null, note('nik.page_missing'));
  if (birthDate === 'mismatch' || gender === 'mismatch') return doubt(masked, note('nik.page_mismatch'));
  const confidence = row.readConfidence.nik;
  if (birthDate === 'match' && gender === 'match' && confidence !== null && !unsure(confidence)) {
    return sure(masked, note('nik.page_confirmed'));
  }
  return doubt(masked, note('nik.page_check'));
}

/** The local reading first; Claude reads a crop of the NIK line only when that reading is unsure, and its NIK passes
 *  the same birth-date and gender checks before it is used. */
function nik(row: RowFacts): FieldValue {
  if (fullPages(row)) return nikFromPage(row);
  const { status, masked, birthDate, gender, fromOcr, claude, secondReader } = row.nik;
  if (status === 'conflict') return doubt(null, note('nik.conflict'));
  const bothMatch = birthDate === 'match' && gender === 'match';
  if (claude === 'used' && masked) {
    return bothMatch ? sure(masked, note('nik.crop_confirmed')) : doubt(masked, note('nik.crop_check'));
  }
  if (claude === 'different') {
    return masked ? doubt(masked, note('nik.claude_differs')) : doubt(null, note('nik.claude_mismatch'));
  }
  if (status === 'invalid') return doubt(null, note('nik.invalid'));
  if (!masked) return doubt(null, note('nik.missing'));
  if (birthDate === 'mismatch' || gender === 'mismatch') return doubt(masked, note('nik.mismatch'));
  // the second local OCR reads an unsure scan again; the same value from both engines is trusted, another one is not
  if (fromOcr && !bothMatch) {
    return doubt(masked, note(secondReader === 'different' ? 'nik.scan_second_differs' : 'nik.scan_check'));
  }
  if (row.reasons.includes('nik_digits_repaired')) return doubt(masked, note('nik.digits_repaired'));
  if (bothMatch) return sure(masked, note('nik.matches_contract'));
  if (secondReader === 'same') return sure(masked, note('nik.two_engines'));
  if (claude === 'same') return sure(masked, note('nik.claude_same'));
  return sure(masked, note('nik.digital'));
}

/** NAMA VENDOR is the vendor folder (`root/<vendor>/<masa kontrak>/<pdf>`), the outsourcing company that employs the
 *  employee; the contract always prints it, so the folder's name is looked up there first, and only when it is not
 *  printed is it compared with the employer company Claude read. */
function vendor(row: RowFacts): FieldValue {
  const named = row.vendorFromFolder;
  if (!named) return doubt(null, note('vendor.no_folder'));
  if (row.vendorInDocument === null) return doubt(named, note('vendor.unchecked'));
  const company = row.vendorBasis === 'claude' ? row.vendorFromDocument : null;
  if (!row.vendorInDocument) {
    if (company) return doubt(named, note('vendor.claude_reads', { company }));
    return doubt(named, note('vendor.not_found'));
  }
  return sure(named, note(company ? 'vendor.claude_same' : 'vendor.printed'));
}

/** ENTITAS is only ever in the filename (`Nama - Masa Kontrak - ENTITAS`); the document is not checked for it. */
function entity(row: RowFacts): FieldValue {
  if (!row.entityFromFilename) return doubt(null, note('entity.missing'));
  return sure(row.entityFromFilename, note('entity.filename'));
}

/** Claude's reading checks the filename's name against the employee party of the document. When both name the same
 *  person the filename's spelling is kept, as with the patterns, since OCR can misspell a scanned name that Claude
 *  then copies faithfully. */
function nameFromClaude(row: RowFacts, read: string): FieldValue {
  const named = row.filenameStandard ? row.nameFromFilename : null;
  if (!named) return doubt(read, note('name.claude_no_format'));
  if (row.nameMatch === 'different') return doubt(read, note('name.filename_says', { name: named }));
  if (row.nameMatch === 'similar') return doubt(named, note('name.claude_reads', { name: read }));
  if (unsure(row.readConfidence.name)) return doubt(named, note('name.claude_unsure'));
  return sure(named, note('name.claude_same'));
}

function name(row: RowFacts): FieldValue {
  if (row.nameFromDocument) return nameFromClaude(row, row.nameFromDocument);
  const value = row.nameFromFilename;
  if (!value) return doubt(null, note('name.unread'));
  if (!row.filenameStandard) return doubt(value, note('name.no_format'));
  if (row.nameInDocument === false) return doubt(value, note('name.not_in_identity'));
  if (row.nameInDocument === null) return doubt(value, note('name.unchecked'));
  if (row.nameOnSignaturePage) return sure(value, note('name.on_signature_page'));
  return sure(value, note('name.on_identity_page'));
}

function position(row: RowFacts): FieldValue {
  const { status, value, confidence, agreement, fromUnclearScan, fixedByClaude, source, secondReader } = row.position;
  if (status === 'conflict') return doubt(null, note('position.conflict'));
  if (!value) return doubt(null, note('position.missing'));
  // Not printed letter for letter: HR checks the fix against the PDF
  if (fixedByClaude) return doubt(value, note('position.fixed_by_claude'));
  if (agreement === 'different') return doubt(value, note('position.patterns_differ'));
  // Claude read the same OCR text, so agreeing with it confirms nothing (2026-11-11: `Cleaning Service 1` went green)
  if (agreement === 'same' && fromUnclearScan) return doubt(value, note('position.unclear_same_text'));
  if (source === 'pattern' && secondReader === 'same') return sure(value, note('position.two_engines'));
  if (unsure(confidence)) {
    if (source === 'claude') return doubt(value, note('position.claude_unsure'));
    return doubt(value, note(secondReader === 'different' ? 'position.unclear_second_differs' : 'position.unclear'));
  }
  if (agreement === 'same') return sure(value, note('position.claude_agrees'));
  return sure(value, note(source === 'claude' ? 'position.claude' : 'position.printed'));
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
  const same = note(row.periodFromSecondReader ? 'period.second_reader_same' : 'period.same');
  const one = (fromDoc: string | null, fromName: string | null): FieldValue => {
    if (fromDoc) {
      if (!fromName) return doubt(fromDoc, note('period.document_only'));
      if (fromName !== fromDoc) return doubt(fromDoc, note('period.filename_says', { date: fromName }));
      return sure(fromDoc, same);
    }
    if (doc.status === 'conflict') return doubt(null, note('period.conflict'));
    if (fromName) return doubt(fromName, note('period.from_filename'));
    return doubt(null, note('period.missing'));
  };
  let start = one(doc.start, named?.start ?? null);
  let end = one(doc.end, named?.end ?? null);
  if (unsure(row.readConfidence.period)) {
    const check = note('period.claude_unsure');
    if (doc.start) start = doubt(doc.start, check);
    if (doc.end) end = doubt(doc.end, check);
  }

  const datesDoubtful = Boolean(start.check || end.check);
  const counted =
    typeof start.value === 'string' && typeof end.value === 'string' ? wholeMonths(start.value, end.value) : null;
  let months: FieldValue;
  if (doc.durationMonths !== null && counted !== null && doc.durationMonths !== counted) {
    months = doubt(doc.durationMonths, note('months.differ', { stated: doc.durationMonths, counted }));
  } else if (doc.durationMonths !== null) {
    months = datesDoubtful
      ? doubt(doc.durationMonths, note('months.dates_doubtful'))
      : sure(doc.durationMonths, note(counted !== null ? 'months.matches_dates' : 'months.stated'));
  } else if (counted !== null) {
    months = datesDoubtful ? doubt(counted, note('months.counted_doubtful')) : sure(counted, note('months.counted'));
  } else if (doc.status === 'conflict') {
    months = doubt(null, note('months.conflict'));
  } else if (typeof start.value === 'string' && typeof end.value === 'string') {
    months = doubt(null, note('months.not_whole'));
  } else {
    months = doubt(null, note('months.incomplete'));
  }
  return { start, end, months };
}

/** The no-OCR mode: Claude alone looked at the last page, and decided only above the same cutoffs as for a crop. */
function signatureFromPage(row: RowFacts, at: { page: number | null }): FieldValue {
  if (row.signature === 'signed') return sure('YES', note('signature.page_signed', at));
  if (row.signature === 'unsigned') return sure('NO', note('signature.page_unsigned', at));
  return doubt(null, note('signature.page_unsure', at));
}

function signature(row: RowFacts): FieldValue {
  const page = row.detail.signaturePage;
  const at = { page: page || null };
  if (fullPages(row)) return signatureFromPage(row, at);
  if (row.reasons.includes('signature_llm_differs')) return doubt(null, note('signature.claude_sees_signature', at));
  if (row.reasons.includes('signature_llm_sees_none')) return doubt(null, note('signature.claude_sees_none', at));
  if (row.signature === 'signed' || row.signature === 'unsigned') {
    const signed = row.signature === 'signed';
    const value = signed ? 'YES' : 'NO';
    if (row.reasons.includes('signature_page_guessed')) return doubt(value, note('signature.page_guessed', at));
    if (row.reasons.includes('signature_from_llm')) {
      return sure(value, note(signed ? 'signature.claude_signed' : 'signature.claude_unsigned'));
    }
    if (row.reasons.includes('signature_llm_agrees')) {
      return sure(value, note(signed ? 'signature.claude_agrees_signed' : 'signature.claude_agrees_unsigned'));
    }
    if (row.reasons.includes('signature_not_clear')) return doubt(value, note('signature.unclear', at));
    return sure(value, note(signed ? 'signature.signed' : 'signature.unsigned'));
  }
  if (!page) return doubt(null, note('signature.page_missing'));
  return doubt(null, note('signature.uncertain', at));
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

/** A note as a saved row holds it: a key (2026-11-16 on), the Indonesian text rows held before, or none. */
type SavedNote = Note | string | null;
export type SavedFields = Record<keyof ContractFields, Omit<FieldValue, 'check' | 'basis'> & { check: SavedNote; basis: SavedNote }>;

const legacy = (text: string): Note => note('legacy', { text });

/** The fields of a saved row, every note a key. A text saved before the notes had keys gets its key back when the
 *  current rules give the same field that same text, so it follows the reader's language; any other text (an older
 *  rule's wording) is kept as it was written. Values and yellow cells stay as saved: nothing is judged again. */
export function keyedFields(row: RowFacts & { fields: SavedFields }): ContractFields {
  let now: ContractFields | null | undefined;
  const current = () => {
    if (now === undefined) {
      try {
        now = fieldsOf(row);
      } catch {
        now = null; // a row saved by an older backend may lack a fact today's rules read
      }
    }
    return now;
  };
  const keyed = (saved: SavedNote, fresh: () => Note | null | undefined): Note | null => {
    if (typeof saved !== 'string') return saved;
    const same = fresh();
    return same && noteText(same, 'id') === saved ? same : legacy(saved);
  };
  const fields = {} as ContractFields;
  for (const [key, saved] of Object.entries(row.fields) as [keyof ContractFields, SavedFields[keyof ContractFields]][]) {
    fields[key] = {
      value: saved.value,
      check: keyed(saved.check, () => current()?.[key].check),
      basis: keyed(saved.basis, () => current()?.[key].basis),
    };
  }
  return fields;
}

/** Notes saved as text alone, in the rows saved before the whole reading was kept (2026-11-05). */
export const legacyNote = (text: string | null | undefined): Note | null => (text ? legacy(text) : null);
