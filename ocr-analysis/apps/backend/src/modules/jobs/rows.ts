import type {
  ClaudeCall,
  ClaudeCallKind,
  ContractRow,
  DateRange,
  DocumentPeriod,
  NikCheck,
  NikInfo,
  NikStatus,
  PositionInfo,
  Reader,
  TokenUsage,
} from '@contract-ocr/shared-types';
import type { ExtractorFileResult, ExtractorPeriod, NikCheckCode, ParsedFilename, TokenCounts } from '../workers/protocol.ts';
import { FULL_PAGES, type RowFacts, fieldsOf } from './fields.ts';

const range = (start: string | null | undefined, end: string | null | undefined): DateRange | null =>
  start && end ? { start, end } : null;

function documentPeriod(read: (ExtractorPeriod & { ambigu?: boolean }) | null | undefined): DocumentPeriod {
  const start = read?.tanggal_mulai ?? null;
  const end = read?.tanggal_selesai ?? null;
  const durationMonths = read?.durasi_bulan ?? null;
  const status = read?.ambigu
    ? 'conflict'
    : start && end
      ? 'found'
      : start || end || durationMonths
        ? 'partial'
        : 'missing';
  return { status, start, end, durationMonths };
}

const NIK_STATUS: Record<string, NikStatus> = {
  ditemukan: 'found',
  bertentangan: 'conflict',
  tidak_valid: 'invalid',
  tidak_ditemukan: 'missing',
};
const NIK_CHECK: Record<NikCheckCode, NikCheck> = { cocok: 'match', tidak_cocok: 'mismatch', tidak_ada: 'absent' };
const SECOND_READER: Record<string, NonNullable<NikInfo['secondReader']>> = {
  sama: 'same',
  beda: 'different',
  tidak_terbaca: 'unreadable',
};
const NIK_CLAUDE: Record<string, NikInfo['claude']> = {
  sama: 'same',
  dipakai: 'used',
  beda: 'different',
  tidak_terbaca: 'unreadable',
};

/** The worker's NIK when it is 16 digits; anything else is dropped rather than stored or shown. */
export function fullNik(result: ExtractorFileResult): string | null {
  return typeof result.nik === 'string' && /^\d{16}$/.test(result.nik) ? result.nik : null;
}

export const maskNik = (nik: string) => '*'.repeat(nik.length - 4) + nik.slice(-4);

function nikInfo(result: ExtractorFileResult): NikInfo {
  const nik = fullNik(result);
  const log = result.log.nik;
  const status = NIK_STATUS[log?.status ?? ''] ?? 'missing';
  return {
    status: nik ? 'found' : status === 'found' ? 'invalid' : status, // a "found" NIK that is not 16 digits
    masked: nik ? maskNik(nik) : null,
    birthDate: NIK_CHECK[log?.tanggal_lahir ?? 'tidak_ada'] ?? 'absent',
    gender: NIK_CHECK[log?.jenis_kelamin ?? 'tidak_ada'] ?? 'absent',
    fromOcr: nik !== null && result.log.alasan.includes('nik_from_ocr'),
    claude: NIK_CLAUDE[log?.llm ?? ''] ?? null,
    secondReader: SECOND_READER[log?.pembaca_kedua ?? ''] ?? null,
  };
}

const POSITION_STATUS: Record<string, PositionInfo['status']> = {
  ditemukan: 'found',
  bertentangan: 'conflict',
  tidak_ditemukan: 'missing',
};
const MAX_POSITION = 60; // the extractor's own limit; anything longer is not a title

const confidenceOf = (result: ExtractorFileResult, key: string): number | null => {
  const value = result.confidence?.[key];
  return typeof value === 'number' ? value : null;
};

function positionInfo(result: ExtractorFileResult): PositionInfo {
  const value = typeof result.posisi === 'string' ? result.posisi.trim() : '';
  if (value && value.length <= MAX_POSITION) {
    const agreed = result.log.posisi?.cocok_llm;
    const agreement = agreed === true ? 'same' : agreed === false ? 'different' : null;
    // Claude's verified title replaces the local one; it was compared with it, or the local reading found none
    const source = agreement !== null || result.log.posisi?.sumber === 'llm' ? 'claude' : 'pattern';
    const secondReader = SECOND_READER[result.log.posisi?.pembaca_kedua ?? ''] ?? null;
    return { status: 'found', value, confidence: confidenceOf(result, 'posisi'), agreement, source, secondReader };
  }
  const status = POSITION_STATUS[result.log.posisi?.status ?? ''] ?? 'missing';
  return { status: status === 'found' ? 'missing' : status, value: null, confidence: null, agreement: null, source: null };
}

// cocok/mirip: the name is in the document (offline, fuzzy on the identity page; with the LLM, its verified name)
const NAME_CHECK: Record<string, boolean> = { cocok: true, mirip: true, tidak_cocok: false };
const NAME_MATCH = { cocok: 'same', mirip: 'similar', tidak_cocok: 'different' } as const;

const VENDOR_CHECK: Record<string, boolean> = { cocok: true, tidak_cocok: false };
const VENDOR_BASIS: Record<string, 'text' | 'claude'> = { teks: 'text', llm: 'claude' };
const CALLS: Record<string, ClaudeCallKind> = { teks: 'text', nik: 'nik', ttd: 'signature', halaman: 'pages' };
const SIGNATURE_LOOK = new Set(['signed', 'unsigned', 'unclear']);

function claudeCalls(result: ExtractorFileResult): ClaudeCall[] {
  const calls = Array.isArray(result.log.panggilan_llm) ? result.log.panggilan_llm : [];
  const tokens = Array.isArray(result.log.token_panggilan) ? result.log.token_panggilan : [];
  return calls.flatMap((call, at) => {
    const kind = CALLS[call];
    return kind ? [{ kind, tokens: tokensOf(tokens[at]) }] : [];
  });
}

/** What Claude saw in the crop of the signature area; null when no crop was sent or the call failed. */
function signatureByClaude(result: ExtractorFileResult): ContractRow['signatureByClaude'] {
  const look = result.log.tanda_tangan;
  const status = look?.llm ?? '';
  if (!SIGNATURE_LOOK.has(status)) return null;
  const confidence = typeof look?.llm_conf === 'number' ? look.llm_conf : 0;
  return { status: status as 'signed' | 'unsigned' | 'unclear', confidence };
}

const count = (value: unknown) => (typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : 0);

/** The worker's token counts; anything that is not a whole count reads as 0. */
function tokensOf(used: TokenCounts | null | undefined): TokenUsage | null {
  if (!used || typeof used !== 'object') return null;
  return {
    input: count(used.masuk),
    cacheWrite: count(used.cache_tulis),
    cacheRead: count(used.cache_baca),
    output: count(used.keluar),
  };
}

/** The period the document states, as the row reads it. With the patterns that is the local regex's reading, or, where
 *  the regex read no clear period, the filename's dates once both are printed in the document (the extractor's
 *  `validasi_nama_file.periode` says so); with Claude, its verified reading. */
function readPeriod(result: ExtractorFileResult, reader: Reader, fromName: DateRange | null): ExtractorPeriod & { ambigu?: boolean } | null {
  if (reader === 'claude') return result.log.periode_llm ?? null;
  const regex = result.log.periode_regex;
  const printed = fromName && result.log.validasi_nama_file?.periode === 'cocok' && !regex?.ambigu;
  if (printed && !(regex?.tanggal_mulai && regex?.tanggal_selesai)) {
    return { tanggal_mulai: fromName.start, tanggal_selesai: fromName.end, durasi_bulan: regex?.durasi_bulan ?? null };
  }
  return regex ?? null;
}

/** The extractor's FileResult reduced to what the results table shows, with its Excel values and their checks. With
 *  the patterns, the name comes from the filename and the period is checked against the document; with Claude, both
 *  come from its verified reading. `vendor` is the vendor folder the file sits in. The full NIK is never part of a
 *  row. */
export function toRow(result: ExtractorFileResult, filename: ParsedFilename, vendor: string | null = null): ContractRow {
  const reader: Reader = result.log.pembaca === 'llm' ? 'claude' : 'pattern';
  const fromName = range(filename.start, filename.end);
  const read = readPeriod(result, reader, fromName);
  const fromDocument = range(read?.tanggal_mulai, read?.tanggal_selesai);
  const docName = typeof result.nama_karyawan === 'string' ? result.nama_karyawan.trim() : '';
  const nameCheck = result.log.validasi_nama_file?.nama;
  const fullPages = result.log.alasan.includes(FULL_PAGES);
  const facts: RowFacts = {
    file: result.file,
    reader,
    readByFallback: result.log.llm_cadangan === true,
    claudeCalls: claudeCalls(result),
    tokens: tokensOf(result.log.token_llm),
    vendorFromFolder: vendor?.trim() || null,
    vendorInDocument: VENDOR_CHECK[result.log.validasi_vendor ?? ''] ?? null,
    vendorBasis: VENDOR_BASIS[result.log.vendor_dari ?? ''] ?? null,
    vendorFromDocument: result.vendor_dokumen?.trim() || null,
    entityFromFilename: filename.entity?.trim() || null,
    nameFromFilename: filename.name || null,
    nameFromDocument: reader === 'claude' && docName ? docName : null,
    nameInDocument: NAME_CHECK[nameCheck ?? ''] ?? null,
    nameOnSignaturePage: nameCheck === 'cocok' && result.log.validasi_nama_file?.nama_halaman === 'tanda_tangan',
    nameMatch: reader === 'claude' && docName && nameCheck ? NAME_MATCH[nameCheck] : null,
    readConfidence: {
      name: reader === 'claude' ? confidenceOf(result, 'nama') : null,
      period: reader === 'claude' ? confidenceOf(result, 'periode') : null,
      nik: fullPages ? confidenceOf(result, 'nik') : null,
    },
    nik: nikInfo(result),
    position: positionInfo(result),
    filenameStandard: filename.flags.length === 0 && fromName !== null,
    periodFromFilename: fromName,
    periodFromSecondReader: reader === 'pattern' && result.log.validasi_nama_file?.periode_dari === 'pembaca_kedua',
    periodFromDocument: fromDocument,
    documentPeriod: documentPeriod(read),
    periodMatches:
      fromName && fromDocument ? fromName.start === fromDocument.start && fromName.end === fromDocument.end : null,
    signature: result.status_ttd,
    signatureByClaude: signatureByClaude(result),
    decision: result.keputusan,
    reasons: [...new Set([...result.log.alasan, ...result.flags.filter((f) => f !== 'auto_disabled')])],
    detail: {
      identityPages: result.log.p_id,
      signaturePage: result.log.p_ttd,
      pageKinds: result.log.jenis_halaman,
      ocrConfidence: result.log.ocr_conf,
      signatureSource: result.log.tanda_tangan?.sumber ?? null,
      vendorNote: result.catatan_vendor,
    },
  };
  return { ...facts, fields: fieldsOf(facts) };
}
