import { createHash, randomBytes } from 'node:crypto';

/**
 * Mock sign-in, a stand-in for Apps Gateway (the company SSO) until the app is registered there. It checks no
 * password and asks for none: anyone who can reach the backend signs in as any email. Any domain is accepted, as
 * behind the gateway, which lets only company accounts through itself. It exists so the website has a signed-in user
 * and the `User` table fills up the way it will behind the gateway; it is not access control and must be replaced
 * before anyone else uses the website. The roles and the active switch (see modules/users/store.ts) only become access
 * control once the identity behind them is the gateway's.
 */

export const SESSION_COOKIE = 'contract_ocr_session';
export const SESSION_MS = 8 * 60 * 60 * 1000; // a working day
const MAX_SESSIONS = 1000;
const TOKEN = /^[A-Za-z0-9_-]{43}$/; // 32 random bytes, base64url

export type LoginErrorCode = 'bad_email' | 'bad_name';

const EMAIL = /^[a-z0-9._%+-]+@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

/** The email trimmed and lowercased, as the `User` table and ADMIN_EMAILS keep it, or null if it is not an email. */
export function normalEmail(raw: string): string | null {
  const email = raw.trim().toLowerCase();
  return email.length <= 255 && EMAIL.test(email) ? email : null;
}

/** The email (lowercased) and name to sign in with, or why they are refused. Whoever signs in is recorded by
 *  `UserStore.signIn`; when Apps Gateway replaces this form, its verified identity goes through the same call. */
export function loginIdentity(raw: { email: string; name: string }): { email: string; name: string } | LoginErrorCode {
  const email = normalEmail(raw.email);
  if (email === null) return 'bad_email';
  const name = raw.name.trim().replace(/\s+/g, ' ');
  if (name.length < 2 || name.length > 255 || CONTROL.test(name)) return 'bad_name';
  return { email, name };
}

/** Signed-in sessions, in memory: a backend restart signs everyone out. Kept by the SHA-256 of the token, so the map
 *  never holds a token that would work in a cookie. */
export class SessionStore {
  readonly #sessions = new Map<string, { userId: number; expires: number }>();

  constructor(
    private readonly ttlMs = SESSION_MS,
    private readonly now: () => number = Date.now,
  ) {}

  create(userId: number): string {
    this.#prune();
    if (this.#sessions.size >= MAX_SESSIONS) {
      const oldest = this.#sessions.keys().next().value; // Map keeps insertion order
      if (oldest !== undefined) this.#sessions.delete(oldest);
    }
    const token = randomBytes(32).toString('base64url');
    this.#sessions.set(digest(token), { userId, expires: this.now() + this.ttlMs });
    return token;
  }

  /** The signed-in user's id, or null for no token, an unknown one, or an expired one. */
  userId(token: string | null): number | null {
    if (token === null) return null;
    const key = digest(token);
    const session = this.#sessions.get(key);
    if (!session) return null;
    if (session.expires <= this.now()) {
      this.#sessions.delete(key);
      return null;
    }
    return session.userId;
  }

  end(token: string | null): void {
    if (token !== null) this.#sessions.delete(digest(token));
  }

  #prune(): void {
    const now = this.now();
    for (const [key, session] of this.#sessions) if (session.expires <= now) this.#sessions.delete(key);
  }
}

const digest = (token: string) => createHash('sha256').update(token).digest('base64url');

/** The session token in a Cookie header, if it has the shape of one. */
export function sessionToken(cookieHeader: string | null): string | null {
  for (const part of (cookieHeader ?? '').split(';')) {
    const at = part.indexOf('=');
    if (at < 0 || part.slice(0, at).trim() !== SESSION_COOKIE) continue;
    const value = part.slice(at + 1).trim();
    return TOKEN.test(value) ? value : null;
  }
  return null;
}

/** The Set-Cookie value that stores a session; `null` clears it. HttpOnly keeps it from page scripts, SameSite=Strict
 *  from other sites' requests, and Secure is added when the site is served over HTTPS. */
export function sessionCookie(token: string | null, secure: boolean, ttlMs = SESSION_MS): string {
  const maxAge = token === null ? 0 : Math.floor(ttlMs / 1000);
  return `${SESSION_COOKIE}=${token ?? ''}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure ? '; Secure' : ''}`;
}
