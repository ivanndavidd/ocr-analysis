import type { ClaudeCall, ClaudeState, ReadMode } from '@contract-ocr/shared-types';
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
  return mode === 'full_page' ? m.jobFullPage : claude ? m.jobClaude : m.jobPatterns;
}

// The two ways a run reads. The OCR mode is the main flow; the no-OCR mode sends the first and last page of every PDF
// whole and unmasked to Claude, a backup the owner and mentor test while sending whole pages is not approved yet
// (2026-11-04). The switch shows until they decide.
export const MODES: ReadMode[] = ['ocr', 'full_page'];
export const modeLabel = (mode: ReadMode) => t().claude.modes[mode];

export const fullPageFlow = () => t().claude.fullPageFlow;
export const fullPageDescription = () => t().claude.fullPageDescription;

/** The warning in the run confirmation of the no-OCR mode, which sends whole, unmasked pages. */
export const fullPageConsent = (pdfs: number) => t().claude.fullPageConsent(pdfs);

/** The no-OCR mode's warning in one line, always shown; `fullPageConsent` spells it out under "Detail". */
export const fullPageNotice = () => t().claude.fullPageNotice;

/** Why the no-OCR mode cannot run now; null when it can. Claude is its only reader, so it needs Claude ready. */
export function fullPageBlocked(state: ClaudeState | null): string | null {
  if (state === 'on') return null;
  if (state === 'starting') return t().claude.fullPageWait;
  return t().claude.fullPageNeedsClaude;
}

const MODE_KEY = 'contract-ocr.read-mode';

/** The mode picked in this tab; a new tab starts with the OCR mode. */
export function loadMode(): ReadMode {
  try {
    return sessionStorage.getItem(MODE_KEY) === 'full_page' ? 'full_page' : 'ocr';
  } catch {
    return 'ocr'; // storage blocked
  }
}

export function saveMode(mode: ReadMode): void {
  try {
    sessionStorage.setItem(MODE_KEY, mode);
  } catch {
    // storage blocked: the choice still holds on this page
  }
}
