import { LANGS, type Lang, type Note, noteText } from '@contract-ocr/shared-types';
import { en } from './messages/en.ts';
import { type Messages, id } from './messages/id.ts';

// The website's two languages. Indonesian is the default, whatever the browser says: it is the wording HR knows.
// Each browser remembers its own choice. The current language is read through `lang()`, which the Svelte store binds
// to reactive state (src/store/locale.svelte.ts), so a page re-renders every text that read it when the language
// changes; tests and plain modules get Indonesian unless they bind another.

export type { Lang, Messages };
export { LANGS };
export const DEFAULT_LANG: Lang = 'id';

const MESSAGES: Record<Lang, Messages> = { id, en };
/** Dates and numbers: `1 Jan 2045`, `12.475`, `11.02` in Indonesian; `1 Jan 2045`, `12,475`, `11:02` in English. */
const LOCALES: Record<Lang, string> = { id: 'id-ID', en: 'en-GB' };
const STORAGE_KEY = 'contract-ocr:lang';

let current: () => Lang = () => DEFAULT_LANG;

/** Where the current language comes from; the store binds its reactive state once, at start. */
export function bindLang(read: () => Lang): void {
  current = read;
}

export const lang = (): Lang => current();
/** Every text of the current language. */
export const t = (): Messages => MESSAGES[lang()];
/** The BCP 47 tag for `Intl` formatting in the current language. */
export const locale = (): string => LOCALES[lang()];
/** A cell note the backend sent, in the current language. */
export const noteLabel = (note: Note): string => noteText(note, lang());

export const isLang = (value: unknown): value is Lang => LANGS.includes(value as Lang);

/** The language this browser chose before; the default when none, or when storage is blocked. */
export function loadLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return isLang(saved) ? saved : DEFAULT_LANG;
  } catch {
    return DEFAULT_LANG;
  }
}

export function saveLang(next: Lang): void {
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // storage blocked: the choice still holds until the page reloads
  }
}
