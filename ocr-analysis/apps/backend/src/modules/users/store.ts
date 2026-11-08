import { eq } from 'drizzle-orm';
import type { Database } from '../database/database.ts';
import { type UserRow, users } from '../database/schema.ts';

/** The `User` table. Rows are added by signing in; nothing else writes them. */
export class UserStore {
  constructor(private readonly db: Database) {}

  /** Adds the user the first time the email signs in. A later sign-in keeps the row and its createdAt, and takes the
   *  name given this time. */
  async signIn(email: string, name: string): Promise<UserRow> {
    const [row] = await this.db
      .insert(users)
      .values({ email, name })
      .onConflictDoUpdate({ target: users.email, set: { name } })
      .returning();
    if (!row) throw new Error('sign-in wrote no row');
    return row;
  }

  async get(id: number): Promise<UserRow | null> {
    const [row] = await this.db.select().from(users).where(eq(users.id, id)).limit(1);
    return row ?? null;
  }

  async count(): Promise<number> {
    return (await this.db.$count(users)) as number;
  }
}
