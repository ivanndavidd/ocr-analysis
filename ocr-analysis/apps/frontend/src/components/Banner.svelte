<script lang="ts">
  import type { Snippet } from 'svelte';

  let { tone = 'info', children }: { tone?: 'info' | 'warning' | 'danger'; children: Snippet } = $props();
</script>

<div class="banner {tone}" role={tone === 'danger' ? 'alert' : 'status'}>
  <span class="dot" aria-hidden="true"></span>
  <div>{@render children()}</div>
</div>

<style>
  .banner {
    display: flex;
    gap: var(--blue-space-3);
    align-items: flex-start;
    padding: var(--blue-space-3) var(--blue-space-4);
    border-radius: var(--blue-radius);
    border: 1px solid;
  }
  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    margin-top: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .info {
    background: var(--blue-brand-subtle);
    border-color: #b3dff3;
    color: var(--blue-text);
  }
  .info .dot {
    color: var(--blue-brand);
  }
  .warning {
    background: var(--blue-warning-subtle);
    border-color: #f5d27a;
    color: var(--blue-text);
  }
  .warning .dot {
    color: var(--blue-warning);
  }
  .danger {
    background: var(--blue-danger-subtle);
    border-color: #f5b5b3;
    color: var(--blue-danger-text);
  }
</style>
