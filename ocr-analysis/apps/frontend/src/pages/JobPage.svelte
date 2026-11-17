<script lang="ts">
  import type { ContractRow, FieldKey, Job, JobFile } from '@contract-ocr/shared-types';
  import { onMount } from 'svelte';
  import Badge from '../components/Badge.svelte';
  import Banner from '../components/Banner.svelte';
  import ContractCheck from '../components/ContractCheck.svelte';
  import PdfPreview from '../components/PdfPreview.svelte';
  import { ApiFailure, api, failureCode } from '../services/api.ts';
  import {
    FIELD_GROUPS,
    FIELD_LABEL,
    GROUP_LABEL,
    type FieldGroup,
    type RowState,
    displayValue,
    fullNik,
    groupsToCheck,
    periodCell,
    rowState,
    toCheck,
  } from '../utils/fields.ts';
  import { saveBlob } from '../utils/download.ts';
  import { formatNik, formatTime, formatTokens } from '../utils/format.ts';
  import { FILE_STATE, JOB_STATUS, errorLabel } from '../utils/labels.ts';
  import { matches } from '../utils/listing.ts';
  import { jobFlowLabel } from '../utils/claude.ts';
  import { claudeTokens, groupByFolder, isActive, jobHome, jobTitle, relativeTo } from '../utils/jobs.ts';
  import { folderHref, jobHref } from '../utils/routes.ts';

  const POLL_MS = 2000;
  // After the name column. The three Excel period columns share one cell here; the check dialog shows them apart.
  type Column = Exclude<FieldKey, 'name' | 'start' | 'end' | 'months'> | 'period';
  const DATA: Column[] = ['nik', 'vendor', 'entity', 'position', 'period', 'signature'];
  const COLUMNS = 3 + DATA.length; // status, employee, data, action
  const MAX_FOLDERS_LISTED = 3;

  type Tab = 'all' | RowState;

  let { id, rootName, userId }: { id: string; rootName: string; userId: number } = $props();

  let job = $state<Job | null>(null);
  let loadError = $state<string | null>(null);
  let actionError = $state<{ code: string; jobId?: string } | null>(null);
  let busy = $state<string | null>(null); // 'cancel', 'export', 'retry-failed', or the path of the file being retried
  let tab = $state<Tab>('all');
  let field = $state<FieldGroup | null>(null); // only rows whose value in this column needs a check
  let query = $state('');
  let checking = $state<string | null>(null); // path of the contract open next to its PDF
  let preview = $state<string | null>(null); // path of a PDF without results (failed or not read yet)
  // Full NIK by path, shown in place of the masked one (owner decision 2026-11-02: fully visible for now).
  let niks = $state<Record<string, string>>({});

  let timer: ReturnType<typeof setTimeout> | undefined;
  let alive = true;

  async function load() {
    clearTimeout(timer);
    try {
      const next = await api.job(id);
      if (!alive) return;
      job = next;
      loadError = null;
      if (next.files.some((f) => f.row?.nik.masked && !fullNik(f.row, niks[f.path]))) await loadNiks();
    } catch (error) {
      if (!alive) return;
      loadError = failureCode(error);
    }
    if ((job && isActive(job)) || loadError === 'network') timer = setTimeout(load, POLL_MS);
  }

  async function loadNiks() {
    try {
      const loaded = (await api.niks(id)).niks;
      if (alive) niks = loaded;
    } catch {
      // the masked NIK stays on screen; while the job runs, the next poll asks again
    }
  }

  onMount(() => {
    void load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  });

  async function act(key: string, call: () => Promise<Job>) {
    busy = key;
    actionError = null;
    try {
      job = await call();
      void load();
    } catch (error) {
      actionError = { code: failureCode(error), jobId: error instanceof ApiFailure ? error.jobId : undefined };
    } finally {
      busy = null;
    }
  }

  async function download() {
    busy = 'export';
    actionError = null;
    try {
      const { blob, name } = await api.exportXlsx(id);
      saveBlob(blob, name);
    } catch (error) {
      actionError = { code: failureCode(error) };
    } finally {
      busy = null;
    }
  }

  const files = $derived(job?.files ?? []);
  const counts = $derived.by(() => {
    const byState: Record<RowState, number> = { ready: 0, check: 0, failed: 0, pending: 0 };
    for (const file of files) byState[rowState(file)] += 1;
    return byState;
  });
  const total = $derived(files.length);
  const read = $derived(files.filter((f) => f.state === 'done' || f.state === 'error').length);
  const retryable = $derived(files.filter((f) => f.state === 'error' || f.state === 'cancelled').length);
  const tokens = $derived(claudeTokens(files));
  const shared = $derived(files.filter((f) => f.readIn).length);
  const title = $derived(job ? jobTitle(job, rootName) : '');
  const home = $derived(job ? jobHome(job) : '');
  const active = $derived(job ? isActive(job) : false); // queued or running
  // anyone signed in may open any run; only whoever started it may cancel or retry it
  const mine = $derived(!job?.startedBy || job.startedBy.id === userId);
  const starter = $derived(job?.startedBy?.name ?? null);

  const nameOf = (file: JobFile) => (file.row ? displayValue('name', file.row.fields.name) : null);
  const inTab = $derived(
    files.filter(
      (file) =>
        (tab === 'all' || rowState(file) === tab) && (!query.trim() || matches(`${nameOf(file) ?? ''} ${file.name}`, query)),
    ),
  );
  const fieldCounts = $derived.by(() => {
    const byGroup: Record<FieldGroup, number> = { nik: 0, vendor: 0, entity: 0, name: 0, position: 0, period: 0, signature: 0 };
    for (const file of inTab) if (file.row) for (const group of groupsToCheck(file.row)) byGroup[group] += 1;
    return byGroup;
  });
  const shown = $derived(field ? inTab.filter((file) => file.row && groupsToCheck(file.row).has(field!)) : inTab);
  const groups = $derived(groupByFolder(shown));
  const grouped = $derived(new Set(files.map((f) => f.folder)).size > 1);
  const filtering = $derived(tab !== 'all' || field !== null || query.trim() !== '');

  // The dialog walks the rows in the order the table shows them.
  const openable = $derived(groups.flatMap((g) => g.files).filter((f) => f.row));
  const checkingFile = $derived(checking ? (files.find((f) => f.path === checking && f.row) ?? null) : null);
  const at = $derived(checking ? openable.findIndex((f) => f.path === checking) : -1);
  const step = (offset: number) => {
    const next = openable[at + offset];
    return at >= 0 && next ? () => (checking = next.path) : null;
  };

  const TABS: Array<{ key: Tab; label: string; hint: string }> = [
    { key: 'all', label: 'Semua', hint: 'Semua file di proses ini' },
    { key: 'ready', label: 'Siap dipakai', hint: 'Semua kolom terbaca dan cocok' },
    { key: 'check', label: 'Perlu dicek', hint: 'Ada kolom kuning: cocokkan dengan PDF' },
    { key: 'failed', label: 'Gagal dibaca', hint: 'File tidak terbaca; coba lagi' },
    { key: 'pending', label: 'Belum selesai', hint: 'Masih antre, sedang dibaca, atau dibatalkan' },
  ];
  const tabCount = (key: Tab) => (key === 'all' ? total : counts[key]);
  const visibleTabs = $derived(TABS.filter((t) => t.key !== 'pending' || counts.pending > 0));

  function pick(next: Tab) {
    tab = next;
    field = null;
  }

  /** The check list from its first contract: the dialog then walks every contract with a yellow value. */
  function startChecking() {
    tab = 'check';
    field = null;
    query = '';
    checking = groupByFolder(files.filter((f) => rowState(f) === 'check')).flatMap((g) => g.files)[0]?.path ?? null;
  }

  function clearFilters() {
    tab = 'all';
    field = null;
    query = '';
  }

  const folderChecks = (group: { files: JobFile[] }) => group.files.filter((f) => rowState(f) === 'check').length;
  const flaggedCount = (row: ContractRow) => toCheck(row).length;
</script>

{#snippet empty()}
  <span class="empty" aria-hidden="true">—</span><span class="sr-only">Kosong</span>
{/snippet}

{#snippet value(key: FieldKey, row: ContractRow, nik: string | null)}
  {@const item = row.fields[key]}
  {@const text = key === 'nik' && nik ? formatNik(nik) : displayValue(key, item)}
  <td class="value {key}" class:flag={item.check !== null} title={item.check ?? item.basis ?? undefined}>
    {#if text}
      <span class="text" class:mono={key === 'nik'}>{text}</span>
    {:else}
      {@render empty()}
    {/if}
    {#if item.check}<span class="note">{item.check}</span>{/if}
  </td>
{/snippet}

{#snippet period(row: ContractRow)}
  {@const cell = periodCell(row)}
  <td class="value period" class:flag={cell.checks.length > 0} title={cell.checks.join('; ') || cell.basis || undefined}>
    {#if cell.range}<span class="text">{cell.range}</span>{:else}{@render empty()}{/if}
    {#if cell.months}<span class="sub">{cell.months}</span>{/if}
    {#if cell.checks.length}<span class="note">{cell.checks[0]}</span>{/if}
  </td>
{/snippet}

{#if loadError && !job}
  <Banner tone="danger">{errorLabel(loadError)} <a href={folderHref('')}>Kembali ke {rootName}</a></Banner>
{:else if !job}
  <p class="muted">Memuat proses…</p>
{:else}
  <nav class="crumbs" aria-label="Lokasi">
    <a href={folderHref(home)}>← Kembali ke folder</a>
  </nav>

  <section class="card summary">
    <div class="head">
      <div class="heading">
        <div class="title-line">
          <h1>{title}</h1>
          <Badge tone={active ? 'brand' : job.status === 'done' ? 'success' : job.status === 'interrupted' ? 'warning' : 'neutral'}>
            {JOB_STATUS[job.status]}
          </Badge>
        </div>
        <p class="muted meta">
          {#if job.folders.length === 1}
            {job.folders[0] || rootName}
          {:else}
            {home || rootName}:
            {job.folders.slice(0, MAX_FOLDERS_LISTED).map((f) => relativeTo(f, home, rootName)).join(', ')}{#if job.folders.length > MAX_FOLDERS_LISTED}&nbsp;dan
              {job.folders.length - MAX_FOLDERS_LISTED} folder lain{/if}
          {/if}
          · {total} file · {jobFlowLabel(job.claude, job.mode)}{#if starter}&nbsp;· oleh&nbsp;<strong class="starter">{starter}</strong>{/if}
          · mulai {formatTime(job.startedAt)}{#if job.finishedAt}, selesai {formatTime(job.finishedAt)}{/if}
        </p>
        {#if tokens}
          <p class="muted meta">Claude dipakai untuk {tokens.files} file, {tokens.calls} panggilan · {formatTokens(tokens.used)}</p>
        {/if}
        {#if shared > 0}
          <p class="muted meta">{shared} file memakai hasil proses lain yang membaca PDF yang sama; tokennya dihitung di proses itu.</p>
        {/if}
      </div>
      {#if active && mine}
        <button class="btn btn-danger" disabled={busy !== null} onclick={() => act('cancel', () => api.cancel(id))}>
          {busy === 'cancel' ? 'Membatalkan…' : 'Batalkan'}
        </button>
      {:else if active}
        <span class="muted small">Hanya {starter ?? 'yang menjalankannya'} yang bisa membatalkan proses ini.</span>
      {:else if total > 0}
        <div class="export">
          <button class="btn btn-primary" disabled={busy !== null} onclick={download}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="none" stroke="currentColor" stroke-width="2" d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14" />
            </svg>
            {busy === 'export' ? 'Membuat Excel…' : 'Unduh Excel'}
          </button>
          <span class="muted small">Sel kuning di Excel sama dengan di halaman ini.<br />Berisi NIK lengkap; simpan seperti dokumen kontrak.</span>
        </div>
      {/if}
    </div>

    {#if active}
      <div class="progress-block">
        {#if job.status === 'queued'}
          <p class="queue" role="status">
            <strong>Menunggu giliran{#if job.queueAhead}: {job.queueAhead} proses di depan{/if}.</strong> Satu proses dibaca
            sekaligus dengan semua pembaca, urut dari yang lebih dulu dijalankan. PDF yang juga ada di proses di depan tidak
            dibaca dua kali: hasilnya dipakai di sini begitu selesai dibaca.
          </p>
        {/if}
        <div class="progress-label">
          <span>{read} dari {total} file sudah ada hasilnya</span>
          <span class="muted">{total ? Math.round((read / total) * 100) : 0}%</span>
        </div>
        <div class="progress" role="progressbar" aria-label="Progres baca" aria-valuemin="0" aria-valuemax={total} aria-valuenow={read}>
          <div class="bar" style:width="{total ? (read / total) * 100 : 0}%"></div>
        </div>
      </div>
    {/if}

    <div class="tiles" role="group" aria-label="Saring menurut hasil">
      {#each visibleTabs as item (item.key)}
        <button class="tile {item.key}" class:active={tab === item.key} aria-pressed={tab === item.key} onclick={() => pick(item.key)}>
          <span class="tile-value">{tabCount(item.key)}</span>
          <span class="tile-label">{item.label}</span>
          <span class="tile-hint">{item.hint}</span>
        </button>
      {/each}
    </div>

    {#if !active && read > 0}
      <div class="next" role="status">
        {#if counts.check > 0}
          <p>
            <strong>Langkah berikutnya:</strong> cek {counts.check} kontrak yang punya kolom kuning. Buka satu per satu di
            samping PDF-nya, atau unduh Excel dan cek di sana; sel kuningnya sama.
          </p>
          <button class="btn btn-outline btn-sm" onclick={startChecking}>Mulai cek</button>
        {:else if retryable === 0 && !job.missing}
          <p><strong>Semua kontrak siap dipakai.</strong> Unduh Excel untuk datanya.</p>
        {/if}
        {#if retryable > 0 && !mine}
          <p class="failed-hint">{retryable} file gagal dibaca atau dibatalkan. Hanya {starter} yang bisa mengulangnya.</p>
        {:else if retryable > 0}
          <p class="failed-hint">
            {retryable} file {counts.failed === 0
              ? 'dibatalkan sebelum dibaca'
              : counts.failed === retryable
                ? 'gagal dibaca'
                : 'gagal dibaca atau dibatalkan'}. Coba lagi semuanya sekaligus; file yang sudah terbaca tidak dibaca ulang.
            {#if counts.failed > 0}Satu per satu: buka <button class="link" onclick={() => pick('failed')}>Gagal dibaca</button>.{/if}
          </p>
          <button
            class="btn btn-primary btn-sm"
            disabled={busy !== null}
            onclick={() => act('retry-failed', () => api.retryFailed(id))}
          >
            {busy === 'retry-failed' ? 'Mengulang…' : `Coba lagi yang gagal (${retryable})`}
          </button>
        {/if}
      </div>
    {/if}
  </section>

  {#if actionError}
    <Banner tone="danger">
      {errorLabel(actionError.code)}.
      {#if actionError.jobId && actionError.jobId !== job.id}<a href={jobHref(actionError.jobId)}>Lihat proses yang berjalan</a>{/if}
    </Banner>
  {/if}
  {#if loadError}
    <Banner tone="warning">{errorLabel(loadError)} Mencoba lagi…</Banner>
  {/if}
  {#if job.status === 'interrupted'}
    <Banner tone="warning">
      Proses ini terputus karena backend dijalankan ulang saat proses masih berjalan atau menunggu giliran.
      {#if job.missing}{job.missing} file belum sempat dibaca dan tidak tercantum di sini; jalankan OCR lagi untuk foldernya.{/if}
    </Banner>
  {/if}
  {#if job.saveFailed}
    <Banner tone="warning">
      Hasil proses ini tidak tersimpan lengkap ke database. Hasilnya tetap bisa dilihat dan diunduh di sini selama
      backend belum dijalankan ulang.
    </Banner>
  {/if}

  <section class="card results">
    <div class="toolbar">
      <input
        class="search"
        type="search"
        placeholder="Cari nama karyawan atau nama file"
        aria-label="Cari nama karyawan atau nama file"
        bind:value={query}
      />
      <div class="legend">
        <span><span class="swatch flag" aria-hidden="true"></span>Kuning: cocokkan dengan PDF</span>
        <span><span class="swatch" aria-hidden="true"></span>Putih: terbaca dan cocok</span>
        <details>
          <summary>Kapan nilai dianggap cocok?</summary>
          {#if job.mode === 'full_page'}
            <ul>
              <li><strong>Nama</strong> dan <strong>masa kontrak</strong>: dibaca Claude dengan yakin dan sama dengan nama file.</li>
              <li>
                <strong>NIK</strong>: dibaca Claude dengan yakin dan cocok dengan tanggal lahir dan jenis kelamin yang tercetak di
                halaman pertama.
              </li>
              <li><strong>Posisi</strong>: dibaca Claude dengan yakin.</li>
              <li><strong>Tanda tangan</strong>: Claude yakin ada atau yakin kosong di halaman terakhir.</li>
            </ul>
            <p>
              Tanpa OCR (uji coba): halaman pertama dan terakhir setiap PDF dikirim utuh ke Claude tanpa disamarkan, termasuk
              nama, NIK, alamat, dan tanggal lahir. Halaman lain tidak dibaca, jadi data yang hanya ada di halaman tengah tidak
              terbaca.
            </p>
          {:else}
          <ul>
            <li>
              <strong>Nama</strong>: nama di nama file tercetak di halaman identitas, atau, bila pola teks tidak menemukannya,
              sama dengan nama karyawan yang dibaca Claude.
            </li>
            <li>
              <strong>NIK</strong>: cocok dengan tanggal lahir dan jenis kelamin di kontrak, diambil dari teks digital PDF, atau
              dibaca sama oleh laptop ini dan Claude.
            </li>
            <li><strong>Posisi</strong>: tertulis di halaman identitas dan terbaca jelas.</li>
            <li><strong>Masa kontrak</strong>: tanggal di dokumen sama dengan tanggal di nama file.</li>
            <li><strong>Tanda tangan</strong>: jelas ada atau jelas kosong di atas nama karyawan.</li>
          </ul>
          {#if job.claude}
            <p>
              Claude hanya menerima teks halaman identitas yang sudah disamarkan, potongan gambar baris NIK bila pemeriksaan
              di laptop ini ragu, dan potongan area tanda tangan (setiap scan dan foto, atau PDF teks yang ragu); tidak
              pernah satu halaman utuh. Setiap jawabannya dicek ulang di
              laptop ini: nilai yang tidak tertulis di dokumen dibuang, dan NIK harus cocok dengan tanggal lahir dan jenis
              kelamin.
            </p>
          {:else}
            <p>Semua dibaca di laptop ini; tidak ada isi kontrak yang dikirim keluar.</p>
          {/if}
          {/if}
        </details>
      </div>
    </div>

    {#if FIELD_GROUPS.some((g) => fieldCounts[g] > 0)}
      <div class="chips" role="group" aria-label="Saring menurut kolom yang perlu dicek">
        <span class="chips-label">Kolom kuning:</span>
        {#each FIELD_GROUPS as group (group)}
          {#if fieldCounts[group] > 0}
            <button
              class="chip"
              class:active={field === group}
              aria-pressed={field === group}
              onclick={() => (field = field === group ? null : group)}
            >
              {GROUP_LABEL[group]} <span class="n">{fieldCounts[group]}</span>
            </button>
          {/if}
        {/each}
        {#if field}<button class="link" onclick={() => (field = null)}>Semua kolom</button>{/if}
      </div>
    {/if}

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col" class="status-col">Hasil</th>
            <th scope="col">{FIELD_LABEL.name}</th>
            {#each DATA as key (key)}
              <th scope="col" class={key}>{key === 'period' ? GROUP_LABEL.period : FIELD_LABEL[key]}</th>
            {/each}
            <th scope="col"><span class="sr-only">Aksi</span></th>
          </tr>
        </thead>
        {#each groups as group (group.folder)}
          <tbody>
            {#if grouped}
              <tr class="group">
                <th colspan={COLUMNS} scope="rowgroup">
                  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                    <path fill="currentColor" d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                  </svg>
                  {relativeTo(group.folder, home, rootName)}
                  <span class="muted">· {group.files.length} file{#if folderChecks(group)}&nbsp;· {folderChecks(group)} perlu dicek{/if}</span>
                </th>
              </tr>
            {/if}
            {#each group.files as file (file.path)}
              {@const state = rowState(file)}
              {@const row = file.row}
              <tr class="row {state}">
                <td class="status-col">
                  {#if state === 'ready'}
                    <span class="state ready"><span aria-hidden="true">✓</span> Siap</span>
                  {:else if state === 'check' && row}
                    <span class="state check" title="{flaggedCount(row)} kolom perlu dicek">
                      <span aria-hidden="true">!</span> Cek {flaggedCount(row)}
                    </span>
                  {:else if state === 'failed'}
                    <span class="state failed"><span aria-hidden="true">×</span> Gagal</span>
                  {:else}
                    <span class="state pending">{FILE_STATE[file.state]}</span>
                  {/if}
                </td>
                {#if row && (state === 'ready' || state === 'check')}
                  <td class="who" class:flag={row.fields.name.check !== null}>
                    <button class="name" onclick={() => (checking = file.path)}>{nameOf(file) ?? file.name}</button>
                    <span class="filename" title={file.name}>{file.name}</span>
                    {#if file.readIn}
                      <a class="shared" href={jobHref(file.readIn.jobId)} title="PDF ini dibaca di proses lain yang berjalan bersamaan">
                        Hasil dari proses {file.readIn.by ?? 'lain'}
                      </a>
                    {/if}
                    {#if row.fields.name.check}<span class="note">{row.fields.name.check}</span>{/if}
                  </td>
                  {@const nik = fullNik(row, niks[file.path])}
                  {#each DATA as key (key)}
                    {#if key === 'period'}{@render period(row)}{:else}{@render value(key, row, nik)}{/if}
                  {/each}
                  <td class="actions">
                    <button class="btn btn-sm btn-outline" class:quiet={state === 'ready'} onclick={() => (checking = file.path)}>
                      {state === 'check' ? 'Cek' : 'Lihat'}
                    </button>
                  </td>
                {:else}
                  <td class="who"><span class="name plain">{file.name}</span></td>
                  <td colspan={DATA.length} class="message">
                    {#if state === 'failed'}
                      Tidak bisa dibaca: {errorLabel(file.error ?? 'internal_error')}.
                    {:else if file.state === 'cancelled'}
                      Dibatalkan sebelum dibaca.
                    {:else if file.waitingFor}
                      Sedang dibaca di <a href={jobHref(file.waitingFor.jobId)}>proses {file.waitingFor.by ?? 'lain'}</a>; hasilnya
                      dipakai di sini.
                    {:else}
                      {file.state === 'reading' ? 'Sedang dibaca…' : 'Menunggu giliran…'}
                    {/if}
                  </td>
                  <td class="actions">
                    {#if (state === 'failed' || file.state === 'cancelled') && !active && mine}
                      <button
                        class="btn btn-outline btn-sm"
                        disabled={busy !== null}
                        onclick={() => act(file.path, () => api.retry(id, file.path))}
                      >
                        {busy === file.path ? 'Mengulang…' : 'Coba lagi'}
                      </button>
                    {/if}
                    <button class="btn btn-ghost btn-sm" onclick={() => (preview = file.path)}>Lihat PDF</button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        {:else}
          <tbody>
            <tr>
              <td colspan={COLUMNS} class="empty-state">
                {#if tab === 'check' && !field && !query.trim() && !active}
                  <strong>Tidak ada yang perlu dicek.</strong> Semua data siap dipakai.
                {:else}
                  Tidak ada file untuk saringan ini.
                {/if}
                {#if filtering}<button class="link" onclick={clearFilters}>Tampilkan semua file</button>{/if}
              </td>
            </tr>
          </tbody>
        {/each}
      </table>
    </div>
  </section>
{/if}

{#if job}
  <ContractCheck
    file={checkingFile}
    nik={checkingFile ? fullNik(checkingFile.row, niks[checkingFile.path]) : null}
    position={at >= 0 ? { index: at, total: openable.length } : null}
    onprev={step(-1)}
    onnext={step(1)}
    onclose={() => (checking = null)}
  />
{/if}
<PdfPreview path={preview} onclose={() => (preview = null)} />

<style>
  .crumbs {
    font-size: 13px;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: var(--blue-space-4);
    padding: var(--blue-space-5) var(--blue-space-5) var(--blue-space-4);
  }
  /* A real basis, so on a phone the export block wraps below the title instead of squeezing it. */
  .heading {
    min-width: 0;
    flex: 1 1 320px;
  }
  .title-line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-3);
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    overflow-wrap: anywhere;
  }
  .meta {
    margin-top: 2px;
    font-size: 13px;
    overflow-wrap: anywhere;
  }
  .small {
    font-size: 12px;
  }
  .export {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--blue-space-1);
    text-align: right;
  }
  .progress-block {
    padding: 0 var(--blue-space-5) var(--blue-space-4);
  }
  .queue {
    margin: 0 0 var(--blue-space-3);
    padding: var(--blue-space-3);
    border-radius: var(--blue-radius);
    background: var(--blue-brand-subtle);
    font-size: 13px;
  }
  .starter {
    color: var(--blue-text);
    font-weight: 600;
  }
  .shared {
    display: inline-block;
    margin-top: 2px;
    font-size: 11px;
    font-weight: 600;
  }
  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: var(--blue-space-2);
  }
  .progress {
    height: 8px;
    border-radius: 999px;
    background: var(--blue-brand-subtle);
    overflow: hidden;
  }
  .bar {
    height: 100%;
    background: var(--blue-brand);
    border-radius: inherit;
    transition: width 0.4s ease;
  }
  @media (prefers-reduced-motion: reduce) {
    .bar {
      transition: none;
    }
  }
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: var(--blue-space-3);
    padding: 0 var(--blue-space-5) var(--blue-space-5);
  }
  .tile {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    padding: var(--blue-space-3) var(--blue-space-4);
    border: 1px solid var(--blue-border);
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .tile:hover {
    border-color: var(--blue-brand);
  }
  .tile.active {
    border-color: var(--blue-brand);
    box-shadow: inset 0 0 0 1px var(--blue-brand);
    background: var(--blue-brand-subtle);
  }
  .tile-value {
    font-size: 24px;
    font-weight: 700;
    line-height: 32px;
  }
  .tile.ready .tile-value {
    color: var(--blue-success);
  }
  .tile.check .tile-value {
    color: var(--blue-warning-text);
  }
  .tile.failed .tile-value {
    color: var(--blue-danger-text);
  }
  .tile-label {
    font-weight: 700;
  }
  .tile-hint {
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .next {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2) var(--blue-space-4);
    margin: 0 var(--blue-space-5) var(--blue-space-5);
    padding: var(--blue-space-3) var(--blue-space-4);
    border-radius: var(--blue-radius);
    background: var(--blue-surface-muted);
    font-size: 13px;
  }
  .next p {
    flex: 1 1 320px;
  }
  .failed-hint {
    color: var(--blue-danger-text);
  }
  .failed-hint .link {
    font-size: 13px;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-3) var(--blue-space-5);
    padding: var(--blue-space-3) var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
  }
  .search {
    flex: 1 1 260px;
    max-width: 360px;
    min-height: 36px;
    padding: 0 var(--blue-space-3);
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    font: inherit;
    color: var(--blue-text);
  }
  .search:focus {
    outline: none;
    border-color: var(--blue-brand);
    box-shadow: 0 0 0 3px var(--blue-brand-subtle);
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2) var(--blue-space-4);
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .swatch {
    display: inline-block;
    width: 12px;
    height: 12px;
    margin-right: var(--blue-space-1);
    vertical-align: -2px;
    border: 1px solid var(--blue-border-strong);
    border-radius: 2px;
    background: var(--blue-surface);
  }
  .swatch.flag {
    background: var(--check-fill);
    border-color: var(--check-border);
  }
  .legend details {
    position: relative;
  }
  .legend summary {
    cursor: pointer;
    color: var(--blue-link);
  }
  .legend details[open] > :not(summary) {
    color: var(--blue-text);
  }
  .legend details ul {
    margin: var(--blue-space-2) 0 var(--blue-space-1);
    padding-left: var(--blue-space-4);
    max-width: 560px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2);
    padding: var(--blue-space-2) var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
    background: var(--blue-surface-muted);
  }
  .chips-label {
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: var(--blue-space-1);
    min-height: 28px;
    padding: 0 var(--blue-space-3);
    border: 1px solid var(--check-border);
    border-radius: 999px;
    background: var(--check-fill);
    color: var(--blue-warning-text);
    font: 600 12px var(--blue-font);
    cursor: pointer;
  }
  .chip.active {
    background: var(--blue-warning-text);
    border-color: var(--blue-warning-text);
    color: var(--blue-text-inverse);
  }
  .chip.active .n {
    color: inherit;
  }
  .n {
    font-weight: 400;
  }
  .link {
    padding: 0;
    border: none;
    background: none;
    color: var(--blue-link);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .link:hover {
    text-decoration: underline;
  }
  /* Positioned, so the hidden screen-reader labels in the cells scroll with the table instead of widening the page. */
  .table-wrap {
    position: relative;
    overflow-x: auto;
  }
  /* Wide enough for the whole table: no scroll box, so the header sticks under the top bar while the page scrolls. */
  @media (min-width: 1240px) {
    .table-wrap {
      overflow: visible;
    }
    thead th {
      top: 60px;
    }
  }
  /* About 1120px wide with every column at its narrowest, so it fits the card from 1240px up, where it stops
     scrolling on its own: vendor, entity, position and the reasons wrap instead of widening it. */
  table {
    width: 100%;
    border-collapse: collapse;
    min-width: 920px;
  }
  th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--blue-surface-muted);
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    color: var(--blue-text-muted);
    padding: var(--blue-space-2) var(--blue-space-3);
    border-bottom: 1px solid var(--blue-border);
    white-space: nowrap;
  }
  th,
  td {
    padding-left: 10px;
    padding-right: 10px;
  }
  td {
    padding-top: var(--blue-space-2);
    padding-bottom: var(--blue-space-2);
    border-bottom: 1px solid var(--blue-border);
    vertical-align: top;
    font-size: 13px;
  }
  th:first-child,
  td:first-child {
    padding-left: var(--blue-space-5);
  }
  .group th {
    position: static;
    padding-top: var(--blue-space-4);
    background: var(--blue-surface);
    color: var(--blue-text);
    font-size: 13px;
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .group svg {
    vertical-align: -3px;
    margin-right: var(--blue-space-1);
    color: var(--blue-brand);
  }
  .status-col {
    width: 88px;
  }
  .state {
    display: inline-flex;
    gap: var(--blue-space-1);
    padding: 2px var(--blue-space-2);
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    white-space: nowrap;
  }
  .state.ready {
    background: var(--blue-success-subtle);
    color: var(--blue-success);
  }
  .state.check {
    background: var(--check-fill);
    color: var(--blue-warning-text);
  }
  .state.failed {
    background: var(--blue-danger-subtle);
    color: var(--blue-danger-text);
  }
  .state.pending {
    background: var(--blue-surface-muted);
    color: var(--blue-text-muted);
    font-weight: 600;
  }
  tr.row:hover > td:not(.flag) {
    background: var(--blue-surface-muted);
  }
  .who {
    min-width: 180px;
    max-width: 260px;
  }
  .name {
    display: block;
    padding: 0;
    border: none;
    background: none;
    color: var(--blue-link);
    font: 600 14px var(--blue-font);
    text-align: left;
    cursor: pointer;
    overflow-wrap: anywhere;
  }
  .name:hover {
    text-decoration: underline;
  }
  .name.plain {
    color: var(--blue-text);
    cursor: default;
  }
  /* As wide as the name above it and never wider: width 0 keeps a long filename from widening the column. */
  .filename {
    display: block;
    width: 0;
    min-width: 100%;
    margin-top: 2px;
    font-size: 11px;
    color: var(--blue-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* The Excel's yellow, with a bar on the left so two yellow cells side by side still read as two. */
  td.flag {
    background: var(--check-fill);
    box-shadow: inset 3px 0 0 var(--blue-warning);
  }
  .value {
    min-width: 96px;
  }
  .value.vendor,
  .value.entity {
    min-width: 112px;
    max-width: 170px;
  }
  .value.position {
    min-width: 112px;
    max-width: 180px;
  }
  .value.period {
    min-width: 164px;
  }
  .text {
    white-space: nowrap;
  }
  .value.vendor .text,
  .value.entity .text,
  .value.position .text {
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .mono {
    font-size: 12px;
  }
  .empty {
    color: var(--blue-text-muted);
  }
  .sub {
    display: block;
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  /* Two lines at most; the whole reason is in the cell's tooltip and in the check dialog. */
  .note {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    margin-top: 2px;
    font-size: 11px;
    line-height: 15px;
    color: var(--blue-warning-text);
    max-width: 180px;
  }
  .message {
    color: var(--blue-text-muted);
  }
  tr.failed .message {
    color: var(--blue-danger-text);
  }
  .actions {
    text-align: right;
    white-space: nowrap;
  }
  .actions .btn {
    min-width: 64px;
  }
  .btn.quiet {
    border-color: var(--blue-border-strong);
  }
  .empty-state {
    text-align: center;
    padding: var(--blue-space-6) var(--blue-space-5);
  }
  .empty-state .link {
    margin-left: var(--blue-space-2);
    font-size: 13px;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  @media (max-width: 640px) {
    .head,
    .progress-block,
    .tiles,
    .toolbar,
    .chips {
      padding-left: var(--blue-space-4);
      padding-right: var(--blue-space-4);
    }
    .export {
      align-items: flex-start;
      text-align: left;
    }
    .tiles {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .tile-hint {
      display: none;
    }
  }
</style>
