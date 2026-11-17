import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { PGlite } from '@electric-sql/pglite';
import { SQL } from 'bun';
import { drizzle as bunSqlDrizzle } from 'drizzle-orm/bun-sql';
import { migrate as bunSqlMigrate } from 'drizzle-orm/bun-sql/migrator';
import { drizzle as pgliteDrizzle } from 'drizzle-orm/pglite';
import { migrate as pgliteMigrate } from 'drizzle-orm/pglite/migrator';

/** Where `bun run db:generate` writes the migrations; both kinds of database apply them on open. */
export const MIGRATIONS_FOLDER = resolve(import.meta.dir, '../../../drizzle');

/** A PostgreSQL server (DATABASE_URL), or PostgreSQL embedded in the backend (PGlite) for local testing: kept in a
 *  folder, or in memory when `dir` is null (tests). */
export type DatabaseConfig = { kind: 'postgres'; url: string } | { kind: 'embedded'; dir: string | null };

export type Database = ReturnType<typeof bunSqlDrizzle> | ReturnType<typeof pgliteDrizzle>;

export interface OpenDatabase {
  db: Database;
  close(): Promise<void>;
}

/** Opens the database and brings its tables up to date with the migrations. */
export async function openDatabase(config: DatabaseConfig): Promise<OpenDatabase> {
  if (config.kind === 'postgres') {
    const client = new SQL(config.url);
    const db = bunSqlDrizzle({ client });
    try {
      await bunSqlMigrate(db, { migrationsFolder: MIGRATIONS_FOLDER });
    } catch (error) {
      await client.close();
      throw error;
    }
    return { db, close: () => client.close() };
  }
  if (config.dir) mkdirSync(config.dir, { recursive: true });
  const client = config.dir ? new PGlite(config.dir) : new PGlite();
  const db = pgliteDrizzle({ client });
  try {
    await pgliteMigrate(db, { migrationsFolder: MIGRATIONS_FOLDER });
  } catch (error) {
    await client.close();
    throw error;
  }
  return { db, close: () => client.close() };
}
