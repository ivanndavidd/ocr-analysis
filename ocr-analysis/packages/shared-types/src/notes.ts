// The notes on the data-output cells: why a value needs a look (`check`) or why it can be trusted (`basis`), in the
// website's two languages. The backend's rules (apps/backend/src/modules/jobs/fields.ts) pick a note by key and send
// it with its parameters; the results page renders it in the reader's language and the Excel in Indonesian, so a
// yellow cell carries the same note in both. The Indonesian wording is what HR already knows: change it only on purpose.
//
// Runtime values in this types package, because both apps need the same words and a new shared workspace needs Tech
// Architecture approval (AGENTS.md). The texts are plain functions with no dependency.

export type Lang = 'id' | 'en';
export const LANGS: readonly Lang[] = ['id', 'en'];

const BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const EN_DATE = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

/** A calendar date as people write it: `2026-03-31` → `31 Mar 2026` (the Indonesian months are fixed, not ICU's). */
export function noteDate(iso: string, lang: Lang): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return lang === 'id' ? `${d} ${BULAN[m - 1] ?? m} ${y}` : EN_DATE.format(new Date(Date.UTC(y, m - 1, d)));
}

type Page = { page: number | null };
const halaman = (page: number | null) => (page ? ` halaman ${page}` : '');
const onPage = (page: number | null) => (page ? ` on page ${page}` : '');
const lookAt = (page: number | null) => (page ? ` look at page ${page}` : ' take a look');
const months = (n: number) => `${n} ${n === 1 ? 'month' : 'months'}`;

// Two local OCR engines read an unsure scan; the same value from both is trusted, another one is not.
const DUA_MESIN = 'Dua mesin OCR lokal (Tesseract dan RapidOCR)';
const MESIN_KEDUA_LAIN = 'mesin OCR kedua (RapidOCR) membaca lain, cocokkan angkanya';
const CLAUDE_TANGAN = (seen: string) => `Claude ${seen} di potongan area tanda tangan`;

const id = {
  // NIK, the no-OCR mode: Claude read the whole first page
  'nik.page_invalid': () => 'NIK yang dibaca Claude bukan NIK yang valid',
  'nik.page_missing': () => 'Claude tidak menemukan NIK di halaman pertama',
  'nik.page_mismatch': () => 'Dibaca Claude; tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak',
  'nik.page_confirmed': () => 'Dibaca Claude dari halaman pertama; cocok dengan tanggal lahir dan jenis kelamin di kontrak',
  'nik.page_check': () => 'Dibaca Claude dari halaman pertama; cocokkan angkanya',
  // NIK, read here first
  'nik.conflict': () => 'Ada dua NIK berbeda di dokumen',
  'nik.crop_confirmed': () => 'Dibaca Claude dari potongan baris NIK; cocok dengan tanggal lahir dan jenis kelamin di kontrak',
  'nik.crop_check': () => 'Dibaca Claude dari potongan baris NIK; cocokkan angkanya',
  'nik.claude_differs': () => 'Claude membaca angka lain di baris NIK; cocokkan angkanya',
  'nik.claude_mismatch': () => 'NIK yang dibaca Claude tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak',
  'nik.invalid': () => 'Angka setelah label NIK bukan NIK yang valid',
  'nik.missing': () => 'Tidak ditemukan di halaman identitas',
  'nik.mismatch': () => 'Tidak cocok dengan tanggal lahir atau jenis kelamin di kontrak',
  'nik.scan_second_differs': () => `Dibaca dari scan; ${MESIN_KEDUA_LAIN}`,
  'nik.scan_check': () => 'Dibaca dari scan; cocokkan angkanya',
  'nik.digits_repaired': () => 'Terbaca lebih dari 16 angka di baris NIK; cocokkan angkanya',
  'nik.matches_contract': () => 'Cocok dengan tanggal lahir dan jenis kelamin di kontrak',
  'nik.two_engines': () => `${DUA_MESIN} membaca angka yang sama dari scan`,
  'nik.claude_same': () => 'Claude membaca angka yang sama dari potongan baris NIK',
  'nik.digital': () => 'Dari teks digital PDF, bukan hasil scan',
  // NAMA VENDOR
  'vendor.no_folder': () => 'File tidak berada di dalam folder vendor',
  'vendor.unchecked': () => 'Belum dicocokkan dengan dokumen',
  'vendor.claude_reads': (p: { company: string }) => `Claude membaca "${p.company}" sebagai perusahaan pemberi kerja`,
  'vendor.not_found': () => 'Nama folder vendor tidak ditemukan di dokumen',
  'vendor.claude_same': () => 'Sama dengan perusahaan pemberi kerja yang dibaca Claude',
  'vendor.printed': () => 'Tercetak di dokumen',
  // ENTITAS
  'entity.missing': () => 'Nama file tidak menyebut entitas ("Nama - Masa Kontrak - ENTITAS")',
  'entity.filename': () => 'Dari nama file',
  // NAMA KARYAWAN
  'name.claude_no_format': () => 'Dibaca Claude dari dokumen; nama file tidak berformat "Nama - Masa Kontrak"',
  'name.filename_says': (p: { name: string }) => `Nama file menyebut "${p.name}"`,
  'name.claude_reads': (p: { name: string }) => `Claude membaca "${p.name}" di dokumen`,
  'name.claude_unsure': () => 'Claude kurang yakin membaca nama karyawan',
  'name.claude_same': () => 'Sama dengan nama karyawan yang dibaca Claude dari dokumen',
  'name.unread': () => 'Tidak terbaca dari nama file',
  'name.no_format': () => 'Nama file tidak berformat "Nama - Masa Kontrak"',
  'name.not_in_identity': () => 'Nama dari nama file tidak ada di halaman identitas',
  'name.unchecked': () => 'Belum dicocokkan dengan dokumen',
  'name.on_signature_page': () => 'Nama dari nama file tercetak di kolom karyawan halaman tanda tangan',
  'name.on_identity_page': () => 'Nama dari nama file tercetak di halaman identitas',
  // POSISI
  'position.conflict': () => 'Ada dua posisi berbeda di dokumen',
  'position.missing': () => 'Tidak ditemukan di halaman identitas',
  'position.fixed_by_claude': () => 'Claude memperbaiki salah baca OCR pada jabatan ini; cocokkan di dokumen',
  'position.patterns_differ': () => 'Pembacaan pola teks berbeda; cek di dokumen',
  'position.unclear_same_text': () =>
    'Dibaca dari scan yang kurang jelas; Claude membaca teks OCR yang sama, cocokkan di dokumen',
  'position.two_engines': () => `${DUA_MESIN} membaca posisi yang sama`,
  'position.claude_unsure': () => 'Claude kurang yakin membaca posisi ini',
  'position.unclear_second_differs': () => `Dibaca dari scan yang kurang jelas; ${MESIN_KEDUA_LAIN}`,
  'position.unclear': () => 'Dibaca dari scan yang kurang jelas',
  'position.claude_agrees': () => 'Dibaca Claude dan sama dengan pembacaan pola teks',
  'position.claude': () => 'Dibaca Claude dari halaman identitas',
  'position.printed': () => 'Tertulis di halaman identitas',
  // START PERIODE and END PERIODE
  'period.second_reader_same': () => 'Dibaca ulang mesin OCR kedua (RapidOCR); sama dengan nama file',
  'period.same': () => 'Sama di dokumen dan di nama file',
  'period.document_only': () => 'Hanya dari dokumen; nama file tidak menyebut tanggal',
  'period.filename_says': (p: { date: string }) => `Nama file menyebut ${noteDate(p.date, 'id')}`,
  'period.conflict': () => 'Ada beberapa tanggal berbeda di dokumen',
  'period.from_filename': () => 'Tidak ada di dokumen; diambil dari nama file',
  'period.missing': () => 'Tidak ada di dokumen maupun nama file',
  'period.claude_unsure': () => 'Claude kurang yakin membaca tanggal ini',
  // MONTH
  'months.differ': (p: { stated: number; counted: number }) => `Dokumen menyebut ${p.stated} bulan, tanggalnya ${p.counted} bulan`,
  'months.dates_doubtful': () => 'Tanggal mulai atau selesai perlu dicek',
  'months.matches_dates': () => 'Tertulis di dokumen dan sama dengan tanggalnya',
  'months.stated': () => 'Tertulis di dokumen',
  'months.counted_doubtful': () => 'Dihitung dari tanggal yang perlu dicek',
  'months.counted': () => 'Dihitung dari tanggal mulai dan selesai',
  'months.conflict': () => 'Dokumen menyebut lama kontrak yang berbeda-beda',
  'months.not_whole': () => 'Masa kontrak tidak genap per bulan',
  'months.incomplete': () => 'Masa kontrak tidak lengkap',
  // SIGN; `page` is the signature page, when known
  'signature.page_signed': (p: Page) => `Claude melihat tanda tangan karyawan di${halaman(p.page)}`,
  'signature.page_unsigned': (p: Page) => `Claude melihat tempat tanda tangan karyawan kosong di${halaman(p.page)}`,
  'signature.page_unsure': (p: Page) => `Claude belum yakin; lihat${halaman(p.page)}`,
  'signature.claude_sees_signature': (p: Page) =>
    `Pemeriksaan lokal tidak menemukan coretan, tetapi Claude melihat tanda tangan; lihat${halaman(p.page)}`,
  'signature.claude_sees_none': (p: Page) =>
    `Pemeriksaan lokal melihat coretan, tetapi Claude tidak melihat tanda tangan; lihat${halaman(p.page)}`,
  'signature.page_guessed': (p: Page) => `Halaman tanda tangan tidak dikenali; dibaca dari${halaman(p.page)}, cek halaman itu`,
  'signature.claude_signed': () => `Pemeriksaan lokal ragu; ${CLAUDE_TANGAN('melihat coretan tanda tangan')}`,
  'signature.claude_unsigned': () => `Pemeriksaan lokal ragu; ${CLAUDE_TANGAN('melihat tempat tanda tangan kosong')}`,
  'signature.claude_agrees_signed': () => `Pemeriksaan lokal kurang jelas; ${CLAUDE_TANGAN('juga melihat coretan tanda tangan')}`,
  'signature.claude_agrees_unsigned': () =>
    `Pemeriksaan lokal kurang jelas; ${CLAUDE_TANGAN('juga melihat tempat tanda tangan kosong')}`,
  'signature.unclear': (p: Page) => `Kurang jelas; lihat sekilas${halaman(p.page)}`,
  'signature.signed': () => 'Ada coretan tanda tangan di atas nama karyawan',
  'signature.unsigned': () => 'Tempat tanda tangan karyawan kosong',
  'signature.page_missing': () => 'Halaman tanda tangan tidak ditemukan',
  'signature.uncertain': (p: Page) => `Belum pasti; lihat${halaman(p.page)}`,
  // A note saved before the notes had keys (2026-11-16) that matches no current rule: shown as it was written.
  legacy: (p: { text: string }) => p.text,
};

export type NoteTexts = typeof id;
export type NoteKey = keyof NoteTexts;
/** The parameters a note takes: none, or one object. */
export type NoteArgs<K extends NoteKey> = Parameters<NoteTexts[K]>;

const TWO_ENGINES = 'Both local OCR engines (Tesseract and RapidOCR)';
const SECOND_DIFFERS = 'the second OCR engine (RapidOCR) read it differently';
const IN_CROP = 'in a crop of the signature area';

const en: NoteTexts = {
  'nik.page_invalid': () => 'The NIK Claude read is not a valid NIK',
  'nik.page_missing': () => 'Claude found no NIK on the first page',
  'nik.page_mismatch': () => 'Read by Claude; does not match the birth date or gender in the contract',
  'nik.page_confirmed': () => 'Read by Claude from the first page; matches the birth date and gender in the contract',
  'nik.page_check': () => 'Read by Claude from the first page; check the digits',
  'nik.conflict': () => 'The document has two different NIKs',
  'nik.crop_confirmed': () => 'Read by Claude from a crop of the NIK line; matches the birth date and gender in the contract',
  'nik.crop_check': () => 'Read by Claude from a crop of the NIK line; check the digits',
  'nik.claude_differs': () => 'Claude read different digits on the NIK line; check the digits',
  'nik.claude_mismatch': () => 'The NIK Claude read does not match the birth date or gender in the contract',
  'nik.invalid': () => 'The digits after the NIK label are not a valid NIK',
  'nik.missing': () => 'Not found on the identity page',
  'nik.mismatch': () => 'Does not match the birth date or gender in the contract',
  'nik.scan_second_differs': () => `Read from a scan; ${SECOND_DIFFERS}, check the digits`,
  'nik.scan_check': () => 'Read from a scan; check the digits',
  'nik.digits_repaired': () => 'More than 16 digits were read on the NIK line; check the digits',
  'nik.matches_contract': () => 'Matches the birth date and gender in the contract',
  'nik.two_engines': () => `${TWO_ENGINES} read the same digits from the scan`,
  'nik.claude_same': () => 'Claude read the same digits from a crop of the NIK line',
  'nik.digital': () => "From the PDF's digital text, not a scan",
  'vendor.no_folder': () => 'The file is not inside a vendor folder',
  'vendor.unchecked': () => 'Not yet checked against the document',
  'vendor.claude_reads': (p) => `Claude read "${p.company}" as the employer`,
  'vendor.not_found': () => "The vendor folder's name is not in the document",
  'vendor.claude_same': () => 'Same as the employer Claude read',
  'vendor.printed': () => 'Printed in the document',
  'entity.missing': () => 'The filename does not name the entity ("Name - Contract Period - ENTITY")',
  'entity.filename': () => 'From the filename',
  'name.claude_no_format': () => 'Read by Claude from the document; the filename is not in the "Name - Contract Period" format',
  'name.filename_says': (p) => `The filename says "${p.name}"`,
  'name.claude_reads': (p) => `Claude read "${p.name}" in the document`,
  'name.claude_unsure': () => "Claude is not sure of the employee's name",
  'name.claude_same': () => 'Same as the employee name Claude read in the document',
  'name.unread': () => 'Could not be read from the filename',
  'name.no_format': () => 'The filename is not in the "Name - Contract Period" format',
  'name.not_in_identity': () => 'The name from the filename is not on the identity page',
  'name.unchecked': () => 'Not yet checked against the document',
  'name.on_signature_page': () => "The name from the filename is printed in the employee's column of the signature page",
  'name.on_identity_page': () => 'The name from the filename is printed on the identity page',
  'position.conflict': () => 'The document has two different positions',
  'position.missing': () => 'Not found on the identity page',
  'position.fixed_by_claude': () => 'Claude corrected an OCR misreading of this job title; check it against the document',
  'position.patterns_differ': () => 'The text patterns read it differently; check the document',
  'position.unclear_same_text': () => 'Read from an unclear scan; Claude read the same OCR text, check it against the document',
  'position.two_engines': () => `${TWO_ENGINES} read the same position`,
  'position.claude_unsure': () => 'Claude is not sure of this position',
  'position.unclear_second_differs': () => `Read from an unclear scan; ${SECOND_DIFFERS}, check it against the document`,
  'position.unclear': () => 'Read from an unclear scan',
  'position.claude_agrees': () => "Read by Claude and the same as the text patterns' reading",
  'position.claude': () => 'Read by Claude from the identity page',
  'position.printed': () => 'Written on the identity page',
  'period.second_reader_same': () => 'Read again by the second OCR engine (RapidOCR); matches the filename',
  'period.same': () => 'The same in the document and in the filename',
  'period.document_only': () => 'From the document only; the filename gives no date',
  'period.filename_says': (p) => `The filename says ${noteDate(p.date, 'en')}`,
  'period.conflict': () => 'The document has several different dates',
  'period.from_filename': () => 'Not in the document; taken from the filename',
  'period.missing': () => 'Neither in the document nor in the filename',
  'period.claude_unsure': () => 'Claude is not sure of this date',
  'months.differ': (p) => `The document says ${months(p.stated)}, its dates give ${months(p.counted)}`,
  'months.dates_doubtful': () => 'The start or end date needs a check',
  'months.matches_dates': () => 'Written in the document and matches its dates',
  'months.stated': () => 'Written in the document',
  'months.counted_doubtful': () => 'Counted from dates that need a check',
  'months.counted': () => 'Counted from the start and end dates',
  'months.conflict': () => 'The document gives different contract lengths',
  'months.not_whole': () => 'The contract period is not a whole number of months',
  'months.incomplete': () => 'The contract period is incomplete',
  'signature.page_signed': (p) => `Claude saw the employee's signature${onPage(p.page)}`,
  'signature.page_unsigned': (p) => `Claude saw the employee's signature space empty${onPage(p.page)}`,
  'signature.page_unsure': (p) => `Claude is not sure yet;${lookAt(p.page)}`,
  'signature.claude_sees_signature': (p) => `The local check found no strokes, but Claude sees a signature;${lookAt(p.page)}`,
  'signature.claude_sees_none': (p) => `The local check found strokes, but Claude sees no signature;${lookAt(p.page)}`,
  'signature.page_guessed': (p) =>
    p.page
      ? `The signature page was not recognised; read from page ${p.page}, check that page`
      : 'The signature page was not recognised; check the page it was read from',
  'signature.claude_signed': () => `The local check was unsure; Claude saw signature strokes ${IN_CROP}`,
  'signature.claude_unsigned': () => `The local check was unsure; Claude saw an empty signature space ${IN_CROP}`,
  'signature.claude_agrees_signed': () => `The local check was unclear; Claude also saw signature strokes ${IN_CROP}`,
  'signature.claude_agrees_unsigned': () => `The local check was unclear; Claude also saw an empty signature space ${IN_CROP}`,
  'signature.unclear': (p) => `Unclear; take a quick look${p.page ? ` at page ${p.page}` : ''}`,
  'signature.signed': () => "Signature strokes above the employee's name",
  'signature.unsigned': () => "The employee's signature space is empty",
  'signature.page_missing': () => 'Signature page not found',
  'signature.uncertain': (p) => `Not certain yet;${lookAt(p.page)}`,
  legacy: (p) => p.text,
};

export const NOTE_TEXTS: Record<Lang, NoteTexts> = { id, en };

/** A cell note as the API sends it: a key and, for some keys, their parameters. */
export interface Note {
  key: NoteKey;
  params?: Record<string, string | number | null>;
}

/** A note for `key`, with the parameters it needs. */
export function note<K extends NoteKey>(key: K, ...args: NoteArgs<K>): Note {
  const [params] = args as unknown as [Note['params']?];
  return params ? { key, params } : { key };
}

/** A note in one language. A key this version does not know (a newer backend, a damaged row) shows as the key. */
export function noteText(value: Note, lang: Lang): string {
  const texts = NOTE_TEXTS[lang];
  if (!Object.hasOwn(texts, value.key)) return String(value.key);
  try {
    const text = (texts[value.key] as (params: Note['params']) => unknown)(value.params ?? {});
    return typeof text === 'string' ? text : String(value.key);
  } catch {
    return String(value.key); // parameters missing from a hand-edited row
  }
}
