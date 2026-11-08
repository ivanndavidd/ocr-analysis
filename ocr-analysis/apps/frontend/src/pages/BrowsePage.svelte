<script lang="ts">
  import type { ClaudeState, FolderListing, FolderMark, Health, Job, LatestResult, ReadMark, ReadMode } from '@contract-ocr/shared-types';
  import { onDestroy, onMount } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import Badge from '../components/Badge.svelte';
  import Banner from '../components/Banner.svelte';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import ContractCheck from '../components/ContractCheck.svelte';
  import PdfPreview from '../components/PdfPreview.svelte';
  import { ApiFailure, api, failureCode } from '../services/api.ts';
  import { saveBlob } from '../utils/download.ts';
  import { MARK_LABEL, folderDone, folderMarkTitle, folderPart, isActive, jobTitle, markTitle, markWhen, runState } from '../utils/jobs.ts';
  import { errorLabel } from '../utils/labels.ts';
  import {
    type SortKey,
    loadSort,
    matches,
    nextSort,
    saveSort,
    selectionLabel,
    sortFiles,
    sortFolders,
  } from '../utils/listing.ts';
  import {
    CLAUDE_HINT,
    FLOW_LABEL,
    FULL_PAGE_DESCRIPTION,
    FULL_PAGE_NOTICE,
    FULL_PAGE_FLOW,
    MODES,
    MODE_LABEL,
    claudeConsent,
    claudeNotice,
    flowDescription,
    fullPageBlocked,
    fullPageConsent,
    loadMode,
    saveMode,
  } from '../utils/claude.ts';
  import { filePath, folderHref, jobHref } from '../utils/routes.ts';

  let {
    path,
    rootName,
    extractor,
    claude,
  }: { path: string; rootName: string; extractor: Health['extractor'] | null; claude: ClaudeState | null } = $props();

  let listing = $state<FolderListing | null>(null);
  let loadError = $state<string | null>(null);
  let mine = $state<Job | null>(null); // the user's own run while it is queued or running (one at a time)
  let active = $state(0); // runs queued or running, everyone's: a new run starts after them
  let starting = $state(false);
  let confirming = $state(false); // the run is asked once more before it starts
  let startError = $state<{ code: string; jobId?: string } | null>(null);
  // Cleared when the user opens another folder (the page is rebuilt), but kept while searching.
  const selectedFolders = new SvelteSet<string>(); // folder paths
  const selectedFiles = new SvelteSet<string>(); // PDF names in this folder
  let selectAll = $state<HTMLInputElement | null>(null);
  let query = $state('');
  let sort = $state(loadSort());
  let preview = $state<string | null>(null); // root-relative path of the PDF shown over the page
  let previewNote = $state<string | null>(null);
  let latest = $state<LatestResult | null>(null); // a PDF shown with its newest reading in any user's run
  let opening = $state<string | null>(null); // the PDF whose reading is being looked up
  let marks = $state<Record<string, ReadMark>>({}); // this folder's PDFs that some run has read, by path
  let folderMarks = $state<Record<string, FolderMark>>({}); // how far the runs have read each subfolder, by path
  // "Unduh Excel": the selection's newest readings, after a run of the PDFs no run has read yet
  type Download =
    | { step: 'checking' | 'saving' }
    | { step: 'confirm' | 'starting'; unread: number; total: number }
    | { step: 'blocked'; unread: number; total: number; reason: string }
    | { step: 'reading'; job: Job; total: number }
    | { step: 'stopped'; job: Job } // the run did not finish: cancelled, or the backend stopped
    | { step: 'done'; name: string; unread: number }
    | { step: 'error'; code: string; jobId?: string };
  let download = $state<Download | null>(null);
  let picked: { folders: string[]; files: string[] } = { folders: [], files: [] }; // what the download is for
  let alive = true; // a run being followed stops being polled once the page goes
  onDestroy(() => (alive = false));
  const downloading = $derived(
    download !== null && ['checking', 'saving', 'starting', 'reading'].includes(download.step),
  );
  let mode = $state<ReadMode>(loadMode()); // kept for this tab, so it holds while moving between folders
  const fullPage = $derived(mode === 'full_page');
  const flow = $derived(fullPage ? FULL_PAGE_FLOW : FLOW_LABEL[claude ?? 'off']);

  onMount(async () => {
    try {
      const [folder, current] = await Promise.all([api.folders(path), api.currentJob()]);
      listing = folder;
      mine = current.job;
      active = current.active;
      loadMarks();
    } catch (error) {
      loadError = failureCode(error);
    }
  });

  // asked again when the run is about to be confirmed: other users may have started or finished runs meanwhile
  function refreshQueue() {
    api.currentJob().then(
      (current) => {
        mine = current.job;
        active = current.active;
      },
      () => {},
    );
  }

  const folders = $derived(sortFolders(listing?.folders.filter((f) => matches(f.name, query)) ?? [], sort));
  const files = $derived(sortFiles(listing?.pdfs.filter((name) => matches(name, query)) ?? [], sort));
  const searching = $derived(query.trim() !== '');
  const itemCount = $derived((listing?.folders.length ?? 0) + (listing?.pdfs.length ?? 0));

  // Select all works on what the search shows; anything ticked before stays ticked.
  const visibleFolders = $derived(folders.filter((f) => f.pdfCount > 0));
  const visibleCount = $derived(visibleFolders.length + files.length);
  const allSelected = $derived(
    visibleCount > 0 && visibleFolders.every((f) => selectedFolders.has(f.path)) && files.every((n) => selectedFiles.has(n)),
  );
  const someSelected = $derived(
    visibleFolders.some((f) => selectedFolders.has(f.path)) || files.some((n) => selectedFiles.has(n)),
  );
  const selectedCount = $derived(selectedFolders.size + selectedFiles.size);

  const currentTotal = $derived(
    (listing?.pdfs.length ?? 0) + (listing?.folders.reduce((sum, f) => sum + f.pdfCount, 0) ?? 0),
  );
  const selectedTotal = $derived(
    (listing?.folders.filter((f) => selectedFolders.has(f.path)).reduce((sum, f) => sum + f.pdfCount, 0) ?? 0) +
      selectedFiles.size,
  );
  const runTotal = $derived(selectedCount > 0 ? selectedTotal : currentTotal);
  // The confirmations' one-line warning; the full wording sits under their "Detail".
  const notice = $derived(fullPage ? FULL_PAGE_NOTICE : claudeNotice(claude));

  $effect(() => {
    if (selectAll) selectAll.indeterminate = someSelected && !allSelected;
  });

  function toggle(set: SvelteSet<string>, key: string) {
    if (set.has(key)) set.delete(key);
    else set.add(key);
  }

  function toggleAll() {
    const select = !allSelected;
    for (const folder of visibleFolders) {
      if (select) selectedFolders.add(folder.path);
      else selectedFolders.delete(folder.path);
    }
    for (const name of files) {
      if (select) selectedFiles.add(name);
      else selectedFiles.delete(name);
    }
  }

  function clearSelection() {
    selectedFolders.clear();
    selectedFiles.clear();
  }

  function sortBy(key: SortKey) {
    sort = nextSort(sort, key);
    saveSort(sort);
  }

  // A PDF some run has read opens with that reading next to it (the newest, whoever ran it); any other opens alone.
  async function openPdf(target: string) {
    if (opening) return;
    opening = target;
    let note: string | null = null;
    try {
      const { result } = await api.latestResult(target);
      if (result) {
        latest = result;
        return;
      }
      note = 'Belum ada hasil OCR untuk PDF ini.';
    } catch {
      // the lookup failed: the PDF still opens, without its reading
    } finally {
      opening = null;
    }
    previewNote = note;
    preview = target;
  }

  function onKeydown(e: KeyboardEvent) {
    // An Escape that closes the preview must not clear the selection too; Chromium closes the dialog before the
    // keydown reaches the window, so `preview` is already null by then.
    const fromDialog = e.target instanceof Element && e.target.closest('dialog') !== null;
    if (e.key === 'Escape' && !preview && !latest && !fromDialog && selectedCount > 0) clearSelection();
  }

  function onSearchKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && query) {
      query = '';
      e.stopPropagation(); // the first Escape clears the search, not the selection
    }
  }

  // the marks come after the list: without them the list still works
  function loadMarks() {
    if (listing?.pdfs.length) {
      api.marks(listing.pdfs.map((name) => filePath(path, name))).then(
        (found) => (marks = found.marks),
        () => {},
      );
    }
    if (listing?.folders.some((folder) => folder.pdfCount > 0)) {
      api.folderMarks(path).then(
        (found) => (folderMarks = found.marks),
        () => {},
      );
    }
  }

  /** What a run or a download is for: the ticked folders and PDFs in the order the user sees (ticked items hidden by
   *  the search included), or this whole folder when nothing is ticked. */
  function selection(): { folders: string[]; files: string[] } {
    if (!listing || selectedCount === 0) return { folders: [path], files: [] };
    return {
      folders: sortFolders(listing.folders, sort).filter((f) => selectedFolders.has(f.path)).map((f) => f.path),
      files: sortFiles(listing.pdfs, sort).filter((n) => selectedFiles.has(n)).map((n) => filePath(path, n)),
    };
  }

  // Unduh Excel: straight away when every PDF has a reading (anyone's, either read mode); otherwise asks to read the
  // unread ones first as a run of their own, waits for it here, then downloads.
  async function startDownload() {
    if (!listing || downloading) return;
    picked = selection();
    download = { step: 'checking' };
    try {
      await save(false, 0);
    } catch (error) {
      if (!(error instanceof ApiFailure && error.code === 'not_all_read' && error.counts)) {
        download = { step: 'error', code: failureCode(error) };
        return;
      }
      const { unread, total } = error.counts;
      const reason = mine
        ? 'Proses OCR kamu masih berjalan: tunggu selesai, lalu unduh lagi.'
        : extractor?.state === 'unavailable'
          ? 'Pembaca OCR tidak tersedia.'
          : fullPage
            ? fullPageBlocked(claude)
            : null;
      download = reason ? { step: 'blocked', unread, total, reason } : { step: 'confirm', unread, total };
      if (!reason) refreshQueue();
    }
  }

  async function readThenDownload() {
    if (download?.step !== 'confirm') return;
    const { unread, total } = download;
    download = { step: 'starting', unread, total };
    try {
      let job: Job | null = null;
      try {
        job = await api.startJob({ ...picked, mode, unreadOnly: true });
      } catch (error) {
        if (!(error instanceof ApiFailure && error.code === 'nothing_to_read')) throw error; // read meanwhile
      }
      if (job) {
        mine = job;
        download = { step: 'reading', job, total };
        job = await follow(job.id);
        if (!job) return; // the page went; the run goes on
        if (job.status !== 'done') {
          download = { step: 'stopped', job };
          return;
        }
      }
      await save(true, job ? job.files.filter((f) => f.state !== 'done').length : 0);
    } catch (error) {
      download = { step: 'error', code: failureCode(error), jobId: error instanceof ApiFailure ? error.jobId : undefined };
    }
  }

  /** Polls the run until it stops; null if the page went first. */
  async function follow(id: string): Promise<Job | null> {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (!alive) return null;
      const job = await api.job(id);
      if (!alive) return null;
      mine = isActive(job) ? job : null;
      if (download?.step === 'reading') download = { ...download, job };
      if (!isActive(job)) return job;
    }
  }

  async function save(includeUnread: boolean, unread: number) {
    download = { step: includeUnread ? 'saving' : 'checking' };
    const { blob, name } = await api.exportLatest(picked, includeUnread);
    saveBlob(blob, name);
    download = { step: 'done', name, unread };
    loadMarks(); // PDFs this page read are marked now
  }

  async function saveAnyway() {
    try {
      await save(true, download?.step === 'blocked' ? download.unread : 0);
    } catch (error) {
      download = { step: 'error', code: failureCode(error) };
    }
  }

  async function start() {
    if (!listing) return;
    starting = true;
    startError = null;
    try {
      const job = await api.startJob({ ...selection(), mode });
      location.hash = jobHref(job.id);
    } catch (error) {
      startError = { code: failureCode(error), jobId: error instanceof ApiFailure ? error.jobId : undefined };
      confirming = false;
    } finally {
      starting = false;
    }
  }

  const title = $derived(listing?.crumbs.at(-1)?.name ?? rootName);
  const breadcrumbText = $derived(listing?.crumbs.length ? listing.crumbs.map((c) => c.name).join(' / ') : rootName);
  const mineDone = $derived(mine ? mine.files.filter((f) => f.state !== 'waiting' && f.state !== 'reading').length : 0);
  const blockedReason = $derived(
    mine
      ? 'Tunggu proses kamu selesai: satu orang satu proses sekaligus.'
      : extractor?.state === 'unavailable'
        ? 'Pembaca OCR tidak tersedia.'
        : runTotal === 0
          ? 'Folder ini tidak berisi PDF.'
          : fullPage
            ? fullPageBlocked(claude)
            : null,
  );
  const selected = $derived(selectionLabel(selectedFolders.size, selectedFiles.size));
  const scope = $derived(
    selectedCount > 0
      ? `${selected} dipilih`
      : searching && itemCount > 0
        ? 'Semua PDF di folder ini, bukan hanya hasil pencarian'
        : listing && listing.folders.length > 0
          ? 'Semua PDF di folder ini, termasuk subfolder'
          : null,
  );
  const sortLabel = (key: SortKey) =>
    sort.key !== key ? '' : key === 'name' ? (sort.dir === 'asc' ? 'A ke Z' : 'Z ke A') : sort.dir === 'asc' ? 'sedikit ke banyak' : 'banyak ke sedikit';
</script>

<svelte:window onkeydown={onKeydown} />

{#if mine}
  <Banner>
    Proses OCR kamu untuk <strong>{jobTitle(mine, rootName)}</strong> {runState(mine)} ({mineDone}/{mine.files.length} file).
    <a href={jobHref(mine.id)}>Lihat progres</a>
  </Banner>
{/if}

<nav class="crumbs" aria-label="Lokasi folder">
  {#if listing && listing.crumbs.length > 0}
    <a href={folderHref('')}>
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path fill="currentColor" d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
      {rootName}
    </a>
    {#each listing.crumbs as crumb, i (crumb.path)}
      <svg class="sep" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path fill="none" stroke="currentColor" stroke-width="2" d="m9 6 6 6-6 6" />
      </svg>
      {#if i === listing.crumbs.length - 1}
        <span aria-current="page">{crumb.name}</span>
      {:else}
        <a href={folderHref(crumb.path)}>{crumb.name}</a>
      {/if}
    {/each}
  {:else}
    <span aria-current="page">{rootName}</span>
  {/if}
</nav>

<section class="card">
  <div class="head">
    <div>
      <h1>{title}</h1>
      {#if listing}
        <p class="muted">{listing.folders.length} folder · {listing.pdfs.length} PDF di folder ini</p>
      {/if}
    </div>
    {#if listing}
      <div class="run">
        <fieldset class="modes" disabled={starting}>
          <legend class="muted">Cara baca</legend>
          <div class="segments">
            {#each MODES as id (id)}
              <label class="segment" class:active={mode === id}>
                <input type="radio" name="read-mode" value={id} bind:group={mode} onchange={() => saveMode(id)} />
                {MODE_LABEL[id]}
              </label>
            {/each}
          </div>
        </fieldset>
        <span class="flow" class:risky={fullPage}>{flow}</span>
        <div class="actions">
          <button
            class="btn btn-outline"
            disabled={downloading || runTotal === 0}
            title="Excel dari hasil OCR terakhir tiap PDF; PDF yang belum di-OCR dibaca dulu"
            onclick={startDownload}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 4v11m-5-5 5 5 5-5M5 20h14" />
            </svg>
            Unduh Excel ({runTotal})
          </button>
          <button
            class="btn btn-primary"
            disabled={starting || blockedReason !== null}
            onclick={() => {
              startError = null;
              confirming = true;
              refreshQueue();
            }}
          >
            {fullPage ? 'Jalankan tanpa OCR' : 'Jalankan OCR'} ({runTotal})
          </button>
        </div>
        <span class="hint muted">{blockedReason ?? scope ?? ''}</span>
      </div>
    {/if}
  </div>

  {#if download && download.step !== 'confirm'}
    <div class="pad">
      {#if download.step === 'checking'}
        <Banner>Menyiapkan Excel…</Banner>
      {:else if download.step === 'starting'}
        <Banner>Memulai OCR untuk {download.unread} PDF yang belum di-OCR…</Banner>
      {:else if download.step === 'reading'}
        {@const job = download.job}
        {@const done = job.files.filter((f) => f.state === 'done' || f.state === 'error').length}
        <Banner>
          <strong>Membaca {job.files.length} PDF yang belum di-OCR</strong>: {runState(job)}, {done}/{job.files.length} selesai.
          <a href={jobHref(job.id)}>Lihat proses</a><br />
          <span class="muted">
            Excel berisi {download.total} PDF diunduh otomatis setelah selesai. Tetap di halaman ini; bila ditinggal, proses
            tetap berjalan dan Unduh Excel bisa ditekan lagi nanti.
          </span>
        </Banner>
      {:else if download.step === 'saving'}
        <Banner>Membuat Excel…</Banner>
      {:else if download.step === 'done'}
        <Banner>
          Excel diunduh: <strong>{download.name}</strong>.
          {#if download.unread}{download.unread} PDF belum terbaca; barisnya kosong dan berwarna kuning.{/if}
          <button class="link" onclick={() => (download = null)}>Tutup</button>
        </Banner>
      {:else if download.step === 'blocked'}
        <Banner tone="warning">
          {download.unread} dari {download.total} PDF belum di-OCR. {download.reason}
          <button class="link" onclick={saveAnyway}>Unduh yang sudah ada saja</button>
          · <button class="link" onclick={() => (download = null)}>Tutup</button>
        </Banner>
      {:else if download.step === 'stopped'}
        <Banner tone="warning">
          Proses untuk PDF yang belum di-OCR {runState(download.job)}, jadi Excel belum diunduh.
          <a href={jobHref(download.job.id)}>Lihat proses</a> · <button class="link" onclick={saveAnyway}>Unduh yang sudah ada</button>
          · <button class="link" onclick={() => (download = null)}>Tutup</button>
        </Banner>
      {:else if download.step === 'error'}
        <Banner tone="danger">
          {errorLabel(download.code)}.
          {#if download.jobId}<a href={jobHref(download.jobId)}>Lihat proses kamu</a>{/if}
          <button class="link" onclick={() => (download = null)}>Tutup</button>
        </Banner>
      {/if}
    </div>
  {/if}

  {#if startError}
    <div class="pad">
      <Banner tone="danger">
        {errorLabel(startError.code)}.
        {#if startError.jobId}<a href={jobHref(startError.jobId)}>Lihat proses kamu</a>{/if}
      </Banner>
    </div>
  {/if}

  {#if loadError}
    <div class="pad">
      <Banner tone="danger">{errorLabel(loadError)}. <a href={folderHref('')}>Kembali ke {rootName}</a></Banner>
    </div>
  {:else if !listing}
    <p class="empty muted">Memuat folder…</p>
  {:else if itemCount === 0}
    <p class="empty muted">Folder ini kosong.</p>
  {:else}
    <div class="toolbar">
      <label class="search">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2" />
          <path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="m20 20-3.5-3.5" />
        </svg>
        <input
          type="search"
          bind:value={query}
          onkeydown={onSearchKeydown}
          placeholder="Cari folder atau PDF di sini"
          aria-label="Cari folder atau PDF di folder ini"
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      {#if searching}
        <span class="muted found" role="status">{folders.length + files.length} dari {itemCount}</span>
      {/if}
    </div>

    {#if selectedCount > 0}
      <div class="selection" role="status">
        <button class="clear" aria-label="Batal pilih" title="Batal pilih (Esc)" onclick={clearSelection}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="2" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
        <strong>{selected} dipilih</strong>
        {#if selectedFolders.size > 0}<span class="muted">· {selectedTotal} PDF</span>{/if}
      </div>
    {/if}

    <div class="list-head">
      <span class="check-cell">
        <input
          type="checkbox"
          class="check"
          bind:this={selectAll}
          checked={allSelected}
          disabled={visibleCount === 0}
          onchange={toggleAll}
          aria-label={searching ? 'Pilih semua hasil pencarian' : 'Pilih semua'}
        />
      </span>
      <button class="sort col-name" class:active={sort.key === 'name'} onclick={() => sortBy('name')}>
        Nama
        {@render arrow('name')}
        <span class="sr-only">{sortLabel('name') ? `, urut ${sortLabel('name')}` : ', urutkan'}</span>
      </button>
      {#if listing.folders.length > 0}
        <!-- only folders have a count; a folder of PDFs alone has nothing to sort by it -->
        <button class="sort col-count" class:active={sort.key === 'count'} onclick={() => sortBy('count')}>
          {@render arrow('count')}
          PDF
          <span class="sr-only">{sortLabel('count') ? `, urut ${sortLabel('count')}` : ', urutkan'}</span>
        </button>
      {/if}
    </div>

    {#if folders.length + files.length === 0}
      <p class="empty muted">
        Tidak ada folder atau PDF yang cocok dengan “{query.trim()}”.
        <button class="link" onclick={() => (query = '')}>Hapus pencarian</button>
      </p>
    {:else}
      <ul class="list" aria-label="Isi folder">
        {#each folders as folder (folder.path)}
          {@const isSelected = selectedFolders.has(folder.path)}
          {@const mark = folderMarks[folder.path]}
          <li class="row" class:selected={isSelected}>
            <span class="check-cell">
              <input
                type="checkbox"
                class="check"
                checked={isSelected}
                disabled={folder.pdfCount === 0}
                onchange={() => toggle(selectedFolders, folder.path)}
                aria-label="Pilih folder {folder.name}"
              />
            </span>
            <a class="open" href={folderHref(folder.path)}>
              <svg class="icon folder" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              </svg>
              <span class="name">{folder.name}</span>
              {#if mark && mark.read > 0}
                <span class="mark" title={folderMarkTitle(mark)}>
                  {#if folderDone(mark)}
                    <Badge tone="brand">{MARK_LABEL}</Badge>
                  {:else}
                    <span class="mark-part">{folderPart(mark)}</span>
                  {/if}
                </span>
              {/if}
              <span class="count" class:zero={folder.pdfCount === 0}>{folder.pdfCount}</span>
              <svg class="icon trail" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path fill="none" stroke="currentColor" stroke-width="2" d="m9 6 6 6-6 6" />
              </svg>
            </a>
          </li>
        {/each}
        {#each files as name (name)}
          {@const isSelected = selectedFiles.has(name)}
          {@const mark = marks[filePath(path, name)]}
          <li class="row" class:selected={isSelected}>
            <span class="check-cell">
              <input
                type="checkbox"
                class="check"
                checked={isSelected}
                onchange={() => toggle(selectedFiles, name)}
                aria-label="Pilih {name}"
              />
            </span>
            <button
              class="open"
              title="Lihat PDF dan hasil OCR terakhirnya"
              aria-busy={opening === filePath(path, name)}
              onclick={() => openPdf(filePath(path, name))}
            >
              <svg class="icon pdf" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V9h5.5z" />
              </svg>
              <span class="name">{name}</span>
              {#if mark}
                <span class="mark" title={markTitle(mark)}>
                  <span class="mark-when">{markWhen(mark)}</span>
                  <Badge tone="brand">{MARK_LABEL}</Badge>
                </span>
              {/if}
              <svg class="icon trail" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path fill="none" stroke="currentColor" stroke-width="2" d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
                <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2" />
              </svg>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>

<PdfPreview path={preview} note={previewNote} onclose={() => (preview = null)} />
<ContractCheck
  file={latest?.file ?? null}
  nik={latest?.nik ?? null}
  run={latest?.run ?? null}
  position={null}
  onprev={null}
  onnext={null}
  onclose={() => (latest = null)}
/>

<ConfirmDialog
  open={confirming && blockedReason === null}
  title={fullPage ? `Kirim ${runTotal} PDF ke Claude tanpa OCR?` : `Jalankan OCR untuk ${runTotal} PDF?`}
  confirmLabel={starting ? 'Memulai…' : fullPage ? 'Ya, kirim halaman utuh' : 'Ya, jalankan'}
  busy={starting}
  onconfirm={start}
  oncancel={() => (confirming = false)}
>
  <dl class="summary">
    <dt>Folder</dt>
    <dd>{breadcrumbText}</dd>
    <dt>Yang dibaca</dt>
    <dd>{scope ?? 'Semua PDF di folder ini'}</dd>
    <dt>Cara baca</dt>
    <dd><strong>{MODE_LABEL[fullPage ? 'full_page' : 'ocr']}: {flow}</strong></dd>
  </dl>
  {@render warnings()}
  {@render detail(runTotal, ['Proses bisa dibatalkan dari halaman progres.'])}
</ConfirmDialog>

<ConfirmDialog
  open={download?.step === 'confirm' || download?.step === 'starting'}
  title={download && 'unread' in download ? `${download.unread} dari ${download.total} PDF belum di-OCR` : ''}
  confirmLabel={download?.step === 'starting' ? 'Memulai…' : 'Ya, jalankan OCR lalu unduh'}
  busy={download?.step === 'starting'}
  onconfirm={readThenDownload}
  oncancel={() => (download = null)}
>
  {#if download && 'unread' in download}
    <p>
      {download.unread} PDF itu dibaca dulu sebagai proses baru, lalu Excel {download.total} PDF terunduh otomatis. Tetap di
      halaman ini sampai selesai.
    </p>
    <dl class="summary">
      <dt>Folder</dt>
      <dd>{breadcrumbText}</dd>
      <dt>Yang diunduh</dt>
      <dd>{scope ?? 'Semua PDF di folder ini'}</dd>
      <dt>Cara baca</dt>
      <dd><strong>{MODE_LABEL[fullPage ? 'full_page' : 'ocr']}: {flow}</strong></dd>
    </dl>
    {@render warnings()}
    {@render detail(download.unread, [
      'Proses baru itu atas nama kamu. Excel berisi hasil OCR terakhir tiap PDF, siapa pun yang menjalankannya.',
      'Bila halaman ini ditinggal, proses tetap berjalan dan Unduh Excel bisa ditekan lagi nanti.',
    ])}
  {/if}
</ConfirmDialog>

<!-- What the two run confirmations always show: the data warning and the queue, one line each. -->
{#snippet warnings()}
  {#if notice}<Banner tone={fullPage ? 'danger' : 'warning'}>{notice}</Banner>{/if}
  {#if active > 0}<Banner>Ada {active} proses lain di antrean; proses ini mulai setelahnya.</Banner>{/if}
{/snippet}

<!-- The long explanation, folded away until opened: how the PDFs are read, exactly what goes to Claude, and the rest. -->
{#snippet detail(pdfs: number, more: string[])}
  <details class="more">
    <summary>{notice ? 'Detail cara baca dan data yang dikirim' : 'Detail cara baca'}</summary>
    <div class="more-body">
      {#if fullPage}
        <p>{FULL_PAGE_DESCRIPTION}</p>
        <p>{fullPageConsent(pdfs)}</p>
      {:else}
        {@const ask = claudeConsent(claude, pdfs)}
        <p>{flowDescription(claude)}</p>
        {#if ask}<p>{ask}</p>{/if}
        {#if claude && CLAUDE_HINT[claude]}<p>{CLAUDE_HINT[claude]}</p>{/if}
      {/if}
      {#if active > 0}<p>PDF yang juga ada di proses lain di antrean tidak dibaca dua kali: hasilnya dipakai di sini.</p>{/if}
      {#each more as line (line)}<p>{line}</p>{/each}
    </div>
  </details>
{/snippet}

{#snippet arrow(key: SortKey)}
  <svg class="arrow" class:desc={sort.key === key && sort.dir === 'desc'} viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
    <path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M12 19V5m-6 6 6-6 6 6" />
  </svg>
{/snippet}

<style>
  .crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-1);
    font-size: 16px;
  }
  .crumbs a,
  .crumbs [aria-current] {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 36px;
    padding: 0 var(--blue-space-2);
    border-radius: var(--blue-radius-sm);
    overflow-wrap: anywhere;
  }
  .crumbs > :first-child {
    margin-left: calc(-1 * var(--blue-space-2)); /* the text lines up with the card below */
  }
  .crumbs a {
    color: var(--blue-link);
    font-weight: 600;
  }
  .crumbs a:hover {
    background: var(--blue-brand-subtle);
    text-decoration: none;
  }
  .crumbs [aria-current] {
    color: var(--blue-text);
    font-weight: 700;
  }
  .sep {
    flex: none;
    color: var(--blue-text-muted);
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--blue-space-4);
    padding: var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    overflow-wrap: anywhere;
  }
  .run {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--blue-space-1);
  }
  .hint {
    font-size: 12px;
    min-height: 18px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--blue-space-2);
  }
  .actions .btn {
    gap: var(--blue-space-2);
  }
  .flow {
    font-size: 13px;
  }
  .flow.risky {
    color: var(--blue-danger-text);
    font-weight: 600;
  }
  .modes {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    margin: 0;
    padding: 0;
    border: 0;
  }
  .modes legend {
    float: left; /* beside the switch, not above it */
    font-size: 13px;
    padding: 0;
  }
  .segments {
    display: inline-flex;
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    overflow: hidden;
  }
  .segment {
    position: relative;
    padding: var(--blue-space-1) var(--blue-space-3);
    font-size: 13px;
    cursor: pointer;
    color: var(--blue-text);
    background: var(--blue-surface);
  }
  .segment + .segment {
    border-left: 1px solid var(--blue-border-strong);
  }
  .segment input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
  .segment.active {
    background: var(--blue-brand-subtle);
    color: var(--blue-brand);
    font-weight: 600;
  }
  .segment:has(input:focus-visible) {
    outline: 2px solid var(--blue-brand);
    outline-offset: -2px;
  }
  .modes:disabled .segment {
    cursor: default;
    opacity: 0.6;
  }
  .pad {
    padding: var(--blue-space-4) var(--blue-space-5) 0;
  }
  .summary {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--blue-space-2) var(--blue-space-4);
    margin: 0;
  }
  .summary dt {
    color: var(--blue-text-muted);
  }
  .summary dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .more summary {
    width: fit-content;
    cursor: pointer;
    color: var(--blue-link);
    font-size: 13px;
    font-weight: 600;
  }
  .more-body {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-2);
    margin-top: var(--blue-space-2);
    padding: var(--blue-space-3);
    border-radius: var(--blue-radius);
    background: var(--blue-surface-muted);
    color: var(--blue-text-muted);
    font-size: 13px;
  }
  .more-body p {
    margin: 0;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    padding: var(--blue-space-3) var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
  }
  .search {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    flex: 0 1 420px;
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
  .found {
    font-size: 13px;
    white-space: nowrap;
  }
  .selection {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    padding: var(--blue-space-2) var(--blue-space-5) var(--blue-space-2) var(--blue-space-3);
    background: var(--blue-brand-subtle);
    border-bottom: 1px solid var(--blue-border);
  }
  .clear {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--blue-text);
    cursor: pointer;
  }
  .clear:hover {
    background: rgba(0, 149, 218, 0.12);
  }
  .list-head {
    display: flex;
    align-items: center;
    min-height: 40px;
    padding-right: var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
    font-size: 12px;
    font-weight: 600;
    color: var(--blue-text-muted);
  }
  .sort {
    display: inline-flex;
    align-items: center;
    gap: var(--blue-space-1);
    min-height: 32px;
    padding: 0 var(--blue-space-1);
    border: none;
    border-radius: var(--blue-radius-sm);
    background: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .sort:hover {
    color: var(--blue-text);
    background: var(--blue-surface-muted);
  }
  .sort.active {
    color: var(--blue-text);
  }
  .arrow {
    opacity: 0;
    transition: transform 0.15s;
  }
  .sort:hover .arrow {
    opacity: 0.4;
  }
  .sort.active .arrow {
    opacity: 1;
  }
  .arrow.desc {
    transform: rotate(180deg);
  }
  .col-name {
    margin-right: auto;
    margin-left: calc(20px + var(--blue-space-3) - var(--blue-space-1));
  }
  .col-count {
    justify-content: flex-end;
    width: 64px;
    margin-right: calc(16px + var(--blue-space-3) - var(--blue-space-1));
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .row {
    display: flex;
    align-items: center;
    min-height: 52px;
    border-bottom: 1px solid var(--blue-border);
  }
  .row:last-child {
    border-bottom: none;
  }
  .row:hover,
  .row.selected {
    background: var(--blue-brand-subtle);
  }
  .check-cell {
    display: grid;
    place-items: center;
    flex: none;
    width: 56px;
    align-self: stretch;
  }
  /* Round check like OneDrive: faint until the row is hovered, solid once selected. */
  .check {
    appearance: none;
    width: 20px;
    height: 20px;
    margin: 0;
    border: 1.5px solid var(--blue-border-strong);
    border-radius: 50%;
    background: var(--blue-surface) center / 12px no-repeat;
    cursor: pointer;
    transition: border-color 0.1s;
  }
  .row .check:not(:checked):not(:focus-visible) {
    opacity: 0.55;
  }
  .row:hover .check {
    opacity: 1;
  }
  .check:hover:not(:disabled) {
    border-color: var(--blue-brand);
  }
  .check:checked,
  .check:indeterminate {
    border-color: var(--blue-brand);
    background-color: var(--blue-brand);
  }
  .check:checked {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 6.2 5 8.5l4.5-5' fill='none' stroke='white' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
  .check:indeterminate {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M3 6h6' stroke='white' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E");
  }
  .check:disabled {
    cursor: not-allowed;
    opacity: 0.25 !important;
  }
  .open {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--blue-space-3);
    align-self: stretch;
    padding: var(--blue-space-2) var(--blue-space-5) var(--blue-space-2) 0;
    border: none;
    background: none;
    color: var(--blue-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .open[aria-busy='true'] {
    cursor: progress;
  }
  .open:hover {
    text-decoration: none;
  }
  .open:hover .name {
    color: var(--blue-link);
    text-decoration: underline;
  }
  .name {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .mark {
    flex: none;
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
  }
  .mark-when,
  .mark-part {
    font-size: 12px;
    color: var(--blue-text-muted);
    white-space: nowrap;
  }
  .count {
    width: 64px;
    text-align: right;
    font-size: 13px;
    font-weight: 600;
    color: var(--blue-text);
  }
  .count.zero {
    color: var(--blue-text-muted);
    font-weight: 400;
  }
  .icon {
    flex: none;
  }
  .folder {
    color: var(--blue-brand);
  }
  .pdf {
    color: var(--blue-danger);
  }
  .trail {
    color: var(--blue-text-muted);
  }
  .open:hover .trail {
    color: var(--blue-link);
  }
  .empty {
    padding: var(--blue-space-5);
  }
  .link {
    padding: 0;
    border: none;
    background: none;
    color: var(--blue-link);
    font: inherit;
    cursor: pointer;
  }
  .link:hover {
    text-decoration: underline;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  @media (max-width: 640px) {
    .mark-when {
      display: none;
    }
    .head,
    .toolbar {
      padding-left: var(--blue-space-4);
      padding-right: var(--blue-space-4);
    }
    .search {
      flex: 1;
    }
    .open,
    .list-head {
      padding-right: var(--blue-space-4);
    }
    .check-cell {
      width: 48px;
    }
    .run {
      align-items: stretch;
      width: 100%;
    }
    .actions .btn {
      flex: 1;
    }
    .row .check:not(:checked) {
      opacity: 1; /* no hover on touch screens */
    }
  }
</style>
