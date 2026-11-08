// Turn a run's worker replies into the rows the website shows, with the website's own code (apps/backend rows.ts),
// so a cell is yellow ("Perlu dicek") in the benchmark exactly when it would be on the website.
//
//   bun bench/to_rows.ts runs/<engine>/files.jsonl
//
// Writes rows.jsonl next to it: per contract its fields (value, check, basis), the decision, and the full NIK.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fullNik, toRow } from '../../ocr-analysis/apps/backend/src/modules/jobs/rows.ts';

const input = process.argv[2];
if (!input) throw new Error('usage: bun bench/to_rows.ts runs/<engine>/files.jsonl');
const out: string[] = [];
for (const line of readFileSync(input, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const record = JSON.parse(line);
  if (!record.reply) {
    out.push(JSON.stringify({ name: record.name, error: record.error ?? 'no_reply' }));
    continue;
  }
  const { result, filename } = record.reply;
  const row = toRow(result, filename, record.vendor);
  out.push(
    JSON.stringify({
      name: record.name,
      error: null,
      decision: row.decision,
      reasons: row.reasons,
      fields: row.fields,
      nik: fullNik(result),
      nikInfo: row.nik,
    }),
  );
}
writeFileSync(join(dirname(input), 'rows.jsonl'), out.join('\n') + '\n', 'utf8');
console.log(`${out.length} rows -> ${join(dirname(input), 'rows.jsonl')}`);
