import type { ClaudeState } from '@contract-ocr/shared-types';
import type { ExtractorClient } from './protocol.ts';

/** Whether Claude helps the patterns, for the page: off by configuration, or as the workers' ready messages report it.
 *  Workers that cannot reach Claude still read with the patterns alone, so this never blocks a job. */
export function claudeState(configured: boolean, extractor: ExtractorClient): ClaudeState {
  if (!configured) return 'off';
  const claude = extractor.claude();
  if (claude === null) return extractor.status().state === 'unavailable' ? 'unavailable' : 'starting';
  return claude ? 'on' : 'unavailable';
}
