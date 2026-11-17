import type { ManagedUser, Role } from '@contract-ocr/shared-types';

// The User page: wording, its search and filters, and which rows an admin may not change.

/** `Penguji Dua` → `PD`: the round avatar of an account (the quick sign-in list, the User page). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join('');
}

export const ROLE_LABEL: Record<Role, string> = { admin: 'Admin', reviewer: 'Reviewer' };

/** The choices of a row's dropdowns on the User page. */
export const ROLE_CHOICES: readonly Role[] = ['reviewer', 'admin'];
export const STATUS_CHOICES = [
  { value: 'active', label: 'Aktif' },
  { value: 'inactive', label: 'Nonaktif' },
] as const;

export const STATUS_FILTERS = [
  { value: 'all', label: 'Semua status' },
  { value: 'active', label: 'Aktif' },
  { value: 'inactive', label: 'Nonaktif' },
] as const;
export const ROLE_FILTERS = [
  { value: 'all', label: 'Semua peran' },
  { value: 'admin', label: 'Admin' },
  { value: 'reviewer', label: 'Reviewer' },
] as const;

export interface UserFilter {
  q: string; // part of the name or email, any case
  status: (typeof STATUS_FILTERS)[number]['value'];
  role: (typeof ROLE_FILTERS)[number]['value'];
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
  if (user.id === signedInId) return 'Akun Anda sendiri: peran dan status tidak bisa diubah sendiri';
  if (user.adminFromConfig) return 'Admin tetap dari ADMIN_EMAILS di backend: ubah di pengaturan backend';
  return null;
}

/** What a change does, for its confirmation. Activating needs none: it is the page's everyday step, undone in a click. */
export function changeQuestion(user: ManagedUser, change: { role?: Role; active?: boolean }): { title: string; body: string; confirm: string } | null {
  if (change.role === 'admin') {
    return {
      title: 'Jadikan admin?',
      body: `${user.name} akan bisa membuka halaman User, mengaktifkan dan menonaktifkan user lain, serta mengubah peran mereka.`,
      confirm: 'Ya, jadikan admin',
    };
  }
  if (change.role === 'reviewer') {
    return {
      title: 'Jadikan reviewer?',
      body: `${user.name} tetap bisa memakai website, tetapi tidak bisa lagi membuka halaman User.`,
      confirm: 'Ya, jadikan reviewer',
    };
  }
  if (change.active === false) {
    return {
      title: 'Nonaktifkan user?',
      body: `${user.name} langsung tidak bisa memakai website, termasuk sesi yang sedang terbuka, sampai diaktifkan lagi. Proses OCR miliknya yang sedang berjalan tetap diselesaikan.`,
      confirm: 'Ya, nonaktifkan',
    };
  }
  return null;
}
