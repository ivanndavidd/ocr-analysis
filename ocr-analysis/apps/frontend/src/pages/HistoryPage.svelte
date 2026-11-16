<script lang="ts">
  import type { JobHistory } from '@contract-ocr/shared-types';
  import { untrack } from 'svelte';
  import Banner from '../components/Banner.svelte';
  import { api } from '../services/api.ts';
  import { jobFlowLabel } from '../utils/claude.ts';
  import { formatCount, formatDateTime } from '../utils/format.ts';
  import {
    DEFAULT_VIEW,
    MAX_SEARCH,
    MODE_OPTIONS,
    OUTCOME_OPTIONS,
    PAGE_SIZES,
    STATUS_OPTIONS,
    type HistoryView,
    dayOf,
    narrows,
    pageList,
    parseView,
    viewFilter,
    viewQuery,
  } from '../utils/history.ts';
  import { isActive, jobTitle, runState } from '../utils/jobs.ts';
  import { folderHref, historyHref, jobHref } from '../utils/routes.ts';

  // Every user's runs, newest first, kept in the database: who ran what, when, and how it came out. The backend searches,
  // filters and pages; this page asks for one page at a time. Its search, filters and page live in the address.

  const REFRESH_MS = 5_000; // while a run on the page is queued or running
  const SEARCH_DELAY_MS = 300;

  let { rootName, query }: { rootName: string; query: string } = $props();
  const view = $derived(parseView(query));
  let text = $state(''); // the search box, ahead of the address while typing
  let result = $state<JobHistory | null>(null);
  let loading = $state(false);
  let failed = $state(false);
  let asked = 0; // the newest request: an older answer that arrives late is dropped
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  const today = dayOf(new Date());

  const pages = $derived(result ? Math.max(1, Math.ceil(result.total / view.size)) : 1);
  const first = $derived((view.page - 1) * view.size + 1);
  const live = $derived(result?.jobs.some(isActive) ?? false);
  const narrowed = $derived(narrows(view));

  const where = (folders: string[]) =>
    folders.length === 1 ? folders[0] || rootName : folders.slice(0, 2).join(', ') + (folders.length > 2 ? ', …' : '');

  /** Changes the list by changing the address; a new search or filter starts again at page 1. */
  function go(next: Partial<HistoryView>) {
    const href = historyHref(viewQuery({ ...view, page: 1, ...next }));
    if (href === location.hash) return;
    window.history.replaceState(window.history.state, '', href);
    dispatchEvent(new HashChangeEvent('hashchange'));
  }

  async function load(target: HistoryView, quiet: boolean) {
    const ask = ++asked;
    if (!quiet) loading = true;
    try {
      const page = await api.history((target.page - 1) * target.size, target.size, viewFilter(target));
      if (ask !== asked) return;
      const last = Math.max(1, Math.ceil(page.total / target.size));
      if (target.page > last) return go({ page: last }); // fewer runs match than the address expects
      result = page;
      failed = false;
    } catch {
      if (ask === asked && !quiet) failed = true;
    } finally {
      if (ask === asked) loading = false;
    }
  }

  // A new address: the page it asks for.
  $effect(() => {
    const target = view;
    untrack(() => void load(target, false));
  });

  // The address's search into the box, unless the box already says it (spaces still being typed included).
  $effect(() => {
    const q = view.q;
    untrack(() => {
      if (text.trim() !== q.trim()) text = q;
    });
  });

  // While a run on the page is going, the page again now and then, so its counts move and a new run shows on top.
  $effect(() => {
    if (!live) return;
    const timer = setInterval(() => void load(untrack(() => view), true), REFRESH_MS);
    return () => clearInterval(timer);
  });

  $effect(() => () => clearTimeout(searchTimer));

  function typed() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => go({ q: text }), SEARCH_DELAY_MS);
  }

  function searchKey(event: KeyboardEvent) {
    if (event.key !== 'Enter') return;
    clearTimeout(searchTimer);
    go({ q: text });
  }

  function reset() {
    clearTimeout(searchTimer);
    text = '';
    go({ ...DEFAULT_VIEW, size: view.size });
  }

  function turn(page: number) {
    go({ page });
    document.getElementById('history-title')?.scrollIntoView({ block: 'start' });
  }
</script>

<section class="card history" aria-labelledby="history-title">
  <div class="head">
    <h1 id="history-title">Riwayat OCR</h1>
    <span class="muted">Proses semua user, terbaru dulu</span>
  </div>

  <div class="filters" role="search" aria-label="Cari dan saring riwayat">
    <label class="search">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2" />
        <path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        bind:value={text}
        oninput={typed}
        onkeydown={searchKey}
        maxlength={MAX_SEARCH}
        placeholder="Cari folder, nama, atau email user"
        aria-label="Cari folder, nama, atau email user"
        autocomplete="off"
        spellcheck="false"
      />
    </label>
    <div class="selects">
      <select
        aria-label="Status proses"
        class:set={view.status}
        value={view.status}
        onchange={(e) => go({ status: e.currentTarget.value as HistoryView['status'] })}
      >
        <option value="">Semua status</option>
        {#each STATUS_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
      </select>
      <select
        aria-label="Hasil"
        class:set={view.outcome}
        value={view.outcome}
        onchange={(e) => go({ outcome: e.currentTarget.value as HistoryView['outcome'] })}
      >
        <option value="">Semua hasil</option>
        {#each OUTCOME_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
      </select>
      <select
        aria-label="Cara baca"
        class:set={view.mode}
        value={view.mode}
        onchange={(e) => go({ mode: e.currentTarget.value as HistoryView['mode'] })}
      >
        <option value="">Semua cara baca</option>
        {#each MODE_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
      </select>
      <select
        aria-label="User"
        class:set={view.mine}
        value={view.mine ? 'mine' : ''}
        onchange={(e) => go({ mine: e.currentTarget.value === 'mine' })}
      >
        <option value="">Semua user</option>
        <option value="mine">Proses saya</option>
      </select>
    </div>
    <div class="dates">
      <label class="date">
        <span>Dari</span>
        <input
          type="date"
          class:set={view.from}
          value={view.from}
          max={view.to || today}
          onchange={(e) => go({ from: e.currentTarget.value })}
        />
      </label>
      <label class="date">
        <span>Sampai</span>
        <input
          type="date"
          class:set={view.to}
          value={view.to}
          min={view.from || undefined}
          max={today}
          onchange={(e) => go({ to: e.currentTarget.value })}
        />
      </label>
      {#if narrowed}<button class="link" type="button" onclick={reset}>Hapus filter</button>{/if}
    </div>
  </div>

  {#if failed}
    <div class="pad">
      <Banner tone="danger">
        Riwayat gagal dimuat. <button class="link" type="button" onclick={() => load(view, false)}>Coba lagi</button>
      </Banner>
    </div>
  {/if}

  {#if result}
    <p class="count" aria-live="polite">
      {#if result.total > 0}
        Menampilkan <strong>{formatCount(first)}–{formatCount(first + result.jobs.length - 1)}</strong> dari
        <strong>{formatCount(result.total)}</strong> proses{#if narrowed}&nbsp;yang cocok{/if}
      {:else}
        0 proses
      {/if}
      {#if loading}<span class="muted"> · memuat…</span>{/if}
    </p>

    {#if result.total === 0}
      <div class="state empty">
        {#if narrowed}
          <strong>Tidak ada proses yang cocok</strong>
          <span class="muted">Ubah kata kunci atau filternya.</span>
          <button class="btn btn-outline btn-sm" type="button" onclick={reset}>Hapus filter</button>
        {:else}
          <strong>Belum ada proses OCR</strong>
          <span class="muted">Pilih folder atau PDF, lalu tekan Jalankan OCR.</span>
          <a class="btn btn-outline btn-sm" href={folderHref('')}>Buka folder</a>
        {/if}
      </div>
    {:else}
      <ul class:stale={loading}>
        {#each result.jobs as job (job.id)}
          <li>
            <div class="what">
              <a href={jobHref(job.id)}>{jobTitle(job, rootName)}</a>
              <span class="muted small">{where(job.folders)} · {jobFlowLabel(job.claude, job.mode)}</span>
              <span class="muted small">
                {#if job.startedBy}oleh <strong class="by">{job.startedBy.name}</strong>&nbsp;·&nbsp;{/if}<span class:live={isActive(job)}>{runState(job)}</span>
                {formatDateTime(isActive(job) ? job.startedAt : (job.finishedAt ?? job.startedAt))}
              </span>
            </div>
            <div class="counts">
              <span class="ready">{job.ready} siap</span>
              <span class="check">{job.check} perlu dicek</span>
              {#if job.failed}<span class="failed">{job.failed} gagal</span>{/if}
              <span class="total muted">dari {job.total}</span>
            </div>
            <a class="btn btn-ghost btn-sm" href={jobHref(job.id)}>{isActive(job) ? 'Lihat progres' : 'Buka hasil'}</a>
          </li>
        {/each}
      </ul>
      <div class="foot">
        <label class="size">
          <span class="muted small">Per halaman</span>
          <select value={String(view.size)} onchange={(e) => go({ size: Number(e.currentTarget.value) })}>
            {#each PAGE_SIZES as size (size)}<option value={String(size)}>{size}</option>{/each}
          </select>
        </label>
        {#if pages > 1}
          <nav class="pager" aria-label="Halaman riwayat">
            <button class="btn btn-outline btn-sm" type="button" disabled={view.page <= 1} onclick={() => turn(view.page - 1)}>
              ‹ Sebelumnya
            </button>
            {#each pageList(view.page, pages) as n, i (n ?? `gap-${i}`)}
              {#if n === null}
                <span class="gap" aria-hidden="true">…</span>
              {:else}
                <button
                  class="num"
                  class:current={n === view.page}
                  type="button"
                  aria-label="Halaman {n}"
                  aria-current={n === view.page ? 'page' : undefined}
                  onclick={() => turn(n)}>{n}</button
                >
              {/if}
            {/each}
            <button class="btn btn-outline btn-sm" type="button" disabled={view.page >= pages} onclick={() => turn(view.page + 1)}>
              Berikutnya ›
            </button>
          </nav>
        {/if}
      </div>
    {/if}
  {:else if !failed}
    <p class="muted state">Memuat riwayat…</p>
  {/if}
</section>

<style>
  .history {
    padding: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--blue-space-1) var(--blue-space-3);
    padding: var(--blue-space-5) var(--blue-space-5) var(--blue-space-3);
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    font-weight: 700;
  }
  .small {
    font-size: 12px;
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
    flex: 1 1 280px;
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
  .selects,
  .dates {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2);
  }
  select,
  .date input {
    min-height: 40px;
    padding: 0 var(--blue-space-2);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    font: inherit;
    font-size: 14px;
  }
  select:focus-visible,
  .date input:focus-visible {
    outline: none;
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  select.set,
  .date input.set {
    border-color: var(--blue-brand);
    background: var(--blue-brand-subtle);
    color: var(--blue-link);
    font-weight: 600;
  }
  .date {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    font-size: 13px;
    color: var(--blue-text-muted);
  }
  .pad {
    padding: var(--blue-space-4) var(--blue-space-5) 0;
  }
  .count {
    margin: 0;
    padding: var(--blue-space-3) var(--blue-space-5);
    font-size: 13px;
    color: var(--blue-text-muted);
    border-bottom: 1px solid var(--blue-border);
  }
  .count strong {
    color: var(--blue-text);
  }
  .state {
    padding: var(--blue-space-6) var(--blue-space-5);
    text-align: center;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--blue-space-2);
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    transition: opacity 0.15s;
  }
  ul.stale {
    opacity: 0.55;
  }
  li {
    position: relative;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2) var(--blue-space-4);
    padding: var(--blue-space-3) var(--blue-space-5);
  }
  li + li {
    border-top: 1px solid var(--blue-border);
  }
  li:hover {
    background: var(--blue-surface-muted);
  }
  /* The whole row opens the run: the title's link stretches over it, so the row stays one link with a short name. */
  li:has(.what a:focus-visible) {
    outline: 2px solid var(--blue-brand);
    outline-offset: -2px;
  }
  .what {
    flex: 1 1 260px;
    min-width: 0;
  }
  .what a {
    display: block;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .what a::after {
    content: '';
    position: absolute;
    inset: 0;
  }
  .what a:focus-visible {
    outline: none;
  }
  li > .btn {
    position: relative; /* above the stretched link, so it keeps its own hover */
  }
  .what .muted {
    display: block;
    overflow-wrap: anywhere;
  }
  .by {
    color: var(--blue-text);
    font-weight: 600;
  }
  .live {
    color: var(--blue-brand);
    font-weight: 600;
  }
  .counts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--blue-space-2);
    font-size: 12px;
    font-weight: 600;
  }
  .counts span {
    padding: 2px var(--blue-space-2);
    border-radius: 999px;
  }
  .counts .total {
    padding-left: 0;
    font-weight: 400;
  }
  .ready {
    background: var(--blue-success-subtle);
    color: var(--blue-success);
  }
  .check {
    background: var(--check-fill);
    color: var(--blue-warning-text);
  }
  .failed {
    background: var(--blue-danger-subtle);
    color: var(--blue-danger-text);
  }
  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--blue-space-3);
    padding: var(--blue-space-3) var(--blue-space-5);
    border-top: 1px solid var(--blue-border);
  }
  .size {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
  }
  .size select {
    min-height: 32px;
  }
  .pager {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-1);
  }
  .num {
    min-width: 32px;
    height: 32px;
    padding: 0 var(--blue-space-2);
    border: 1px solid transparent;
    border-radius: var(--blue-radius);
    background: none;
    color: var(--blue-text);
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .num:hover {
    background: var(--blue-surface-muted);
  }
  .num.current {
    border-color: var(--blue-brand);
    background: var(--blue-brand);
    color: var(--blue-text-inverse);
    cursor: default;
  }
  .num:focus-visible {
    outline: 2px solid var(--blue-brand);
    outline-offset: 1px;
  }
  .gap {
    padding: 0 var(--blue-space-1);
    color: var(--blue-text-muted);
  }
  .link {
    padding: 0;
    border: none;
    background: none;
    color: var(--blue-link);
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .link:hover {
    text-decoration: underline;
  }
  @media (max-width: 640px) {
    .head,
    .filters,
    .count,
    li,
    .foot,
    .pad {
      padding-left: var(--blue-space-4);
      padding-right: var(--blue-space-4);
    }
    .search {
      max-width: none;
    }
    .selects select {
      flex: 1 1 140px;
      min-width: 0;
    }
    .selects,
    .dates {
      width: 100%;
    }
    .date {
      flex: 1 1 140px;
    }
    .date input {
      flex: 1;
      min-width: 0;
    }
    .pager .btn {
      padding-left: var(--blue-space-2);
      padding-right: var(--blue-space-2);
    }
  }
</style>
