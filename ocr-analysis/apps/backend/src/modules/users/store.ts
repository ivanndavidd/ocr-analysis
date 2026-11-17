import type { UserAccessChange, UserAccessErrorCode } from '@contract-ocr/shared-types';
import { and, asc, desc, eq, inArray, sql } from 'drizzle-orm';
import type { Database } from '../database/database.ts';
import { type UserRow, users } from '../database/schema.ts';

/** Why a change on the User page is refused. */
export class AccessError extends Error {
  constructor(readonly code: UserAccessErrorCode) {
    super(code);
    this.name = 'AccessError';
  }
}

/**
 * The `User` table. Signing in adds a user as an inactive reviewer; only an active admin can change another user's
 * role or active state. `adminEmails` (ADMIN_EMAILS) are the bootstrap: those emails become active admins on every
 * sign-in, so the first admin exists and an admin locked out by mistake gets back in by signing in again.
 */
export class UserStore {
  constructor(
    private readonly db: Database,
    private readonly adminEmails: ReadonlySet<string> = new Set(),
  ) {}

  /** Records a sign-in: adds the user the first time the email signs in (an inactive reviewer, or an active admin if
   *  the email is in ADMIN_EMAILS). A later sign-in keeps the row, its createdAt, role and active state, and takes the
   *  name given this time. Every sign-in sets lastLoginAt. */
  async signIn(email: string, name: string): Promise<UserRow> {
    const admin = this.adminEmails.has(email) ? ({ role: 'admin', active: true } as const) : {};
    const [row] = await this.db
      .insert(users)
      .values({ email, name, lastLoginAt: sql`now()`, ...admin })
      .onConflictDoUpdate({ target: users.email, set: { name, lastLoginAt: sql`now()`, ...admin } })
      .returning();
    if (!row) throw new Error('sign-in wrote no row');
    return row;
  }

  async get(id: number): Promise<UserRow | null> {
    const [row] = await this.db.select().from(users).where(eq(users.id, id)).limit(1);
    return row ?? null;
  }

  /** Every user, latest sign-in first (a user who just signed in and waits to be activated is on top). */
  async list(): Promise<UserRow[]> {
    return this.db.select().from(users).orderBy(sql`${users.lastLoginAt} desc nulls last`, desc(users.id));
  }

  /** The email is in ADMIN_EMAILS: an active admin on every sign-in, so the User page leaves it alone. */
  adminFromConfig(email: string): boolean {
    return this.adminEmails.has(email);
  }

  async countActiveAdmins(): Promise<number> {
    return (await this.db.$count(users, and(eq(users.role, 'admin'), eq(users.active, true)))) as number;
  }

  /**
   * An admin changes another user's role or active state. Refused when the actor is not (or no longer) an active
   * admin, for the actor's own row (nobody locks themselves out), for an ADMIN_EMAILS user (the next sign-in would undo
   * it), and for an unknown user. Both rows are locked first, in id order, so two admins demoting each other at the
   * same moment cannot both succeed and leave no admin.
   */
  async setAccess(actorId: number, targetId: number, change: UserAccessChange): Promise<UserRow> {
    const update = {
      ...(change.role === undefined ? {} : { role: change.role }),
      ...(change.active === undefined ? {} : { active: change.active }),
    };
    return this.db.transaction(async (tx) => {
      const rows = await tx
        .select()
        .from(users)
        .where(inArray(users.id, [actorId, targetId]))
        .orderBy(asc(users.id))
        .for('update');
      const actor = rows.find((row) => row.id === actorId);
      if (!actor?.active || actor.role !== 'admin') throw new AccessError('admin_only');
      if (actorId === targetId) throw new AccessError('cannot_change_self');
      const target = rows.find((row) => row.id === targetId);
      if (!target) throw new AccessError('user_not_found');
      if (this.adminEmails.has(target.email)) throw new AccessError('admin_from_config');
      if (Object.keys(update).length === 0) return target;
      const [row] = await tx.update(users).set(update).where(eq(users.id, targetId)).returning();
      if (!row) throw new AccessError('user_not_found');
      return row;
    });
  }

  async count(): Promise<number> {
    return (await this.db.$count(users)) as number;
  }
}
