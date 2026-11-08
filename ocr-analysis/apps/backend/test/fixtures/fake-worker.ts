// Speaks the `serve-stdio` protocol without Python or Tesseract. What it does depends on the file name:
// "slow" hangs, "crash" exits, "garbage" replies with non-JSON, "reject" answers ok:false; anything else succeeds.
// FAKE_MODE=not_ready reports that OCR is unavailable; FAKE_MODE=die exits before saying anything.
// FAKE_CLAUDE=1 says Claude reads with it, as `serve-stdio --claude` does once Claude is reachable.
// Files named "Budi…" carry a NIK (fictitious: regency and district 99 exist nowhere); the rest have none.

export {}; // a module, so top-level await is allowed

const mode = process.env.FAKE_MODE ?? '';
const FAKE_NIK = '3199990101900001';
const out = (message: unknown) => process.stdout.write(`${JSON.stringify(message)}\n`);

if (mode === 'die') process.exit(1);
if (mode === 'not_ready') {
  out({ ready: false, error: 'ocr_unavailable' });
  process.exit(2);
}

const reader = Bun.stdin.stream().getReader();
let buffer = new Uint8Array(0);

async function fill(): Promise<boolean> {
  const { value, done } = await reader.read();
  if (done || !value) return false;
  const merged = new Uint8Array(buffer.length + value.length);
  merged.set(buffer);
  merged.set(value, buffer.length);
  buffer = merged;
  return true;
}

async function line(): Promise<string | null> {
  for (;;) {
    const at = buffer.indexOf(10);
    if (at >= 0) {
      const text = new TextDecoder().decode(buffer.slice(0, at));
      buffer = buffer.slice(at + 1);
      return text;
    }
    if (!(await fill())) return null;
  }
}

async function bytes(size: number): Promise<Uint8Array | null> {
  while (buffer.length < size) if (!(await fill())) return null;
  const body = buffer.slice(0, size);
  buffer = buffer.slice(size);
  return body;
}

out({ ready: true, claude: process.env.FAKE_CLAUDE === '1' });
for (;;) {
  const header = await line();
  if (header === null) process.exit(0);
  const { id, filename, size, vendor, mode } = JSON.parse(header) as {
    id: number;
    filename: string;
    size: number;
    vendor?: string;
    mode?: string;
  };
  if ((await bytes(size)) === null) process.exit(0);

  if (filename.includes('slow')) await Bun.sleep(60_000);
  if (filename.includes('crash')) process.exit(1);
  if (filename.includes('garbage')) {
    process.stdout.write('this is not json\n');
    continue;
  }
  if (filename.includes('reject')) {
    out({ id, ok: false, error: 'too_large' });
    continue;
  }
  const hasNik = filename.startsWith('Budi');
  out({
    id,
    ok: true,
    result: {
      file: filename,
      nik: hasNik ? FAKE_NIK : null,
      status_ttd: 'signed',
      keputusan: 'review_priority',
      flags: ['auto_disabled'],
      catatan_vendor: String(process.pid), // lets tests see which process answered
      log: {
        p_id: [1],
        p_ttd: 2,
        jenis_halaman: { '1': 'native', '2': 'native' },
        ocr_conf: {},
        periode_regex: { tanggal_mulai: '2026-01-01', tanggal_selesai: '2026-03-31' },
        tanda_tangan: { sumber: 'image' },
        nik: hasNik
          ? { status: 'ditemukan', tanggal_lahir: 'cocok', jenis_kelamin: 'tidak_ada' }
          : { status: 'tidak_ditemukan', tanggal_lahir: 'tidak_ada', jenis_kelamin: 'tidak_ada' },
        // the header's read mode comes back as a reason; the no-OCR mode adds its own, as the real worker does
        alasan: [
          ...(mode === 'full_page' ? ['read_full_pages'] : ['extractor_disabled']),
          ...(hasNik ? [] : ['nik_not_found']),
          `mode:${mode}`,
        ],
        validasi_vendor: vendor ? 'cocok' : null, // tells tests the header carried the vendor folder
      },
    },
    filename: { name: 'Budi Santoso', start: '2026-01-01', end: '2026-03-31', duration_months: null, flags: [] },
  });
}
