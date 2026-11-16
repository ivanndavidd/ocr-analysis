import type { ManagedUser, Role } from '@contract-ocr/shared-types';
import { t } from './i18n.ts';

// The User page: wording, its search and filters, and which rows an admin may not change.

/** `Penguji Dua` → `PD`: the round avatar of an account (the quick sign-in list, the User page). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join('');
}

export const roleLabel = (role: Role) => t().users.roles[role];

/** The choices of a row's dropdowns on the User page. */
export const ROLE_CHOICES: readonly Role[] = ['reviewer', 'admin'];
export const STATUS_CHOICES = ['active', 'inactive'] as const;

/** The filters' choices; `statusLabel` and `roleFilterLabel` word them. */
export const STATUS_FILTERS = ['all', 'active', 'inactive'] as const;
export const ROLE_FILTERS = ['all', 'admin', 'reviewer'] as const;
export const statusLabel = (status: (typeof STATUS_FILTERS)[number]) => t().users.statuses[status];
export const roleFilterLabel = (role: (typeof ROLE_FILTERS)[number]) => t().users.roles[role];

export interface UserFilter {
  q: string; // part of the name or email, any case
  status: (typeof STATUS_FILTERS)[number];
  role: (typeof ROLE_FILTERS)[number];
}

export const NO_FILTER: UserFilter = { q: '', status: 'all', role: 'all' };

export function filterUsers(users: readonly ManagedUser[], filter: UserFilter): ManagedUser[] {
  const q = filter.q.trim().toLowerCase();
  return users.filter(
    (user) =>
      (!q || user.name.toLowerCase().includes(q) || user.email.includes(q)) &&
      (filter.status === 'all' || user.active === (filter.status === 'active')) &&
      (filter.role === 'all' || user.role === filter.role),
  );
}

export function userCounts(users: readonly ManagedUser[]): { total: number; active: number; inactive: number; admins: number } {
  const active = users.filter((user) => user.active).length;
  return { total: users.length, active, inactive: users.length - active, admins: users.filter((u) => u.role === 'admin').length };
}

/** Why the signed-in admin may not change this row, or null: their own row (nobody locks themselves out), or an
 *  ADMIN_EMAILS admin (each sign-in would make it an active admin again). The backend refuses both as well. */
export function lockReason(user: ManagedUser, signedInId: number): string | null {
  if (user.id === signedInId) return t().users.lockedSelf;
  if (user.adminFromConfig) return t().users.lockedConfig;
  return null;
}

/** What a change does, for its confirmation. Activating needs none: it is the page's everyday step, undone in a click. */
export function changeQuestion(user: ManagedUser, change: { role?: Role; active?: boolean }): { title: string; body: string; confirm: string } | null {
  const m = t().users;
  const question = change.role === 'admin' ? m.makeAdmin : change.role === 'reviewer' ? m.makeReviewer : change.active === false ? m.deactivate : null;
  return question && { title: question.title, body: question.body(user.name), confirm: question.confirm };
}
