import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PGlite } from '@electric-sql/pglite';
import { drizzle } from 'drizzle-orm/pglite';
import { migrate } from 'drizzle-orm/pglite/migrator';
import { SESSION_MS, SessionStore, loginIdentity, normalEmail, sessionCookie, sessionToken } from '../src/modules/auth/sessions.ts';
import { MIGRATIONS_FOLDER, type OpenDatabase, openDatabase } from '../src/modules/database/database.ts';
import { users as usersTable } from '../src/modules/database/schema.ts';
import { type AccessError, UserStore } from '../src/modules/users/store.ts';

// fictitious test users only

describe('User table', () => {
  let database: OpenDatabase;
  let users: UserStore;

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    users = new UserStore(database.db);
  });
  afterAll(() => database.close());

  test('one row per email, added as an inactive reviewer: createdAt stays, the name and last login follow each sign-in', async () => {
    const first = await users.signIn('rina.uji@gdn-commerce.com', 'Rina Uji');
    expect(first).toMatchObject({ email: 'rina.uji@gdn-commerce.com', name: 'Rina Uji', role: 'reviewer', active: false });
    expect(first.id).toBeGreaterThan(0);
    expect(first.createdAt).toBeInstanceOf(Date);
    expect(first.lastLoginAt).toBeInstanceOf(Date);

    await Bun.sleep(5);
    const again = await users.signIn('rina.uji@gdn-commerce.com', 'Rina Uji Coba');
    expect(again).toEqual({ ...first, name: 'Rina Uji Coba', lastLoginAt: again.lastLoginAt });
    expect(again.lastLoginAt!.getTime()).toBeGreaterThan(first.lastLoginAt!.getTime());
    const other = await users.signIn('budi.uji@gdn-commerce.com', 'Budi Uji');
    expect(other.id).not.toBe(first.id);
    expect(await users.count()).toBe(2);
    expect(await users.get(first.id)).toEqual(again);
    expect(await users.get(9999)).toBeNull();
  });

  test('a sign-in keeps the role and active state an admin gave', async () => {
    const admin = await new UserStore(database.db, new Set(['admin.uji@gdn-commerce.com'])).signIn('admin.uji@gdn-commerce.com', 'Admin Uji');
    const user = await users.signIn('tono.uji@gdn-commerce.com', 'Tono Uji');
    await users.setAccess(admin.id, user.id, { role: 'admin', active: true });
    expect(await users.signIn('tono.uji@gdn-commerce.com', 'Tono Uji')).toMatchObject({ role: 'admin', active: true });
    await users.setAccess(admin.id, user.id, { active: false });
    expect(await users.signIn('tono.uji@gdn-commerce.com', 'Tono Uji')).toMatchObject({ role: 'admin', active: false });
  });

  test('the embedded database keeps its rows in its folder across restarts', async () => {
    const dir = join(mkdtempSync(join(tmpdir(), 'ocr-db-')), 'pglite');
    const one = await openDatabase({ kind: 'embedded', dir });
    const row = await new UserStore(one.db).signIn('sari.uji@gdn-commerce.com', 'Sari Uji');
    await one.close();

    const two = await openDatabase({ kind: 'embedded', dir }); // migrations already applied: nothing to redo
    try {
      expect(await new UserStore(two.db).get(row.id)).toEqual(row);
    } finally {
      await two.close();
    }
  });
});

describe('roles and the active switch', () => {
  // fictitious test users; BOSS is in ADMIN_EMAILS
  const BOSS = 'boss.uji@gdn-commerce.com';
  let database: OpenDatabase;
  let users: UserStore;

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    users = new UserStore(database.db, new Set([BOSS]));
  });
  afterAll(() => database.close());

  test('ADMIN_EMAILS makes an active admin on the first sign-in and again on every later one', async () => {
    const boss = await users.signIn(BOSS, 'Boss Uji');
    expect(boss).toMatchObject({ role: 'admin', active: true });
    expect(users.adminFromConfig(BOSS)).toBe(true);
    expect(users.adminFromConfig('lain.uji@gdn-commerce.com')).toBe(false);
    // listed after it was already a user (the owner's row before ADMIN_EMAILS was set): promoted at the next sign-in
    const before = await new UserStore(database.db).signIn('dewi.uji@gdn-commerce.com', 'Dewi Uji');
    expect(before).toMatchObject({ role: 'reviewer', active: false });
    const promoted = await new UserStore(database.db, new Set(['dewi.uji@gdn-commerce.com'])).signIn('dewi.uji@gdn-commerce.com', 'Dewi Uji');
    expect(promoted).toMatchObject({ id: before.id, role: 'admin', active: true });
    expect(await users.countActiveAdmins()).toBe(2);
  });

  test('an active admin changes another user; everyone else, and the admin on their own row, is refused', async () => {
    const boss = await users.signIn(BOSS, 'Boss Uji');
    const ani = await users.signIn('ani.uji@gdn-commerce.com', 'Ani Uji');
    const eko = await users.signIn('eko.uji@gdn-commerce.com', 'Eko Uji');

    expect(await users.setAccess(boss.id, ani.id, { active: true })).toMatchObject({ id: ani.id, role: 'reviewer', active: true });
    expect(await users.setAccess(boss.id, ani.id, {})).toMatchObject({ id: ani.id, active: true }); // nothing to change
    const refusal = (promise: Promise<unknown>) => promise.then(() => 'changed', (error: AccessError) => error.code);
    expect(await refusal(users.setAccess(ani.id, eko.id, { active: true }))).toBe('admin_only'); // a reviewer
    expect(await refusal(users.setAccess(9999, eko.id, { active: true }))).toBe('admin_only'); // nobody
    expect(await refusal(users.setAccess(boss.id, boss.id, { role: 'reviewer' }))).toBe('cannot_change_self');
    expect(await refusal(users.setAccess(boss.id, 9999, { active: true }))).toBe('user_not_found');

    await users.setAccess(boss.id, ani.id, { role: 'admin' });
    expect(await refusal(users.setAccess(ani.id, ani.id, { active: false }))).toBe('cannot_change_self');
    expect(await refusal(users.setAccess(ani.id, boss.id, { role: 'reviewer' }))).toBe('admin_from_config');
    expect(await users.setAccess(ani.id, eko.id, { active: true, role: 'admin' })).toMatchObject({ role: 'admin', active: true });
    // an admin who was deactivated changes nothing more
    await users.setAccess(boss.id, ani.id, { active: false });
    expect(await refusal(users.setAccess(ani.id, eko.id, { active: false }))).toBe('admin_only');
    expect(await users.get(eko.id)).toMatchObject({ role: 'admin', active: true });
  });

  test('two admins demoting each other at the same moment leave one admin', async () => {
    const boss = await users.signIn(BOSS, 'Boss Uji');
    const [one, two] = await Promise.all([
      users.signIn('satu.uji@gdn-commerce.com', 'Satu Uji'),
      users.signIn('dua.uji@gdn-commerce.com', 'Dua Uji'),
    ]);
    for (const user of [one!, two!]) await users.setAccess(boss.id, user.id, { role: 'admin', active: true });
    const results = await Promise.allSettled([
      users.setAccess(one!.id, two!.id, { role: 'reviewer' }),
      users.setAccess(two!.id, one!.id, { role: 'reviewer' }),
    ]);
    expect(results.map((r) => r.status).sort()).toEqual(['fulfilled', 'rejected']);
    const roles = [(await users.get(one!.id))!.role, (await users.get(two!.id))!.role].sort();
    expect(roles).toEqual(['admin', 'reviewer']);
  });

  test('the list puts the latest sign-in first, and users who never signed in since the upgrade last', async () => {
    await users.signIn('ani.uji@gdn-commerce.com', 'Ani Uji');
    const list = await users.list();
    expect(list[0]!.email).toBe('ani.uji@gdn-commerce.com');
    const times = list.map((u) => u.lastLoginAt?.getTime() ?? 0);
    expect(times).toEqual([...times].sort((a, b) => b - a));
  });

  test('a database made before roles keeps its users, each now an inactive reviewer with no last login', async () => {
    // the migrations up to 0003, as an existing database has them
    const older = mkdtempSync(join(tmpdir(), 'ocr-migrations-'));
    const journal = JSON.parse(readFileSync(join(MIGRATIONS_FOLDER, 'meta', '_journal.json'), 'utf8')) as {
      entries: { idx: number; tag: string }[];
    };
    const kept = journal.entries.filter((entry) => entry.idx <= 3);
    mkdirSync(join(older, 'meta'));
    writeFileSync(join(older, 'meta', '_journal.json'), JSON.stringify({ ...journal, entries: kept }));
    for (const entry of kept) copyFileSync(join(MIGRATIONS_FOLDER, `${entry.tag}.sql`), join(older, `${entry.tag}.sql`));

    const client = new PGlite();
    const db = drizzle({ client });
    try {
      await migrate(db, { migrationsFolder: older });
      await client.query(`insert into "User" ("email", "name") values ('lama.uji@gdn-commerce.com', 'Lama Uji')`);
      await migrate(db, { migrationsFolder: MIGRATIONS_FOLDER });
      const [row] = await db.select().from(usersTable);
      expect(row).toMatchObject({ email: 'lama.uji@gdn-commerce.com', role: 'reviewer', active: false, lastLoginAt: null });
      await expect(client.query(`update "User" set "role" = 'owner'`)).rejects.toThrow(); // the role check constraint
    } finally {
      await client.close();
    }
  });
});

describe('mock sign-in rules', () => {
  test('an email is kept trimmed and lowercased, as ADMIN_EMAILS compares it', () => {
    expect(normalEmail(' Rina.Uji@GDN-Commerce.com ')).toBe('rina.uji@gdn-commerce.com');
    expect(normalEmail('rina')).toBeNull();
  });

  test('lowercases the email and tidies the name', () => {
    expect(loginIdentity({ email: ' Rina.Uji@Blibli.com ', name: ' Rina \t Uji ' })).toEqual({
      email: 'rina.uji@blibli.com',
      name: 'Rina Uji',
    });
  });

  test.each(['rina@gmail.com', 'rina@sub.gdn-commerce.com', 'rina.uji+test@example.co.id'])(
    'accepts %p: any domain, Apps Gateway filters accounts itself',
    (email) => {
      expect(loginIdentity({ email, name: 'Rina Uji' })).toEqual({ email, name: 'Rina Uji' });
    },
  );

  test.each(['', 'rina', 'rina@', 'rina uji@gdn-commerce.com', 'rina@gdn-commerce', `${'r'.repeat(250)}@example.com`])(
    'refuses the email %p',
    (email) => {
      expect(loginIdentity({ email, name: 'Rina Uji' })).toBe('bad_email');
    },
  );

  test('refuses names that are too short, too long or hold control characters', () => {
    const name = (n: string) => loginIdentity({ email: 'rina@blibli.com', name: n });
    expect(name('R')).toBe('bad_name');
    expect(name('R'.repeat(256))).toBe('bad_name');
    expect(name('Rina\u0007Uji')).toBe('bad_name');
    expect(name('R'.repeat(255))).toEqual({ email: 'rina@blibli.com', name: 'R'.repeat(255) });
  });
});

describe('sessions', () => {
  test('a token maps to its user until it ends or expires', () => {
    let now = 1_000;
    const sessions = new SessionStore(60_000, () => now);
    const token = sessions.create(7);
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(sessions.userId(token)).toBe(7);
    expect(sessions.userId(null)).toBeNull();
    expect(sessions.userId('x'.repeat(43))).toBeNull();

    now += 59_999;
    expect(sessions.userId(token)).toBe(7);
    now += 1;
    expect(sessions.userId(token)).toBeNull();

    const other = sessions.create(8);
    sessions.end(other);
    expect(sessions.userId(other)).toBeNull();
  });

  test('tokens are random', () => {
    const sessions = new SessionStore();
    const tokens = new Set(Array.from({ length: 50 }, () => sessions.create(1)));
    expect(tokens.size).toBe(50);
  });

  test('reads the session cookie and ignores anything that is not a token', () => {
    const token = new SessionStore().create(1);
    expect(sessionToken(`theme=dark; contract_ocr_session=${token}; x=1`)).toBe(token);
    expect(sessionToken(`contract_ocr_session=${token}`)).toBe(token);
    expect(sessionToken('contract_ocr_session=short')).toBeNull();
    expect(sessionToken(`other_session=${token}`)).toBeNull();
    expect(sessionToken(null)).toBeNull();
  });

  test('the cookie is HttpOnly and SameSite=Strict, Secure over HTTPS, and cleared with Max-Age=0', () => {
    expect(sessionCookie('abc', false)).toBe(
      `contract_ocr_session=abc; Path=/; HttpOnly; SameSite=Strict; Max-Age=${SESSION_MS / 1000}`,
    );
    expect(sessionCookie('abc', true)).toEndWith('; Secure');
    expect(sessionCookie(null, false)).toBe('contract_ocr_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0');
  });
});
