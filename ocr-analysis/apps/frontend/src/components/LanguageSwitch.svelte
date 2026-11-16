<script lang="ts">
  import { setLang } from '../store/locale.svelte.ts';
  import { LANGS, lang, t } from '../utils/i18n.ts';

  // The language switch on every page, the sign-in page included: two short codes, each with its own name in its own
  // language, so someone who cannot read the current language still finds theirs. The group's label is in both.
</script>

<div class="switch" role="group" aria-label={t().language.label}>
  {#each LANGS as code (code)}
    <button
      type="button"
      class:active={lang() === code}
      aria-pressed={lang() === code}
      lang={code}
      title={t().language.names[code]}
      onclick={() => setLang(code)}
    >
      <span aria-hidden="true">{code.toUpperCase()}</span>
      <span class="sr-only">{t().language.names[code]}</span>
    </button>
  {/each}
</div>

<style>
  .switch {
    display: inline-flex;
    flex: none;
    border: 1px solid var(--blue-border-strong);
    border-radius: var(--blue-radius);
    overflow: hidden;
  }
  button {
    min-width: 34px;
    min-height: 28px;
    padding: 0 var(--blue-space-2);
    border: 0;
    background: var(--blue-surface);
    color: var(--blue-text-muted);
    font: 600 12px/1 var(--blue-font);
    cursor: pointer;
  }
  button + button {
    border-left: 1px solid var(--blue-border-strong);
  }
  button:hover:not(.active) {
    color: var(--blue-text);
    background: var(--blue-surface-muted);
  }
  button.active {
    background: var(--blue-brand-subtle);
    color: var(--blue-brand);
    font-weight: 700;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--blue-brand);
    outline-offset: -2px;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
</style>
