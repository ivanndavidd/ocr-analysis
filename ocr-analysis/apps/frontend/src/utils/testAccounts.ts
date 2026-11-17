import type { LoginRequest } from '@contract-ocr/shared-types';

/**
 * Fictitious accounts for the mock sign-in's quick login, so one person can test as several users (the run queue,
 * the shared history). This file is committed: never a real person's email or name. The list shows only in a
 * development build (`bun run dev`) and goes with the mock sign-in when Apps Gateway replaces it.
 */
export const TEST_ACCOUNTS: readonly LoginRequest[] = [
  { email: 'penguji.pg@gdn-commerce.com', name: 'Penguji PG' },
  { email: 'penguji.dua@gdn-commerce.com', name: 'Penguji Dua' },
  { email: 'penguji.tiga@gdn-commerce.com', name: 'Penguji Tiga' },
  { email: 'penguji.empat@gdn-commerce.com', name: 'Penguji Empat' },
];
