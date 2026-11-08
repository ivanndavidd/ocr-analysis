<script lang="ts">
  import type { Snippet } from 'svelte';

  import type { Tone } from '../utils/tones.ts';

  let { tone = 'neutral', children }: { tone?: Tone; children: Snippet } = $props();
</script>

<span class="badge {tone}">{@render children()}</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: var(--blue-space-1);
    padding: 2px var(--blue-space-2);
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    line-height: 18px;
    white-space: nowrap;
  }
  .neutral {
    background: var(--blue-surface-muted);
    color: var(--blue-text-muted);
    box-shadow: inset 0 0 0 1px var(--blue-border);
  }
  .brand {
    background: var(--blue-brand-subtle);
    color: var(--blue-brand-pressed);
  }
  .success {
    background: var(--blue-success-subtle);
    color: var(--blue-success);
  }
  .warning {
    background: var(--blue-warning-subtle);
    color: var(--blue-warning-text);
  }
  .danger {
    background: var(--blue-danger-subtle);
    color: var(--blue-danger-text);
  }
</style>
