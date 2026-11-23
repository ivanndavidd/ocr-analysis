<script lang="ts">
  import { api, failureCode } from '../services/api.ts';
  import { t } from '../utils/i18n.ts';
  import { errorLabel } from '../utils/labels.ts';

  // A contract shown over the page instead of in a new tab: the list behind it keeps its selection and scroll, and the
  // file name never lands in the address bar or browser history. The PDF lives in memory only while it is open.

  let {
    path, // root-relative; null when closed
    onclose,
    note = null, // a line under the title, e.g. that no run has read this PDF yet
  }: { path: string | null; onclose: () => void; note?: string | null } = $props();

  let dialog = $state<HTMLDialogElement | null>(null);
  let url = $state<string | null>(null);
  let error = $state<string | null>(null);
  const name = $derived(path?.split('/').at(-1) ?? '');

  $effect(() => {
    if (!dialog) return;
    if (path && !dialog.open) dialog.showModal();
    else if (!path && dialog.open) dialog.close();
  });

  $effect(() => {
    const current = path;
    url = null;
    error = null;
    if (!current) return;
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
</script>

<!-- Every way out asks the parent to clear `path` and the effect above closes the dialog: the `close` event alone is not
     dependable (some Chromium builds never fire it). -->
<dialog
  bind:this={dialog}
  aria-labelledby="pdf-preview-title"
  oncancel={(e) => {
    e.preventDefault();
    onclose();
  }}
  onclose={onclose}
  onclick={(e) => e.target === dialog && onclose()}
>
  <header>
    <svg class="pdf" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path fill="currentColor" d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V9h5.5z" />
    </svg>
    <div class="title">
      <h2 id="pdf-preview-title" title={name}>{name}</h2>
      {#if note}<p class="note">{note}</p>{/if}
    </div>
    <button class="close" aria-label={t().preview.close} title={t().common.closeEsc} onclick={onclose}>
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path fill="none" stroke="currentColor" stroke-width="2" d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  </header>
  <div class="body">
    {#if error}
      <p class="state error">{errorLabel(error)}.</p>
    {:else if url}
      <iframe src={url} title={t().common.pdfOf(name)}></iframe>
    {:else if path}
      <p class="state muted">{t().common.loadingPdf}</p>
    {/if}
  </div>
</dialog>

<style>
  dialog {
    width: min(1100px, calc(100vw - 2 * var(--blue-space-4)));
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
    gap: var(--blue-space-3);
    padding: var(--blue-space-2) var(--blue-space-2) var(--blue-space-2) var(--blue-space-4);
    border-bottom: 1px solid var(--blue-border);
  }
  .title {
    flex: 1;
    min-width: 0;
  }
  .note {
    margin: 0;
    font-size: 12px;
    color: var(--blue-text-muted);
  }
  h2 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pdf {
    flex: none;
    color: var(--blue-danger);
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
    display: flex;
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
  .error {
    color: var(--blue-danger-text);
  }
  @media (max-width: 640px) {
    dialog {
      width: 100vw;
      height: 100dvh;
      border-radius: 0;
    }
  }
</style>
