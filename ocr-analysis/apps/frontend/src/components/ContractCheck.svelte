<script lang="ts">
  import type { FieldKey, JobFile, LatestResult } from '@contract-ocr/shared-types';
  import { untrack } from 'svelte';
  import { api, failureCode } from '../services/api.ts';
  import { FIELDS, FIELD_LABEL, EXCEL_HEADER, displayValue, fieldPage, toCheck } from '../utils/fields.ts';
  import { formatNik, formatTokens, percent } from '../utils/format.ts';
  import { callsLabel } from '../utils/claude.ts';
  import { readBy } from '../utils/jobs.ts';
  import { DECISION, PAGE_KIND, errorLabel, reasonLabel } from '../utils/labels.ts';
  import { jobHref } from '../utils/routes.ts';

  // One contract's data next to its PDF, so a person compares the yellow values with the page without leaving the
  // list. Like PdfPreview, the PDF is fetched into memory and its name never reaches the address bar.

  let {
    file,
    nik,
    position,
    onprev,
    onnext,
    onclose,
    run = null,
  }: {
    file: JobFile | null; // open while set; always a file with a row
    nik: string | null; // the file's full NIK, once the results page has it
    position: { index: number; total: number } | null; // in the list behind the dialog
    onprev: (() => void) | null;
    onnext: (() => void) | null;
    onclose: () => void;
    run?: LatestResult['run'] | null; // opened from the folder page: the run whose reading this is
  } = $props();

  let dialog = $state<HTMLDialogElement | null>(null);
  let url = $state<string | null>(null);
  let error = $state<string | null>(null);
  let page = $state<number | null>(null);

  const row = $derived(file?.row ?? null);
  const flagged = $derived(row ? toCheck(row) : []);
  const title = $derived(row?.fields.name.value ? String(row.fields.name.value) : (file?.name ?? ''));
  const pages = $derived(row ? Object.entries(row.detail.pageKinds).sort(([a], [b]) => Number(a) - Number(b)) : []);
  const confidence = $derived(
    row ? Object.entries(row.detail.ocrConfidence).sort(([a], [b]) => Number(a) - Number(b)) : [],
  );

  $effect(() => {
    if (!dialog) return;
    if (file && !dialog.open) dialog.showModal();
    else if (!file && dialog.open) dialog.close();
  });

  // A new file: its PDF, opened at the first page worth a look. Keyed on the path: a running job hands over fresh
  // file objects on every poll, and those must not reload the PDF.
  const path = $derived(file?.path ?? null);
  $effect(() => {
    const current = path;
    url = null;
    error = null;
    const opened = untrack(() => file?.row);
    if (!current || !opened) return;
    const first = toCheck(opened)[0];
    page = first ? fieldPage(first, opened) : (opened.detail.identityPages[0] ?? null);
    let objectUrl: string | null = null;
    let stale = false;
    api.pdf(current).then(
      (blob) => {
        if (stale) return;
        objectUrl = URL.createObjectURL(blob);
        url = objectUrl;
      },
      (failure) => {
        if (!stale) error = failureCode(failure);
      },
    );
    return () => {
      stale = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  });

  function onkeydown(event: KeyboardEvent) {
    if (!event.altKey) return;
    if (event.key === 'ArrowLeft' && onprev) onprev();
    else if (event.key === 'ArrowRight' && onnext) onnext();
    else return;
    event.preventDefault();
  }

  const shown = (key: FieldKey) => {
    if (!row) return null;
    if (key === 'nik' && nik) return formatNik(nik);
    return displayValue(key, row.fields[key]);
  };
</script>

<!-- Every way out asks the parent to clear `file`; the effect above closes the dialog (see PdfPreview). -->
<dialog
  bind:this={dialog}
  aria-labelledby="check-title"
  oncancel={(e) => {
    e.preventDefault();
    onclose();
  }}
  onclose={onclose}
  onclick={(e) => e.target === dialog && onclose()}
  {onkeydown}
>
  {#if file && row}
    <header>
      <div class="who">
        <h2 id="check-title" title={title}>{title}</h2>
        <p class="file" title={file.path}>{file.name}</p>
      </div>
      {#if position}
        <div class="nav" role="group" aria-label="Pindah kontrak">
          <button class="btn btn-outline btn-sm" disabled={!onprev} onclick={() => onprev?.()} title="Sebelumnya (Alt + ←)">
            ‹ Sebelumnya
          </button>
          <span class="count">{position.index + 1} dari {position.total}</span>
          <button class="btn btn-outline btn-sm" disabled={!onnext} onclick={() => onnext?.()} title="Berikutnya (Alt + →)">
            Berikutnya ›
          </button>
        </div>
      {/if}
      <button class="close" aria-label="Tutup" title="Tutup (Esc)" onclick={onclose}>
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="none" stroke="currentColor" stroke-width="2" d="M6 6l12 12M18 6 6 18" />
        </svg>
      </button>
    </header>

    <div class="body">
      <aside class="data" aria-label="Data kontrak">
        {#if run}
          <div class="source">
            <p><strong>Hasil OCR terakhir PDF ini</strong><br />{readBy(run)}</p>
            <a class="btn btn-outline btn-sm" href={jobHref(run.jobId)} onclick={onclose}>Buka proses</a>
          </div>
        {/if}
        {#if flagged.length}
          <p class="verdict check">
            <strong>{flagged.length} kolom perlu dicek.</strong> Cocokkan nilai berlatar kuning dengan PDF di samping.
          </p>
        {:else}
          <p class="verdict ready"><strong>Siap dipakai.</strong> Semua kolom terbaca dan cocok.</p>
        {/if}

        <ul class="fields">
          {#each FIELDS as key (key)}
            {@const field = row.fields[key]}
            {@const value = shown(key)}
            {@const at = fieldPage(key, row)}
            <li class:flag={field.check !== null}>
              <div class="top">
                <span class="label">{FIELD_LABEL[key]} <span class="excel">· Excel: {EXCEL_HEADER[key]}</span></span>
                {#if at && field.check !== null}
                  <button class="page" class:current={page === at} onclick={() => (page = at)}>Lihat hal. {at}</button>
                {/if}
              </div>
              <div class="value">
                {#if value}
                  <span class:mono={key === 'nik'}>{value}</span>
                {:else}
                  <span class="empty">Kosong</span>
                {/if}
              </div>
              {#if field.check}
                <p class="note"><span class="icon" aria-hidden="true">!</span>{field.check}</p>
              {:else if field.basis}
                <p class="note ok"><span class="icon" aria-hidden="true">✓</span>{field.basis}</p>
              {/if}
            </li>
          {/each}
        </ul>

        {#if row.detail.vendorNote}
          <div class="vendor">
            <strong>Catatan untuk vendor</strong>
            <p>{row.detail.vendorNote}</p>
          </div>
        {/if}

        <details class="tech">
          <summary>Detail teknis</summary>
          <dl>
            <dt>Halaman identitas</dt>
            <dd>{row.detail.identityPages.length ? row.detail.identityPages.join(', ') : 'tidak ditemukan'}</dd>
            <dt>Halaman tanda tangan</dt>
            <dd>
              {row.detail.signaturePage ?? 'tidak ditemukan'}{row.detail.signatureSource ? ` (dari ${row.detail.signatureSource})` : ''}
            </dd>
            <dt>Jenis halaman</dt>
            <dd>
              {#each pages as [n, kind], i (n)}{i ? ', ' : ''}hal. {n} {PAGE_KIND[kind] ?? kind}{:else}—{/each}
            </dd>
            <dt>Keyakinan OCR</dt>
            <dd>
              {#each confidence as [n, value], i (n)}{i ? ', ' : ''}hal. {n} {percent(value)}{:else}tidak perlu OCR (teks digital){/each}
            </dd>
            <dt>Dibaca oleh</dt>
            <dd>
              {row.reader === 'claude'
                ? row.readByFallback
                  ? 'Pola teks; nama dan periode dibaca Claude karena pola teks tidak bisa memastikannya'
                  : 'Claude'
                : 'Pola teks'}
              {#if row.claudeCalls?.length}
                <br /><span class="muted">Dikirim ke Claude: {callsLabel(row.claudeCalls)}</span>
              {/if}
              {#if row.tokens}<br /><span class="muted">{formatTokens(row.tokens)}</span>{/if}
            </dd>
            <dt>Keputusan pembaca</dt>
            <dd>{DECISION[row.decision]}</dd>
          </dl>
          {#if row.reasons.length}
            <ul class="codes">
              {#each row.reasons as code (code)}
                <li>{reasonLabel(code)} <span class="mono muted">{code}</span></li>
              {/each}
            </ul>
          {/if}
        </details>
      </aside>

      <section class="pdf" aria-label="PDF kontrak">
        {#if error}
          <p class="state error">{errorLabel(error)}.</p>
        {:else if url}
          <!-- Recreated per page: the viewer reads the fragment only when it loads. No thumbnails, page at full width. -->
          {#key `${url}#${page ?? ''}`}
            <iframe src="{url}#{page ? `page=${page}&` : ''}navpanes=0&view=FitH" title="Isi {file.name}"></iframe>
          {/key}
        {:else}
          <p class="state muted">Memuat PDF…</p>
        {/if}
      </section>
    </div>
  {/if}
</dialog>

<style>
  dialog {
    width: min(1440px, calc(100vw - 2 * var(--blue-space-4)));
    height: calc(100dvh - 2 * var(--blue-space-4));
    max-width: none;
    max-height: none;
    padding: 0;
    border: none;
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    box-shadow: 0 8px 32px rgba(49, 53, 59, 0.3);
  }
  dialog[open] {
    display: flex;
    flex-direction: column;
  }
  dialog::backdrop {
    background: rgba(49, 53, 59, 0.6);
  }
  header {
    display: flex;
    align-items: center;
    gap: var(--blue-space-4);
    padding: var(--blue-space-2) var(--blue-space-2) var(--blue-space-2) var(--blue-space-5);
    border-bottom: 1px solid var(--blue-border);
  }
  .who {
    flex: 1;
    min-width: 0;
  }
  h2 {
    font-size: 17px;
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .file {
    font-size: 12px;
    color: var(--blue-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .nav {
    display: flex;
    align-items: center;
    gap: var(--blue-space-2);
    flex: none;
  }
  .count {
    font-size: 13px;
    color: var(--blue-text-muted);
    min-width: 72px;
    text-align: center;
  }
  .close {
    display: grid;
    flex: none;
    place-items: center;
    width: 40px;
    height: 40px;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--blue-text);
    cursor: pointer;
  }
  .close:hover {
    background: var(--blue-surface-muted);
  }
  .body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(320px, 400px) minmax(0, 1fr);
  }
  .data {
    overflow-y: auto;
    padding: var(--blue-space-4) var(--blue-space-5);
    border-right: 1px solid var(--blue-border);
  }
  .source {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--blue-space-3);
    margin-bottom: var(--blue-space-3);
    padding: var(--blue-space-3);
    border: 1px solid var(--blue-border);
    border-radius: var(--blue-radius);
    font-size: 13px;
    color: var(--blue-text-muted);
  }
  .source strong {
    color: var(--blue-text);
  }
  .source .btn {
    flex: none;
  }
  .verdict {
    padding: var(--blue-space-3);
    border-radius: var(--blue-radius);
    font-size: 13px;
  }
  .verdict.check {
    background: var(--check-fill);
    color: var(--blue-warning-text);
  }
  .verdict.ready {
    background: var(--blue-success-subtle);
    color: var(--blue-success);
  }
  .fields {
    list-style: none;
    margin: var(--blue-space-3) 0 0;
    padding: 0;
  }
  .fields li {
    padding: var(--blue-space-2) var(--blue-space-3);
    border-radius: var(--blue-radius);
    border: 1px solid transparent;
  }
  .fields li + li {
    margin-top: var(--blue-space-1);
  }
  .fields li.flag {
    background: var(--check-fill);
    border-color: var(--check-border);
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--blue-space-2);
  }
  .label {
    font-size: 12px;
    font-weight: 600;
    color: var(--blue-text-muted);
  }
  .excel {
    font-weight: 400;
  }
  .page {
    flex: none;
    padding: 0;
    border: none;
    background: none;
    color: var(--blue-link);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .page:hover {
    text-decoration: underline;
  }
  .page.current {
    font-weight: 700;
  }
  .value {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--blue-space-2);
    font-size: 15px;
    font-weight: 600;
    min-height: 24px;
  }
  .value .mono {
    font-size: 14px;
  }
  .value .btn {
    min-height: 0;
    font-weight: 600;
  }
  .empty {
    font-weight: 400;
    font-style: italic;
    color: var(--blue-text-muted);
  }
  .note {
    display: flex;
    gap: var(--blue-space-2);
    margin-top: 2px;
    font-size: 12px;
    color: var(--blue-warning-text);
  }
  .note.ok {
    color: var(--blue-success);
  }
  .icon {
    flex: none;
    font-weight: 700;
  }
  .vendor {
    margin-top: var(--blue-space-4);
    padding: var(--blue-space-3);
    border: 1px solid var(--blue-border);
    border-radius: var(--blue-radius);
    font-size: 13px;
  }
  .tech {
    margin-top: var(--blue-space-4);
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  .tech summary {
    cursor: pointer;
    font-weight: 600;
  }
  .tech dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px var(--blue-space-3);
    margin: var(--blue-space-2) 0 0;
  }
  .tech dd {
    margin: 0;
    color: var(--blue-text);
  }
  .codes {
    margin: var(--blue-space-2) 0 0;
    padding-left: var(--blue-space-4);
  }
  .pdf {
    display: flex;
    min-width: 0;
    background: var(--blue-background);
  }
  iframe {
    flex: 1;
    border: none;
  }
  .state {
    margin: auto;
    padding: var(--blue-space-5);
  }
  .state.error {
    color: var(--blue-danger-text);
  }
  @media (max-width: 900px) {
    .body {
      grid-template-columns: 1fr;
      grid-template-rows: auto minmax(70vh, 1fr);
      overflow-y: auto;
    }
    .data {
      overflow: visible;
      border-right: none;
      border-bottom: 1px solid var(--blue-border);
    }
    .nav .btn {
      padding: 0 var(--blue-space-2);
    }
  }
  @media (max-width: 640px) {
    dialog {
      width: 100vw;
      height: 100dvh;
      border-radius: 0;
    }
    header {
      flex-wrap: wrap;
      padding-left: var(--blue-space-4);
    }
    .nav {
      order: 3;
      width: 100%;
      justify-content: space-between;
    }
    .data {
      padding: var(--blue-space-4);
    }
  }
</style>
