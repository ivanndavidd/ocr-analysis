// Wording for the fixed codes the backend and the extractor send, in the current language (utils/messages). Unknown
// codes are shown as they are.
import type { Decision, FileState, JobStatus } from '@contract-ocr/shared-types';
import { t } from './i18n.ts';

const has = <T extends object>(table: T, code: string): code is Extract<keyof T, string> => Object.hasOwn(table, code);

export const decisionLabel = (decision: Decision) => t().labels.decision[decision];
export const fileStateLabel = (state: FileState) => t().labels.fileState[state];
export const jobStatusLabel = (status: JobStatus) => t().labels.jobStatus[status];

export function pageKindLabel(kind: string): string {
  const kinds = t().labels.pageKind;
  return has(kinds, kind) ? kinds[kind] : kind;
}

function reason(code: string): string | undefined {
  const reasons = t().labels.reasons;
  return has(reasons, code) ? reasons[code] : undefined;
}

// `extractor_error:<kind>` carries the kind of failure (`timeout`, `api:RateLimitError`); the label is the same.
export const reasonLabel = (code: string) => reason(code) ?? reason(code.split(':')[0] ?? '') ?? code;

export function errorLabel(code: string): string {
  const errors = t().labels.errors;
  return has(errors, code) ? errors[code] : (reason(code) ?? code);
}
