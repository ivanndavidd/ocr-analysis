// What `contract-extractor serve-stdio` sends back (see apps/extractor/src/contract_extractor/worker.py).
// Only the fields the website reads are typed; the rest of FileResult is ignored.

import type { Decision, ExtractorState, ReadMode, SignatureStatus } from '@contract-ocr/shared-types';

export interface ExtractorPeriod {
  tanggal_mulai: string | null;
  tanggal_selesai: string | null;
  durasi_bulan?: number | null;
}

export interface ExtractorFileResult {
  file: string;
  nama_karyawan?: string | null; // with Claude: its verified reading
  nik?: string | null; // full NIK, kept in backend memory only; never logged, never sent in a job view
  posisi?: string | null;
  vendor_dokumen?: string | null; // with Claude: the employer company it read, after verification
  confidence?: Record<string, number>;
  status_ttd: SignatureStatus | null;
  keputusan: Decision;
  flags: string[];
  catatan_vendor: string | null;
  log: {
    p_id: number[];
    p_ttd: number | null;
    jenis_halaman: Record<string, string>;
    ocr_conf: Record<string, number>;
    periode_regex: (ExtractorPeriod & { ambigu?: boolean }) | null; // ambigu: conflicting candidates, left empty
    pembaca?: 'llm' | 'pola' | null; // who read name and period
    llm_cadangan?: boolean; // Claude read the name and period, since the patterns could not confirm them
    panggilan_llm?: string[]; // every Claude call for the file: teks, nik, ttd
    token_panggilan?: (TokenCounts | null)[]; // each call's tokens, in the same order
    token_llm?: TokenCounts | null; // all calls together
    periode_llm?: ExtractorPeriod | null; // Claude's period after verification, only what the document writes
    tanda_tangan: { sumber: string | null; llm?: string | null; llm_conf?: number | null } | null;
    nik?: {
      status: 'ditemukan' | 'bertentangan' | 'tidak_valid' | 'tidak_ditemukan';
      tanggal_lahir: NikCheckCode;
      jenis_kelamin: NikCheckCode;
      llm?: 'sama' | 'dipakai' | 'beda' | 'tidak_terbaca' | null; // Claude's reading of a crop of the NIK line
      pembaca_kedua?: 'sama' | 'beda' | 'tidak_terbaca' | null; // the second local OCR (RapidOCR) on the same scan
    } | null;
    posisi?: {
      status: 'ditemukan' | 'bertentangan' | 'tidak_ditemukan';
      sumber: 'label' | 'kalimat' | 'llm' | null;
      cocok_llm?: boolean | null; // Claude's title against the local reading
      pembaca_kedua?: 'sama' | 'beda' | 'tidak_terbaca' | null; // the second local OCR (RapidOCR) on the same scan
    } | null;
    validasi_nama_file?: {
      nama?: 'cocok' | 'mirip' | 'tidak_cocok';
      periode?: 'cocok' | 'mirip' | 'tidak_cocok';
      nama_halaman?: 'tanda_tangan'; // the name was confirmed in the signature block, not on the identity page
      periode_dari?: 'pembaca_kedua'; // the second OCR's reading completed a period Tesseract's text left a date of unread
    };
    validasi_vendor?: 'cocok' | 'tidak_cocok' | null; // the vendor folder against the document; null: not checked
    vendor_dari?: 'teks' | 'llm' | null; // what decided it: the page text, or the employer Claude read
    alasan: string[];
  };
}

export type NikCheckCode = 'cocok' | 'tidak_cocok' | 'tidak_ada';

export type TokenCounts = { masuk?: number; cache_tulis?: number; cache_baca?: number; keluar?: number };

export interface ParsedFilename {
  name: string;
  start: string | null;
  end: string | null;
  duration_months: number | null;
  entity?: string | null; // `Nama - Masa Kontrak - ENTITAS`; absent from workers older than the field
  flags: string[];
}

export type ExtractorReply =
  | { ok: true; result: ExtractorFileResult; filename: ParsedFilename }
  | { ok: false; error: string };

/** A file the workers could not answer for: the worker hung, crashed, replied garbage, or none can start. */
export class ExtractorFailure extends Error {
  constructor(
    readonly code: 'timeout' | 'worker_crashed' | 'bad_reply' | 'extractor_unavailable',
    readonly detail: string | null = null,
  ) {
    super(code);
  }
}

export interface ExtractorClient {
  status(): { state: ExtractorState; error: string | null; workers: number };
  /** Whether the latest worker to start has Claude helping (its ready message says so); null before any has. */
  claude(): boolean | null;
  /** `vendor`: the vendor folder the file sits in, checked against the document; null skips that check. */
  extract(filename: string, data: Uint8Array, vendor?: string | null, mode?: ReadMode): Promise<ExtractorReply>;
  close(): Promise<void>;
}
