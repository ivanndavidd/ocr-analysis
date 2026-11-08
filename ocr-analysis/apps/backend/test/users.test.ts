import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SESSION_MS, SessionStore, loginIdentity, sessionCookie, sessionToken } from '../src/modules/auth/sessions.ts';
import { type OpenDatabase, openDatabase } from '../src/modules/database/database.ts';
import { UserStore } from '../src/modules/users/store.ts';

// fictitious test users only

describe('User table', () => {
  let database: OpenDatabase;
  let users: UserStore;

  beforeAll(async () => {
    database = await openDatabase({ kind: 'embedded', dir: null });
    users = new UserStore(database.db);
  });
  afterAll(() => database.close());

  test('one row per email: createdAt stays, the name follows the latest sign-in', async () => {
    const first = await users.signIn('rina.uji@gdn-commerce.com', 'Rina Uji');
    expect(first).toMatchObject({ email: 'rina.uji@gdn-commerce.com', name: 'Rina Uji' });
    expect(first.id).toBeGreaterThan(0);
    expect(first.createdAt).toBeInstanceOf(Date);

    const again = await users.signIn('rina.uji@gdn-commerce.com', 'Rina Uji Coba');
    expect(again).toEqual({ ...first, name: 'Rina Uji Coba' });
    const other = await users.signIn('budi.uji@gdn-commerce.com', 'Budi Uji');
    expect(other.id).not.toBe(first.id);
    expect(await users.count()).toBe(2);
    expect(await users.get(first.id)).toEqual(again);
    expect(await users.get(9999)).toBeNull();
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

describe('mock sign-in rules', () => {
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
