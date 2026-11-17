<script lang="ts">
  import type { ManagedUser, Role, UserAccessChange } from '@contract-ocr/shared-types';
  import { onMount } from 'svelte';
  import Badge from '../components/Badge.svelte';
  import Banner from '../components/Banner.svelte';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import { api, failureCode } from '../services/api.ts';
  import { formatDateTime, formatDay } from '../utils/format.ts';
  import { errorLabel } from '../utils/labels.ts';
  import {
    NO_FILTER,
    ROLE_CHOICES,
    ROLE_FILTERS,
    ROLE_LABEL,
    STATUS_CHOICES,
    STATUS_FILTERS,
    type UserFilter,
    changeQuestion,
    filterUsers,
    initials,
    lockReason,
    userCounts,
  } from '../utils/users.ts';

  // The User page (admins only): everyone who has signed in, with their role and whether they may use the website.
  // The backend enforces every rule shown here; the page only keeps an admin from asking for what it would refuse.

  let { signedInId }: { signedInId: number } = $props();

  let list = $state<ManagedUser[] | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let filter = $state<UserFilter>({ ...NO_FILTER });
  let busyId = $state<number | null>(null);
  let actionError = $state<string | null>(null);
  // what the last change did: a toast that leaves the rows where they are, read out to screen readers
  let done = $state<string | null>(null);
  let doneTimer: ReturnType<typeof setTimeout> | undefined;
  const DONE_MS = 4_000;
  let pending = $state<{ user: ManagedUser; change: UserAccessChange; question: NonNullable<ReturnType<typeof changeQuestion>> } | null>(null);

  const shown = $derived(list ? filterUsers(list, filter) : []);
  const counts = $derived(userCounts(list ?? []));
  const filtered = $derived(filter.q.trim() !== '' || filter.status !== 'all' || filter.role !== 'all');

  async function load() {
    loading = true;
    loadError = null;
    try {
      list = (await api.users()).users;
    } catch (error) {
      loadError = failureCode(error);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
    return () => clearTimeout(doneTimer);
  });

  /** A row's dropdown changed. It goes back to the saved value at once and shows the new one only once saved, so a
   *  cancelled confirmation or a refused change leaves it as it was. */
  function choose(select: HTMLSelectElement, saved: string, user: ManagedUser, change: UserAccessChange) {
    select.value = saved;
    ask(user, change);
  }

  function ask(user: ManagedUser, change: UserAccessChange) {
    const question = changeQuestion(user, change);
    if (question) pending = { user, change, question };
    else void apply(user, change);
  }

  async function apply(user: ManagedUser, change: UserAccessChange) {
    busyId = user.id;
    actionError = null;
    done = null;
    try {
      const updated = await api.setUserAccess(user.id, change);
      list = (list ?? []).map((row) => (row.id === updated.id ? updated : row));
      done =
        change.role !== undefined
          ? `${updated.name} sekarang ${ROLE_LABEL[updated.role]}.`
          : `${updated.name} sekarang ${updated.active ? 'aktif' : 'nonaktif'}.`;
      clearTimeout(doneTimer);
      doneTimer = setTimeout(() => (done = null), DONE_MS);
    } catch (error) {
      actionError = failureCode(error);
      if (actionError === 'user_not_found') void load();
    } finally {
      busyId = null;
      pending = null;
    }
  }
</script>

<section class="card users" aria-labelledby="users-title">
  <div class="head">
    <div class="title">
      <h1 id="users-title">User</h1>
      <p class="muted">
        Semua akun yang pernah masuk, login terakhir dulu. Akun baru masuk sebagai Reviewer nonaktif dan tidak bisa
        memakai website sampai admin mengaktifkannya.
      </p>
    </div>
    <button class="btn btn-outline btn-sm" type="button" onclick={load} disabled={loading}>
      {loading ? 'Memuat…' : 'Muat ulang'}
    </button>
  </div>

  {#if list}
    <ul class="summary" aria-label="Ringkasan user">
      <li><strong>{counts.total}</strong> user</li>
      <li><strong>{counts.active}</strong> aktif</li>
      <li class:waiting={counts.inactive > 0}><strong>{counts.inactive}</strong> nonaktif</li>
      <li><strong>{counts.admins}</strong> admin</li>
    </ul>
  {/if}

  <div class="filters" role="search" aria-label="Cari dan saring user">
    <label class="search">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2" />
        <path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        bind:value={filter.q}
        maxlength="100"
        placeholder="Cari nama atau email"
        aria-label="Cari nama atau email"
        autocomplete="off"
        spellcheck="false"
      />
    </label>
    <select aria-label="Status" class:set={filter.status !== 'all'} bind:value={filter.status}>
      {#each STATUS_FILTERS as option (option.value)}<option value={option.value}>{option.label}</option>{/each}
    </select>
    <select aria-label="Peran" class:set={filter.role !== 'all'} bind:value={filter.role}>
      {#each ROLE_FILTERS as option (option.value)}<option value={option.value}>{option.label}</option>{/each}
    </select>
    {#if filtered}
      <button class="btn btn-ghost btn-sm" type="button" onclick={() => (filter = { ...NO_FILTER })}>Hapus filter</button>
    {/if}
  </div>

  <div class="messages">
    {#if loadError}
      <Banner tone="danger">Daftar user gagal dimuat: {errorLabel(loadError)}</Banner>
    {/if}
    {#if actionError}
      <Banner tone="danger">{errorLabel(actionError)}</Banner>
    {/if}
  </div>

  {#if list === null}
    {#if !loadError}<p class="muted empty">Memuat user…</p>{/if}
  {:else if shown.length === 0}
    <p class="muted empty">
      {list.length === 0 ? 'Belum ada user.' : 'Tidak ada user yang cocok dengan pencarian atau filter.'}
    </p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Peran</th>
            <th scope="col">Status</th>
            <th scope="col">Login terakhir</th>
            <th scope="col">Terdaftar</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as user (user.id)}
            {@const lock = lockReason(user, signedInId)}
            {@const busy = busyId === user.id}
            <tr class:inactive={!user.active}>
              <td>
                <div class="who">
                  <span class="avatar" aria-hidden="true">{initials(user.name)}</span>
                  <span class="names">
                    <span class="name">
                      {user.name}
                      {#if user.id === signedInId}<Badge tone="brand">Anda</Badge>{/if}
                    </span>
                    <span class="email">{user.email}</span>
                    {#if lock}
                      <span class="lock muted">
                        <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
                          <rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2" />
                          <path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2" />
                        </svg>
                        {lock}
                      </span>
                    {/if}
                  </span>
                </div>
              </td>
              <td data-label="Peran">
                {#if lock}
                  <Badge tone={user.role === 'admin' ? 'brand' : 'neutral'}>{ROLE_LABEL[user.role]}</Badge>
                {:else}
                  <select
                    class="pick"
                    class:admin={user.role === 'admin'}
                    aria-label={`Peran ${user.name}`}
                    value={user.role}
                    disabled={busy}
                    onchange={(e) => choose(e.currentTarget, user.role, user, { role: e.currentTarget.value as Role })}
                  >
                    {#each ROLE_CHOICES as role (role)}<option value={role}>{ROLE_LABEL[role]}</option>{/each}
                  </select>
                {/if}
              </td>
              <td data-label="Status">
                {#if lock}
                  <Badge tone={user.active ? 'success' : 'warning'}>{user.active ? 'Aktif' : 'Nonaktif'}</Badge>
                {:else}
                  {@const status = user.active ? 'active' : 'inactive'}
                  <select
                    class="pick"
                    class:on={user.active}
                    class:off={!user.active}
                    aria-label={`Status ${user.name}`}
                    value={status}
                    disabled={busy}
                    onchange={(e) => choose(e.currentTarget, status, user, { active: e.currentTarget.value === 'active' })}
                  >
                    {#each STATUS_CHOICES as choice (choice.value)}<option value={choice.value}>{choice.label}</option>{/each}
                  </select>
                {/if}
              </td>
              <td data-label="Login terakhir" class="when">
                {#if user.lastLoginAt}{formatDateTime(user.lastLoginAt)}{:else}<span class="muted">Belum tercatat</span>{/if}
              </td>
              <td data-label="Terdaftar" class="when">{formatDay(user.createdAt)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<div class="toast-region" aria-live="polite">
  {#if done}
    <div class="toast">
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path d="m5 12 5 5 9-10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
      </svg>
      {done}
    </div>
  {/if}
</div>

<ConfirmDialog
  open={pending !== null}
  title={pending?.question.title ?? ''}
  confirmLabel={pending?.question.confirm ?? ''}
  busy={busyId !== null}
  onconfirm={() => pending && apply(pending.user, pending.change)}
  oncancel={() => (pending = null)}
>
  <p>{pending?.question.body ?? ''}</p>
  {#if pending}<p class="muted target">{pending.user.email}</p>{/if}
</ConfirmDialog>

<style>
  .users {
    overflow: hidden;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--blue-space-3);
    padding: var(--blue-space-5) var(--blue-space-5) var(--blue-space-3);
  }
  .title {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-1);
    max-width: 720px;
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    font-weight: 700;
  }
  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: var(--blue-space-2);
    margin: 0;
    padding: 0 var(--blue-space-5) var(--blue-space-3);
    list-style: none;
    font-size: 13px;
    color: var(--blue-text-muted);
  }
  .summary li {
    padding: 2px var(--blue-space-3);
    border-radius: 999px;
    background: var(--blue-surface-muted);
    box-shadow: inset 0 0 0 1px var(--blue-border);
  }
  .summary strong {
    color: var(--blue-text);
  }
  .summary li.waiting {
    background: var(--blue-warning-subtle);
    box-shadow: none;
    color: var(--blue-warning-text);
  }
  .summary li.waiting strong {
    color: var(--blue-warning-text);
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2) var(--blue-space-3);
    padding: 0 var(--blue-space-5) var(--blue-space-4);
    border-bottom: 1px solid var(--blue-border);
  }
  .search {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    flex: 1 1 260px;
    max-width: 420px;
    min-height: 40px;
    padding: 0 var(--blue-space-3);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text-muted);
  }
  .search:focus-within {
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  .search input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    background: transparent;
    color: var(--blue-text);
    font: inherit;
  }
  select {
    min-height: 40px;
    padding: 0 var(--blue-space-2);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    font: inherit;
    font-size: 14px;
  }
  select:focus-visible {
    outline: none;
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  select.set {
    border-color: var(--blue-brand);
    background: var(--blue-brand-subtle);
    color: var(--blue-link);
    font-weight: 600;
  }
  .messages:not(:empty) {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-2);
    padding: var(--blue-space-3) var(--blue-space-5) 0;
  }
  .empty {
    padding: var(--blue-space-6) var(--blue-space-5);
    text-align: center;
  }
  .table-wrap {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    background: var(--blue-surface-muted);
    text-align: left;
    font-size: 12px;
    font-weight: 700;
    color: var(--blue-text-muted);
    padding-top: var(--blue-space-2);
    padding-bottom: var(--blue-space-2);
    border-bottom: 1px solid var(--blue-border);
    white-space: nowrap;
  }
  th,
  td {
    padding-left: var(--blue-space-3);
    padding-right: var(--blue-space-3);
  }
  th:first-child,
  td:first-child {
    padding-left: var(--blue-space-5);
  }
  th:last-child,
  td:last-child {
    padding-right: var(--blue-space-5);
  }
  td {
    padding-top: var(--blue-space-3);
    padding-bottom: var(--blue-space-3);
    border-bottom: 1px solid var(--blue-border);
    vertical-align: middle;
    font-size: 13px;
  }
  tr:last-child td {
    border-bottom: none;
  }
  tr.inactive {
    background: #fffdf5;
  }
  .who {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    min-width: 240px;
  }
  .avatar {
    flex: none;
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--blue-brand-subtle);
    color: var(--blue-brand-pressed);
    font-size: 12px;
    font-weight: 700;
  }
  .names {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .name {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2);
    font-size: 14px;
    font-weight: 600;
  }
  .email {
    color: var(--blue-text-muted);
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  .when {
    white-space: nowrap;
  }
  .lock {
    display: flex;
    align-items: center;
    gap: var(--blue-space-1);
    margin-top: 2px;
    font-size: 12px;
  }
  .lock svg {
    flex: none;
  }
  /* A row's role and status dropdowns, tinted like the badges they replace so the column still reads at a glance. */
  .pick {
    min-width: 116px;
    min-height: 32px;
    padding: 0 var(--blue-space-2);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .pick:hover:not(:disabled) {
    border-color: var(--blue-brand);
  }
  .pick:focus-visible {
    outline: none;
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  .pick:disabled {
    cursor: progress;
    opacity: 0.6;
  }
  .pick.admin {
    border-color: #b3dff3;
    background: var(--blue-brand-subtle);
    color: var(--blue-brand-pressed);
  }
  .pick.on {
    border-color: #b7dcb9;
    background: var(--blue-success-subtle);
    color: var(--blue-success);
  }
  .pick.off {
    border-color: #f5d27a;
    background: var(--blue-warning-subtle);
    color: var(--blue-warning-text);
  }
  .pick option {
    background: var(--blue-surface);
    color: var(--blue-text);
  }
  .target {
    margin-top: var(--blue-space-2);
    font-size: 13px;
    overflow-wrap: anywhere;
  }
  .toast-region {
    position: fixed;
    left: 50%;
    bottom: var(--blue-space-5);
    z-index: 20;
    transform: translateX(-50%);
    width: max-content;
    max-width: calc(100vw - 2 * var(--blue-space-4));
  }
  .toast {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    padding: var(--blue-space-3) var(--blue-space-4);
    border-radius: var(--blue-radius);
    background: var(--blue-text);
    color: var(--blue-text-inverse);
    box-shadow: 0 4px 16px rgba(49, 53, 59, 0.25);
    font-size: 14px;
  }
  .toast svg {
    flex: none;
    color: #8fd19e;
  }
  /* Too narrow for the table: each user becomes a block, with the column names beside the values. */
  @media (max-width: 760px) {
    .head,
    .summary,
    .filters,
    .messages:not(:empty) {
      padding-left: var(--blue-space-4);
      padding-right: var(--blue-space-4);
    }
    thead {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
    }
    table,
    tbody,
    tr,
    td {
      display: block;
    }
    tr {
      padding: var(--blue-space-3) var(--blue-space-4);
      border-bottom: 1px solid var(--blue-border);
    }
    td,
    td:first-child,
    td:last-child {
      padding: var(--blue-space-1) 0;
      border-bottom: none;
    }
    td[data-label] {
      display: grid;
      grid-template-columns: 112px minmax(0, 1fr);
      align-items: center;
      justify-items: start; /* a badge or dropdown keeps its own width */
      gap: var(--blue-space-2);
    }
    td[data-label]::before {
      content: attr(data-label);
      font-size: 12px;
      font-weight: 700;
      color: var(--blue-text-muted);
    }
    .who {
      min-width: 0;
      padding-bottom: var(--blue-space-2);
    }
  }
</style>
