<script lang="ts">
  import type { Snippet } from 'svelte';

  // A yes/no question over the page, for steps that start work or may cost money. "Batal" has the focus, so an Enter
  // pressed out of habit does not confirm.

  let {
    open,
    title,
    confirmLabel,
    busy = false,
    onconfirm,
    oncancel,
    children,
  }: {
    open: boolean;
    title: string;
    confirmLabel: string;
    busy?: boolean; // the confirmed step is running: both buttons wait
    onconfirm: () => void;
    oncancel: () => void;
    children: Snippet;
  } = $props();

  const id = $props.id();
  let dialog = $state<HTMLDialogElement | null>(null);
  let cancelButton = $state<HTMLButtonElement | null>(null);

  $effect(() => {
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelButton?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  });

  const cancel = () => !busy && oncancel();
</script>

<!-- Every way out goes through `oncancel` and the parent's `open`; the `close` event alone is not dependable. -->
<dialog
  bind:this={dialog}
  aria-labelledby="{id}-title"
  aria-describedby="{id}-body"
  oncancel={(e) => {
    e.preventDefault();
    cancel();
  }}
  onclick={(e) => e.target === dialog && cancel()}
>
  <h2 id="{id}-title">{title}</h2>
  <div class="body" id="{id}-body">{@render children()}</div>
  <div class="actions">
    <button class="btn btn-outline" bind:this={cancelButton} disabled={busy} onclick={cancel}>Batal</button>
    <button class="btn btn-primary" disabled={busy} onclick={onconfirm}>{confirmLabel}</button>
  </div>
</dialog>

<style>
  dialog {
    width: min(480px, calc(100vw - 2 * var(--blue-space-4)));
    max-width: none;
    padding: var(--blue-space-5);
    border: none;
    border-radius: var(--blue-radius);
    background: var(--blue-surface);
    color: var(--blue-text);
    box-shadow: 0 8px 32px rgba(49, 53, 59, 0.3);
  }
  dialog::backdrop {
    background: rgba(49, 53, 59, 0.6);
  }
  h2 {
    margin: 0 0 var(--blue-space-4);
    font-size: 18px;
    line-height: 26px;
  }
  .body {
    display: flex;
    flex-direction: column;
    gap: var(--blue-space-4);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--blue-space-2);
    margin-top: var(--blue-space-5);
  }
  @media (max-width: 640px) {
    .actions .btn {
      flex: 1;
    }
  }
</style>
