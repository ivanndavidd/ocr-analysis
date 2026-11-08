// Stands in for `contract-extractor xlsx` without Python: reads the spec on stdin and writes back the sheet names.
// A first argument of `fail` exits with an error; `slow` never answers.

export {};

const mode = process.argv[2] ?? '';
const spec = JSON.parse(await Bun.stdin.text()) as { sheets: { name: string }[] };
if (mode === 'fail') {
  process.stderr.write('spesifikasi tidak valid: sheets.0.name: too long\n');
  process.exit(2);
}
if (mode === 'slow') await Bun.sleep(60_000);
process.stdout.write(`xlsx:${spec.sheets.map((s) => s.name).join('|')}`);
