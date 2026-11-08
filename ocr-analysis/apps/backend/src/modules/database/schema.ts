import {
  type AnyPgColumn,
  boolean,
  date,
  doublePrecision,
  index,
  integer,
  pgTable,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from 'drizzle-orm/pg-core';

/**
 * The website's tables, as drawn in the ERD (`Database Design/Contract OCR ERD.vpp`, built by `erd.py`): User 1 → many
 * OCRHeader (one per "Jalankan OCR") 1 → many OCRContractDetail (one per PDF in that run). After changing this file,
 * generate a migration (see drizzle.config.ts) and keep the new file it writes under `drizzle/`.
 *
 * `varchar` without a length is unbounded in PostgreSQL (like `text`); it holds the JSON lists, which grow with a run.
 */

/** One row per email, added the first time that email signs in. */
export const users = pgTable('User', {
  id: integer('id').primaryKey().generatedAlwaysAsIdentity(),
  email: varchar('email', { length: 255 }).notNull().unique(),
  name: varchar('name', { length: 255 }).notNull(),
  createdAt: timestamp('createdAt', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
});

export type UserRow = typeof users.$inferSelect;

/** A run of "Jalankan OCR": who started it, what it covered, how it read, and the Claude tokens its own reads used. */
export const ocrHeaders = pgTable('OCRHeader', {
  id: integer('id').primaryKey().generatedAlwaysAsIdentity(),
  jobId: uuid('jobId').notNull().unique().defaultRandom(), // the run's id in the website's links and API
  // queued | running | done | cancelled | interrupted (the backend stopped while the run was queued or running)
  status: varchar('status', { length: 20 }).notNull(),
  mode: varchar('mode', { length: 20 }).notNull().default('ocr'), // ocr | full_page (no OCR, whole pages to Claude)
  claude: boolean('claude').notNull().default(false), // Claude was helping when the run started
  folders: varchar('folders').notNull(), // JSON array of the root-relative folders chosen
  totalFiles: integer('totalFiles').notNull(),
  inputTokens: integer('inputTokens').notNull().default(0),
  cacheWriteTokens: integer('cacheWriteTokens').notNull().default(0),
  cacheReadTokens: integer('cacheReadTokens').notNull().default(0),
  outputTokens: integer('outputTokens').notNull().default(0),
  startedAt: timestamp('startedAt', { withTimezone: true, mode: 'date' }).notNull(),
  finishedAt: timestamp('finishedAt', { withTimezone: true, mode: 'date' }), // null while running
  userId: integer('userId')
    .notNull()
    .references(() => users.id),
});

/**
 * One PDF of a run and what was read from it: the Excel row's values, plus `checks` (one note per column HR must
 * compare with the PDF, JSON object) and `reasons` (the extractor's codes for the whole file, JSON array; a failed
 * file's cause is its first code). `status`: ready (nothing to check), needs_check, failed, cancelled.
 * `result` keeps the whole reading so a past run opens as it looked; `reusedFromHeaderId` names the run that read it.
 */
export const ocrContractDetails = pgTable(
  'OCRContractDetail',
  {
    id: integer('id').primaryKey().generatedAlwaysAsIdentity(),
    documentKey: varchar('documentKey', { length: 64 }).notNull(), // SHA-256 (hex) of filePath
    filePath: varchar('filePath', { length: 1000 }).notNull(), // root-relative: vendor/period/file name
    status: varchar('status', { length: 20 }).notNull(),
    seconds: doublePrecision('seconds'), // float8: a real (float4) turns 15.7 into 15.699999809
    vendor: varchar('vendor', { length: 255 }),
    entity: varchar('entity', { length: 255 }),
    employeeName: varchar('employeeName', { length: 255 }),
    nik: varchar('nik', { length: 16 }),
    position: varchar('position', { length: 100 }),
    startDate: date('startDate', { mode: 'string' }),
    endDate: date('endDate', { mode: 'string' }),
    months: integer('months'),
    signature: varchar('signature', { length: 10 }), // YES | NO
    checks: varchar('checks'),
    reasons: varchar('reasons'),
    result: varchar('result'), // JSON: the whole reading the results page shows (ContractRow); null when not read
    headerId: integer('headerId')
      .notNull()
      .references(() => ocrHeaders.id, { onDelete: 'cascade' }),
    // set when another run that was queued or running at the same time read this PDF and this run took its result
    reusedFromHeaderId: integer('reusedFromHeaderId').references((): AnyPgColumn => ocrHeaders.id, { onDelete: 'set null' }),
  },
  (t) => [
    // the same PDF read again in a later run is a new row; within a run, a retry updates its row
    uniqueIndex('OCRContractDetail_headerId_documentKey').on(t.headerId, t.documentKey),
    index('OCRContractDetail_documentKey').on(t.documentKey),
  ],
);

export type OcrDetailInsert = typeof ocrContractDetails.$inferInsert;
export type OcrHeaderRow = typeof ocrHeaders.$inferSelect;
export type OcrDetailRow = typeof ocrContractDetails.$inferSelect;
