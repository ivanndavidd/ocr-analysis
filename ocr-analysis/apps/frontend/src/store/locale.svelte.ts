import { type Lang, bindLang, loadLang, saveLang } from '../utils/i18n.ts';

// The chosen language as reactive state: every text a page rendered through `t()`, `noteLabel()` or a formatter read
// it, so switching re-renders those texts in place, with no reload and no lost selection or open dialog.

const state = $state<{ lang: Lang }>({ lang: loadLang() });
bindLang(() => state.lang);

function mark(next: Lang) {
  document.documentElement.lang = next; // screen readers, hyphenation, the browser's translate offer
}
mark(state.lang);

export function setLang(next: Lang): void {
  if (next === state.lang) return;
  state.lang = next;
  saveLang(next);
  mark(next);
}
