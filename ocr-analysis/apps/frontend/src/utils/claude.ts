import type { ClaudeCall, ClaudeState, ReadMode, StartMode } from '@contract-ocr/shared-types';
import { formatCount, totalTokens } from './format.ts';
import { t } from './i18n.ts';

/** How "Jalankan OCR" reads, by whether Claude helps the patterns (the backend's `claude` state). */
export const flowLabel = (state: ClaudeState) => t().claude.flow[state];

// Claude is a fallback (owner 2026-11-11): the identity page's text goes out only for a field the laptop leaves unsure.
export function flowDescription(state: ClaudeState | null): string {
  const { patterns, withClaude } = t().claude;
  return state === 'on' || state === 'starting' ? patterns + withClaude : patterns;
}

/** Why Claude is not helping, for the run area; null when it is, or is still starting. */
export function claudeHint(state: ClaudeState | null): string | null {
  if (state === 'off') return t().claude.hintOff;
  if (state === 'unavailable') return t().claude.hintUnavailable;
  return null;
}

/** The warning in the run confirmation when Claude may be sent something; null when nothing leaves the laptop. */
export function claudeConsent(state: ClaudeState | null, pdfs: number): string | null {
  if (state !== 'on' && state !== 'starting') return null;
  return t().claude.consent(pdfs);
}

/** The one line a run confirmation always shows when Claude may be sent something; `claudeConsent` spells it out under
 *  the confirmation's "Detail". Null when nothing leaves the laptop. */
export function claudeNotice(state: ClaudeState | null): string | null {
  if (state !== 'on' && state !== 'starting') return null;
  return t().claude.notice;
}

/** `teks halaman identitas (12.475 token), potongan baris NIK (2.010 token)`: what was sent for one file, in order. */
export function callsLabel(calls: ClaudeCall[]): string {
  const { calls: labels, callTokens } = t().claude;
  return calls
    .map(({ kind, tokens }) => (tokens ? callTokens(labels[kind], formatCount(totalTokens(tokens))) : labels[kind]))
    .join(', ');
}

/** A job in the lists: how it read, and whether Claude was helping when it ran. */
export function jobFlowLabel(claude: boolean, mode: ReadMode = 'ocr'): string {
  const m = t().claude;
  if (mode === 'claude_page') return m.jobPage;
  if (mode === 'full_page') return m.jobFullPage; // the old no-OCR trial, for runs saved before
  return claude ? m.jobClaude : m.jobPatterns;
}

// The two ways a run reads (owner 2026-11-16). The OCR mode is the main flow. In the page mode the same local OCR reads
// the NIK, the signature and the vendor, while Claude reads the name, period and title from the identity page as an
// image with the NIK blacked out. The old no-OCR trial ('full_page', whole pages unmasked) can no longer be started;
// its saved runs keep their label in the history.
export const MODES: StartMode[] = ['ocr', 'claude_page'];
export const modeLabel = (mode: ReadMode) => t().claude.modes[mode];

export const pageFlow = () => t().claude.pageFlow;
export const pageDescription = () => t().claude.pageDescription;

/** What the page mode sends to Claude, in the run confirmation. */
export const pageConsent = (pdfs: number) => t().claude.pageConsent(pdfs);

/** The page mode's warning in one line, always shown; `pageConsent` spells it out under "Detail". */
export const pageNotice = () => t().claude.pageNotice;

/** Why the page mode cannot run now; null when it can. Claude reading the identity page is the point of the mode. */
export function pageBlocked(state: ClaudeState | null): string | null {
  if (state === 'on') return null;
  if (state === 'starting') return t().claude.pageWait;
  return t().claude.pageNeedsClaude;
}

const MODE_KEY = 'contract-ocr.read-mode';

/** The mode picked in this tab; a new tab, or one that last picked the old no-OCR trial, starts with the OCR mode. */
export function loadMode(): StartMode {
  try {
    return sessionStorage.getItem(MODE_KEY) === 'claude_page' ? 'claude_page' : 'ocr';
  } catch {
    return 'ocr'; // storage blocked
  }
}

export function saveMode(mode: StartMode): void {
  try {
    sessionStorage.setItem(MODE_KEY, mode);
  } catch {
    // storage blocked: the choice still holds on this page
  }
}
