import type { ClaudeCall, ClaudeCallKind, ClaudeState, ReadMode } from '@contract-ocr/shared-types';
import { formatCount, totalTokens } from './format.ts';

/** How "Jalankan OCR" reads, by whether Claude helps the patterns (the backend's `claude` state). */
export const FLOW_LABEL: Record<ClaudeState, string> = {
  off: 'Pola teks',
  starting: 'Pola teks + Claude',
  on: 'Pola teks + Claude',
  unavailable: 'Pola teks (Claude tidak tersedia)',
};

const PATTERNS =
  'Dibaca di laptop ini: nama dan periode dari nama file serta vendor dari nama folder, dicocokkan dengan teks dokumen; NIK dan tanda tangan diperiksa di sini dulu.';
const WITH_CLAUDE =
  ' Claude membantu: membaca posisi dan perusahaan pemberi kerja dari teks halaman identitas yang sudah disamarkan, nama dan periode bila pola teks tidak bisa memastikannya, potongan gambar baris NIK bila pemeriksaan di laptop ini ragu, serta potongan area tanda tangan untuk setiap scan dan foto (PDF teks bila ragu) sebagai pemeriksa kedua.';

export function flowDescription(state: ClaudeState | null): string {
  return state === 'on' || state === 'starting' ? PATTERNS + WITH_CLAUDE : PATTERNS;
}

/** Why Claude is not helping, for the run area; null when it is, or is still starting. */
export const CLAUDE_HINT: Partial<Record<ClaudeState, string>> = {
  off: 'Claude dimatikan di backend (CLAUDE_MODE=off): tidak ada isi kontrak yang dikirim keluar.',
  unavailable:
    'Claude belum bisa dipakai, jadi OCR berjalan dengan pola teks saja. Isi ANTHROPIC_API_KEY di .env backend, atau login dengan "claude auth login" di komputer ini, lalu jalankan ulang backend.',
};

/** The warning in the run confirmation when Claude may be sent something; null when nothing leaves the laptop. */
export function claudeConsent(state: ClaudeState | null, pdfs: number): string | null {
  if (state !== 'on' && state !== 'starting') return null;
  return `Dari ${pdfs} PDF, yang dikirim ke Claude: teks halaman identitas yang sudah disamarkan (tanpa NIK, alamat, dan gaji), potongan gambar baris NIK bila NIK ragu, dan potongan area tanda tangan untuk setiap scan dan foto (PDF teks bila tanda tangannya ragu); tidak pernah satu halaman utuh. Setiap panggilan dikenai biaya pemakaian Claude.`;
}

/** The one line a run confirmation always shows when Claude may be sent something; `claudeConsent` spells it out under
 *  the confirmation's "Detail". Null when nothing leaves the laptop. */
export function claudeNotice(state: ClaudeState | null): string | null {
  if (state !== 'on' && state !== 'starting') return null;
  return 'Sebagian isi PDF dikirim ke Claude, tidak pernah satu halaman utuh. Dikenai biaya pemakaian.';
}

export const CALL_LABEL: Record<ClaudeCallKind, string> = {
  text: 'teks halaman identitas',
  nik: 'potongan baris NIK',
  signature: 'potongan area tanda tangan',
  pages: 'halaman pertama dan terakhir utuh',
};

/** `teks halaman identitas (12.475 token), potongan baris NIK (2.010 token)`: what was sent for one file, in order. */
export const callsLabel = (calls: ClaudeCall[]) =>
  calls
    .map(({ kind, tokens }) => (tokens ? `${CALL_LABEL[kind]} (${formatCount(totalTokens(tokens))} token)` : CALL_LABEL[kind]))
    .join(', ');

/** A job in the lists: how it read, and whether Claude was helping when it ran. */
export const jobFlowLabel = (claude: boolean, mode: ReadMode = 'ocr') =>
  mode === 'full_page' ? 'tanpa OCR, halaman utuh ke Claude' : claude ? 'dibantu Claude' : 'pola teks saja';

// The two ways a run reads. The OCR mode is the main flow; the no-OCR mode sends the first and last page of every PDF
// whole and unmasked to Claude, a backup the owner and mentor test while sending whole pages is not approved yet
// (2026-11-04). The switch shows until they decide.
export const MODES: ReadMode[] = ['ocr', 'full_page'];
export const MODE_LABEL: Record<ReadMode, string> = { ocr: 'Dengan OCR', full_page: 'Tanpa OCR' };

export const FULL_PAGE_FLOW = 'Halaman utuh ke Claude (uji coba)';
export const FULL_PAGE_DESCRIPTION =
  'Tidak memakai OCR di laptop ini. Halaman pertama (dianggap halaman identitas) dan halaman terakhir (dianggap halaman tanda tangan) setiap PDF dikirim utuh sebagai gambar ke Claude, yang membaca nama, NIK, posisi, periode, perusahaan pemberi kerja, dan tanda tangan. Di laptop ini NIK dicocokkan dengan tanggal lahir dan jenis kelamin yang tercetak, nama dan periode dengan nama file, dan perusahaan dengan folder vendor.';

/** The warning in the run confirmation of the no-OCR mode, which sends whole, unmasked pages. */
export const fullPageConsent = (pdfs: number) =>
  `Dari ${pdfs} PDF, halaman pertama dan halaman terakhir dikirim UTUH ke Claude tanpa disamarkan: nama, NIK, alamat, tanggal lahir, dan isi lain di halaman itu ikut terkirim. Cara ini belum disetujui untuk dipakai; gunakan hanya untuk uji coba. Setiap PDF satu panggilan Claude (sekitar 15 ribu token) dan dikenai biaya pemakaian.`;

/** The no-OCR mode's warning in one line, always shown; `fullPageConsent` spells it out under "Detail". */
export const FULL_PAGE_NOTICE =
  'Halaman pertama dan terakhir dikirim UTUH ke Claude tanpa disamarkan, NIK ikut terkirim. Belum disetujui: hanya untuk uji coba.';

/** Why the no-OCR mode cannot run now; null when it can. Claude is its only reader, so it needs Claude ready. */
export function fullPageBlocked(state: ClaudeState | null): string | null {
  if (state === 'on') return null;
  if (state === 'starting') return 'Tunggu Claude siap untuk membaca tanpa OCR.';
  return 'Tanpa OCR butuh Claude, yang tidak tersedia di backend ini. Pilih Dengan OCR.';
}

const MODE_KEY = 'contract-ocr.read-mode';

/** The mode picked in this tab; a new tab starts with the OCR mode. */
export function loadMode(): ReadMode {
  try {
    return sessionStorage.getItem(MODE_KEY) === 'full_page' ? 'full_page' : 'ocr';
  } catch {
    return 'ocr'; // storage blocked
  }
}

export function saveMode(mode: ReadMode): void {
  try {
    sessionStorage.setItem(MODE_KEY, mode);
  } catch {
    // storage blocked: the choice still holds on this page
  }
}
