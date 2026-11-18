<script lang="ts">
  import type { ClaudeState, FolderListing, FolderMark, Health, Job, LatestResult, ReadMark, StartMode } from '@contract-ocr/shared-types';
  import { onDestroy, onMount } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import Badge from '../components/Badge.svelte';
  import Banner from '../components/Banner.svelte';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import ContractCheck from '../components/ContractCheck.svelte';
  import PdfPreview from '../components/PdfPreview.svelte';
  import { ApiFailure, api, failureCode } from '../services/api.ts';
  import { saveBlob } from '../utils/download.ts';
  import { t } from '../utils/i18n.ts';
  import { folderDone, folderMarkTitle, folderPart, isActive, jobTitle, markLabel, markTitle, markWhen, runState } from '../utils/jobs.ts';
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
    MODES,
    claudeConsent,
    claudeHint,
    claudeNotice,
    flowDescription,
    flowLabel,
    loadMode,
    modeLabel,
    pageBlocked,
    pageConsent,
    pageDescription,
    pageFlow,
    pageNotice,
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
  let previewUnread = $state(false); // the PDF has no reading in any run: the preview says so
  let latest = $state<LatestResult | null>(null); // a PDF shown with its newest reading in any user's run
  let opening = $state<string | null>(null); // the PDF whose reading is being looked up
  let marks = $state<Record<string, ReadMark>>({}); // this folder's PDFs that some run has read, by path
  let folderMarks = $state<Record<string, FolderMark>>({}); // how far the runs have read each subfolder, by path
  // "Unduh Excel": the selection's newest readings, after a run of the PDFs no run has read yet
  type Blocked = 'mine' | 'unavailable' | 'claude_page'; // why the unread PDFs cannot be read now; worded at render
  type Download =
    | { step: 'checking' | 'saving' }
    | { step: 'confirm' | 'starting'; unread: number; total: number }
    | { step: 'blocked'; unread: number; total: number; reason: Blocked }
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
  let mode = $state<StartMode>(loadMode()); // kept for this tab, so it holds while moving between folders
  const page = $derived(mode === 'claude_page');
  const flow = $derived(page ? pageFlow() : flowLabel(claude ?? 'off'));

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
  const notice = $derived(page ? pageNotice() : claudeNotice(claude));

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
    let unread = false;
    try {
      const { result } = await api.latestResult(target);
      if (result) {
        latest = result;
        return;
      }
      unread = true;
    } catch {
      // the lookup failed: the PDF still opens, without its reading
    } finally {
      opening = null;
    }
    previewUnread = unread;
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
      const reason: Blocked | null = mine
        ? 'mine'
        : extractor?.state === 'unavailable'
          ? 'unavailable'
          : page && pageBlocked(claude)
            ? 'claude_page'
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
      ? t().browse.waitForMine
      : extractor?.state === 'unavailable'
        ? t().browse.readerUnavailable
        : runTotal === 0
          ? t().browse.noPdfs
          : page
            ? pageBlocked(claude)
            : null,
  );
  /** Why "Unduh Excel" cannot read the unread PDFs first, in the current language. */
  const blockedText = (reason: Blocked) =>
    reason === 'mine' ? t().browse.blockedMine : reason === 'unavailable' ? t().browse.readerUnavailable : pageBlocked(claude);
  const selected = $derived(selectionLabel(selectedFolders.size, selectedFiles.size));
  const scope = $derived(
    selectedCount > 0
      ? t().browse.selected(selected)
      : searching && itemCount > 0
        ? t().browse.everyPdfNotJustSearch
        : listing && listing.folders.length > 0
          ? t().browse.everyPdfWithSubfolders
          : null,
  );
  function sortLabel(key: SortKey): string {
    if (sort.key !== key) return '';
    const m = t().browse;
    if (key === 'name') return sort.dir === 'asc' ? m.sortNameAsc : m.sortNameDesc;
    return sort.dir === 'asc' ? m.sortCountAsc : m.sortCountDesc;
  }
  const sortHint = (key: SortKey) => (sortLabel(key) ? t().browse.sortedBy(sortLabel(key)) : t().browse.sortable);
</script>

<svelte:window onkeydown={onKeydown} />

{#if mine}
  <Banner>
    {t().browse.mineBefore} <strong>{jobTitle(mine, rootName)}</strong>{t().browse.mineAfter(runState(mine), mineDone, mine.files.length)}
    <a href={jobHref(mine.id)}>{t().common.viewProgress}</a>
  </Banner>
{/if}

<nav class="crumbs" aria-label={t().browse.location}>
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
        <p class="muted">{t().browse.counts(listing.folders.length, listing.pdfs.length)}</p>
      {/if}
    </div>
    {#if listing}
      <div class="run">
        <fieldset class="modes" disabled={starting}>
          <legend class="muted">{t().common.readMode}</legend>
          <div class="segments">
            {#each MODES as id (id)}
              <label class="segment" class:active={mode === id}>
                <input type="radio" name="read-mode" value={id} bind:group={mode} onchange={() => saveMode(id)} />
                {modeLabel(id)}
              </label>
            {/each}
          </div>
        </fieldset>
        <span class="flow">{flow}</span>
        <div class="actions">
          <button
            class="btn btn-outline"
            disabled={downloading || runTotal === 0}
            title={t().browse.downloadHint}
            onclick={startDownload}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 4v11m-5-5 5 5 5-5M5 20h14" />
            </svg>
            {t().browse.download(runTotal)}
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
            {t().browse.run(runTotal)}
          </button>
        </div>
        <span class="hint muted">{blockedReason ?? scope ?? ''}</span>
      </div>
    {/if}
  </div>

  {#if download && download.step !== 'confirm'}
    <div class="pad">
      {#if download.step === 'checking'}
        <Banner>{t().browse.preparing}</Banner>
      {:else if download.step === 'starting'}
        <Banner>{t().browse.startingUnread(download.unread)}</Banner>
      {:else if download.step === 'reading'}
        {@const job = download.job}
        {@const done = job.files.filter((f) => f.state === 'done' || f.state === 'error').length}
        <Banner>
          <strong>{t().browse.readingUnread(job.files.length)}</strong>{t().browse.readingState(runState(job), done, job.files.length)}
          <a href={jobHref(job.id)}>{t().common.viewRun}</a><br />
          <span class="muted">{t().browse.readingHint(download.total)}</span>
        </Banner>
      {:else if download.step === 'saving'}
        <Banner>{t().browse.creating}</Banner>
      {:else if download.step === 'done'}
        <Banner>
          {t().browse.downloaded} <strong>{download.name}</strong>.
          {#if download.unread}{t().browse.unreadRows(download.unread)}{/if}
          <button class="link" onclick={() => (download = null)}>{t().common.close}</button>
        </Banner>
      {:else if download.step === 'blocked'}
        <Banner tone="warning">
          {t().browse.unreadOf(download.unread, download.total)}. {blockedText(download.reason)}
          <button class="link" onclick={saveAnyway}>{t().browse.downloadExistingOnly}</button>
          · <button class="link" onclick={() => (download = null)}>{t().common.close}</button>
        </Banner>
      {:else if download.step === 'stopped'}
        <Banner tone="warning">
          {t().browse.stopped(runState(download.job))}
          <a href={jobHref(download.job.id)}>{t().common.viewRun}</a> · <button class="link" onclick={saveAnyway}>{t().browse.downloadExisting}</button>
          · <button class="link" onclick={() => (download = null)}>{t().common.close}</button>
        </Banner>
      {:else if download.step === 'error'}
        <Banner tone="danger">
          {errorLabel(download.code)}.
          {#if download.jobId}<a href={jobHref(download.jobId)}>{t().common.viewYourRun}</a>{/if}
          <button class="link" onclick={() => (download = null)}>{t().common.close}</button>
        </Banner>
      {/if}
    </div>
  {/if}

  {#if startError}
    <div class="pad">
      <Banner tone="danger">
        {errorLabel(startError.code)}.
        {#if startError.jobId}<a href={jobHref(startError.jobId)}>{t().common.viewYourRun}</a>{/if}
      </Banner>
    </div>
  {/if}

  {#if loadError}
    <div class="pad">
      <Banner tone="danger">{errorLabel(loadError)}. <a href={folderHref('')}>{t().common.backTo(rootName)}</a></Banner>
    </div>
  {:else if !listing}
    <p class="empty muted">{t().browse.loadingFolder}</p>
  {:else if itemCount === 0}
    <p class="empty muted">{t().browse.emptyFolder}</p>
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
          placeholder={t().browse.searchPlaceholder}
          aria-label={t().browse.searchLabel}
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      {#if searching}
        <span class="muted found" role="status">{t().browse.found(folders.length + files.length, itemCount)}</span>
      {/if}
    </div>

    {#if selectedCount > 0}
      <div class="selection" role="status">
        <button class="clear" aria-label={t().browse.clearSelection} title={t().browse.clearSelectionKey} onclick={clearSelection}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="2" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
        <strong>{t().browse.selected(selected)}</strong>
        {#if selectedFolders.size > 0}<span class="muted">· {t().listing.pdfs(selectedTotal)}</span>{/if}
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
          aria-label={searching ? t().browse.selectAllResults : t().browse.selectAll}
        />
      </span>
      <button class="sort col-name" class:active={sort.key === 'name'} onclick={() => sortBy('name')}>
        {t().browse.columnName}
        {@render arrow('name')}
        <span class="sr-only">{sortHint('name')}</span>
      </button>
      {#if listing.folders.length > 0}
        <!-- only folders have a count; a folder of PDFs alone has nothing to sort by it -->
        <button class="sort col-count" class:active={sort.key === 'count'} onclick={() => sortBy('count')}>
          {@render arrow('count')}
          {t().browse.columnPdfs}
          <span class="sr-only">{sortHint('count')}</span>
        </button>
      {/if}
    </div>

    {#if folders.length + files.length === 0}
      <p class="empty muted">
        {t().browse.noMatch(query.trim())}
        <button class="link" onclick={() => (query = '')}>{t().browse.clearSearch}</button>
      </p>
    {:else}
      <ul class="list" aria-label={t().browse.contents}>
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
                aria-label={t().browse.selectFolder(folder.name)}
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
                    <Badge tone="brand">{markLabel()}</Badge>
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
                aria-label={t().browse.selectFile(name)}
              />
            </span>
            <button
              class="open"
              title={t().browse.openPdf}
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
                  <Badge tone="brand">{markLabel()}</Badge>
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

<PdfPreview path={preview} note={previewUnread ? t().browse.noReadingYet : null} onclose={() => (preview = null)} />
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
  title={t().browse.confirmRun(runTotal)}
  confirmLabel={starting ? t().browse.starting : t().browse.yesRun}
  busy={starting}
  onconfirm={start}
  oncancel={() => (confirming = false)}
>
  <dl class="summary">
    <dt>{t().common.folder}</dt>
    <dd>{breadcrumbText}</dd>
    <dt>{t().browse.whatIsRead}</dt>
    <dd>{scope ?? t().browse.everyPdf}</dd>
    <dt>{t().common.readMode}</dt>
    <dd><strong>{modeLabel(mode)}: {flow}</strong></dd>
  </dl>
  {@render warnings()}
  {@render detail(runTotal, [t().browse.canCancel])}
</ConfirmDialog>

<ConfirmDialog
  open={download?.step === 'confirm' || download?.step === 'starting'}
  title={download && 'unread' in download ? t().browse.unreadOf(download.unread, download.total) : ''}
  confirmLabel={download?.step === 'starting' ? t().browse.starting : t().browse.yesReadThenDownload}
  busy={download?.step === 'starting'}
  onconfirm={readThenDownload}
  oncancel={() => (download = null)}
>
  {#if download && 'unread' in download}
    <p>{t().browse.readFirst(download.unread, download.total)}</p>
    <dl class="summary">
      <dt>{t().common.folder}</dt>
      <dd>{breadcrumbText}</dd>
      <dt>{t().browse.whatIsDownloaded}</dt>
      <dd>{scope ?? t().browse.everyPdf}</dd>
      <dt>{t().common.readMode}</dt>
      <dd><strong>{modeLabel(mode)}: {flow}</strong></dd>
    </dl>
    {@render warnings()}
    {@render detail(download.unread, [t().browse.newRunIsYours, t().browse.leaveAnyway])}
  {/if}
</ConfirmDialog>

<!-- What the two run confirmations always show: the data warning and the queue, one line each. -->
{#snippet warnings()}
  {#if notice}<Banner tone="warning">{notice}</Banner>{/if}
  {#if active > 0}<Banner>{t().browse.queueAhead(active)}</Banner>{/if}
{/snippet}

<!-- The long explanation, folded away until opened: how the PDFs are read, exactly what goes to Claude, and the rest. -->
{#snippet detail(pdfs: number, more: string[])}
  <details class="more">
    <summary>{notice ? t().browse.detailWithData : t().browse.detail}</summary>
    <div class="more-body">
      {#if page}
        <p>{pageDescription()}</p>
        <p>{pageConsent(pdfs)}</p>
      {:else}
        {@const ask = claudeConsent(claude, pdfs)}
        {@const hint = claudeHint(claude)}
        <p>{flowDescription(claude)}</p>
        {#if ask}<p>{ask}</p>{/if}
        {#if hint}<p>{hint}</p>{/if}
      {/if}
      {#if active > 0}<p>{t().browse.notReadTwice}</p>{/if}
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
