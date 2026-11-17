// `bun run db:generate` writes the SQL for a schema change into drizzle/; the backend applies it on start.
// drizzle-kit is not kept installed: its esbuild 0.18 carries GHSA-67mh-4wv8-2f99. For a schema change, add it for the
// moment (`bun add -d --exact drizzle-kit@0.31.11`), run `bun run db:generate`, then `bun remove drizzle-kit`.
export default {
  dialect: 'postgresql',
  schema: './src/modules/database/schema.ts',
  out: './drizzle',
};
