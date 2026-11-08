import { existsSync, readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import type { DatabaseConfig } from './modules/database/database.ts';
import { type ClientCredential, clientCertificate } from './modules/folders/graph.ts';
import type { SharePointConfig } from './modules/folders/sharepoint.ts';
import { SourceError, segments } from './modules/folders/source.ts';

/** Contracts come from SharePoint when its settings are filled in, otherwise from a local folder (for testing). */
export type SourceConfig = { kind: 'local'; root: string } | { kind: 'sharepoint'; sharepoint: SharePointConfig };

export const CLAUDE_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
export type ClaudeEffort = (typeof CLAUDE_EFFORTS)[number];

/** Claude helping the patterns: the workers start with `--claude`, so Claude reads the position, the name and period
 *  the patterns cannot confirm, and a crop of the NIK line or the signature area when the local reading is unsure.
 *  Like the folder source, the credential decides: an API key when ANTHROPIC_API_KEY is set (production), otherwise
 *  the Claude Code login of the person running the backend (local testing only). */
export interface ClaudeConfig {
  auth: 'api_key' | 'claude_code_login';
  model: string;
  effort: ClaudeEffort;
  timeoutMs: number; // per file, Claude's calls included
}

export interface Config {
  host: string;
  port: number;
  allowedHosts: Set<string>;
  source: SourceConfig;
  extractorCmd: string[]; // with `--claude` and its model and effort when Claude is on
  workbookCmd: string[]; // writes the Excel export: a spec on stdin, the .xlsx on stdout
  workers: number;
  fileTimeoutMs: number;
  recycleAfter: number;
  maxFileBytes: number;
  claude: ClaudeConfig | null; // null: CLAUDE_MODE=off
  database: DatabaseConfig;
}

export class ConfigError extends Error {}

function int(env: Record<string, string | undefined>, key: string, fallback: number, min: number, max: number): number {
  const raw = env[key];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new ConfigError(`${key} must be a whole number from ${min} to ${max}`);
  }
  return value;
}

const SHAREPOINT_REQUIRED = ['SHAREPOINT_TENANT_ID', 'SHAREPOINT_CLIENT_ID', 'SHAREPOINT_SITE_URL'] as const;
// exactly one of these: a certificate (file, or PEM text from Vault) is preferred over a client secret
const SHAREPOINT_CREDENTIALS = ['SHAREPOINT_CERTIFICATE_PATH', 'SHAREPOINT_CERTIFICATE_PEM', 'SHAREPOINT_CLIENT_SECRET'] as const;
const SHAREPOINT_OPTIONAL = ['SHAREPOINT_LIBRARY', 'SHAREPOINT_FOLDER'] as const;
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TENANT = /^[a-z0-9][a-z0-9.-]{0,99}$/i; // a GUID or a domain such as company.onmicrosoft.com

function sharePoint(env: Record<string, string | undefined>): SharePointConfig | null {
  const value = (key: string) => (env[key] ?? '').trim();
  if (![...SHAREPOINT_REQUIRED, ...SHAREPOINT_CREDENTIALS, ...SHAREPOINT_OPTIONAL].some((key) => value(key))) return null;
  // Half a configuration is a mistake, not a reason to fall back to the local folder quietly.
  const missing = SHAREPOINT_REQUIRED.filter((key) => !value(key));
  if (missing.length) {
    throw new ConfigError(`SharePoint also needs ${missing.join(', ')}; leave every SHAREPOINT_* empty to use FOLDER_ROOT`);
  }
  const [chosen, ...others] = SHAREPOINT_CREDENTIALS.filter((key) => value(key));
  if (!chosen || others.length) {
    throw new ConfigError(`SharePoint needs exactly one of ${SHAREPOINT_CREDENTIALS.join(', ')} (a certificate is safer)`);
  }
  const credential = clientCredential(chosen, value(chosen));
  if (!TENANT.test(value('SHAREPOINT_TENANT_ID'))) throw new ConfigError('SHAREPOINT_TENANT_ID must be the Directory (tenant) ID');
  if (!GUID.test(value('SHAREPOINT_CLIENT_ID'))) throw new ConfigError('SHAREPOINT_CLIENT_ID must be the Application (client) ID');
  let site: URL;
  try {
    site = new URL(value('SHAREPOINT_SITE_URL'));
  } catch {
    throw new ConfigError('SHAREPOINT_SITE_URL must be a URL such as https://company.sharepoint.com/sites/hr');
  }
  if (site.protocol !== 'https:' || !site.hostname.endsWith('.sharepoint.com') || site.search || site.hash) {
    throw new ConfigError('SHAREPOINT_SITE_URL must be a URL such as https://company.sharepoint.com/sites/hr');
  }
  try {
    segments(value('SHAREPOINT_FOLDER'));
  } catch (error) {
    if (error instanceof SourceError) throw new ConfigError('SHAREPOINT_FOLDER must be a folder path inside the library');
    throw error;
  }
  return {
    tenantId: value('SHAREPOINT_TENANT_ID'),
    clientId: value('SHAREPOINT_CLIENT_ID'),
    credential,
    siteUrl: `${site.origin}${site.pathname.replace(/\/+$/, '')}`,
    library: value('SHAREPOINT_LIBRARY'),
    folder: value('SHAREPOINT_FOLDER'),
  };
}

function clientCredential(key: (typeof SHAREPOINT_CREDENTIALS)[number], raw: string): ClientCredential {
  if (key === 'SHAREPOINT_CLIENT_SECRET') return { secret: raw };
  let pem = raw.replaceAll('\\n', '\n'); // PEM text kept on one line, as env values often are
  if (key === 'SHAREPOINT_CERTIFICATE_PATH') {
    try {
      pem = readFileSync(resolve(raw), 'utf8');
    } catch {
      throw new ConfigError('SHAREPOINT_CERTIFICATE_PATH must point to a readable PEM file');
    }
  }
  try {
    return { certificate: clientCertificate(pem) };
  } catch (error) {
    throw new ConfigError(`${key}: ${error instanceof Error ? error.message : 'unreadable'}`);
  }
}

function sourceConfig(env: Record<string, string | undefined>): SourceConfig {
  const sharepoint = sharePoint(env);
  if (sharepoint) return { kind: 'sharepoint', sharepoint };
  const root = env.FOLDER_ROOT ? resolve(env.FOLDER_ROOT) : '';
  if (!root || !existsSync(root) || !statSync(root).isDirectory()) {
    throw new ConfigError('FOLDER_ROOT must point to an existing folder (or fill in the SHAREPOINT_* settings)');
  }
  return { kind: 'local', root };
}

/** ALLOW_AUTO=true lets a file whose every check is green be decided `auto` (Otomatis) on the website; false or empty
 *  keeps the extractor config's `decision.allow_auto` (off by default), so such a file goes to review. */
function allowAuto(env: Record<string, string | undefined>): string[] {
  const raw = (env.ALLOW_AUTO ?? '').trim().toLowerCase();
  if (raw === '') return [];
  if (raw === 'true') return ['--allow-auto'];
  if (raw === 'false') return ['--no-allow-auto'];
  throw new ConfigError('ALLOW_AUTO must be true or false');
}

const DEFAULT_CLAUDE_MODEL = 'claude-haiku-5-5';
const DEFAULT_CLAUDE_EFFORT: ClaudeEffort = 'max';
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9@._-]{0,99}$/; // the extractor's own rule
const API_KEY = /^sk-ant-[A-Za-z0-9_-]{10,}$/;
// On top of the file's own time: the text call may take up to the extractor's 120 s, tried up to three times, and
// each of the two crops (NIK line, signature area) up to 120 s more.
const CLAUDE_CALL_BUDGET_MS = 5 * 120_000;

function claudeConfig(env: Record<string, string | undefined>, fileTimeoutMs: number): ClaudeConfig | null {
  const value = (key: string) => (env[key] ?? '').trim();
  const mode = value('CLAUDE_MODE').toLowerCase();
  if (mode === 'off') return null;
  if (mode !== '' && mode !== 'on') throw new ConfigError('CLAUDE_MODE must be on or off');
  const key = value('ANTHROPIC_API_KEY');
  if (key && !API_KEY.test(key)) {
    throw new ConfigError('ANTHROPIC_API_KEY must be an Anthropic API key (sk-ant-...), or empty to use the Claude Code login');
  }
  const model = value('CLAUDE_MODEL') || DEFAULT_CLAUDE_MODEL;
  if (!MODEL_ID.test(model)) throw new ConfigError('CLAUDE_MODEL must be a Claude model id such as claude-haiku-5-5');
  const effort = (value('CLAUDE_EFFORT').toLowerCase() || DEFAULT_CLAUDE_EFFORT) as ClaudeEffort;
  if (!CLAUDE_EFFORTS.includes(effort)) throw new ConfigError(`CLAUDE_EFFORT must be one of ${CLAUDE_EFFORTS.join(', ')}`);
  return { auth: key ? 'api_key' : 'claude_code_login', model, effort, timeoutMs: fileTimeoutMs + CLAUDE_CALL_BUDGET_MS };
}

// apps/backend/data/ is gitignored (the repository ignores every data/ folder)
const DEFAULT_DATABASE_DIR = resolve(import.meta.dir, '..', 'data', 'pglite');

/** DATABASE_URL names a PostgreSQL server; left empty, PostgreSQL runs inside the backend (PGlite) with its files in
 *  DATABASE_DIR, for local testing. */
function databaseConfig(env: Record<string, string | undefined>): DatabaseConfig {
  const url = (env.DATABASE_URL ?? '').trim();
  if (url) {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      throw new ConfigError('DATABASE_URL must be a URL such as postgres://user:password@127.0.0.1:5432/contract_ocr');
    }
    if (parsed.protocol !== 'postgres:' && parsed.protocol !== 'postgresql:') {
      throw new ConfigError('DATABASE_URL must start with postgres:// or postgresql://');
    }
    return { kind: 'postgres', url };
  }
  const dir = (env.DATABASE_DIR ?? '').trim();
  return { kind: 'embedded', dir: dir ? resolve(dir) : DEFAULT_DATABASE_DIR };
}

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const source = sourceConfig(env);
  const python = env.EXTRACTOR_PYTHON ?? '';
  if (!python || !existsSync(python)) {
    throw new ConfigError('EXTRACTOR_PYTHON must point to the python executable of the venv with contract-extractor');
  }
  // Python itself, not the contract-extractor.exe launcher, so killing a stuck worker really stops it on Windows.
  const extractorCmd = [
    python,
    '-m',
    'contract_extractor',
    'serve-stdio',
    ...(env.EXTRACTOR_CONFIG ? ['--config', env.EXTRACTOR_CONFIG] : []),
    ...allowAuto(env),
  ];
  const workbookCmd = [python, '-m', 'contract_extractor', 'xlsx'];
  const port = int(env, 'PORT', 3000, 1, 65535);
  const host = env.HOST || '127.0.0.1';
  const allowedHosts = new Set(
    (env.ALLOWED_HOSTS || `localhost:${port},127.0.0.1:${port}`)
      .split(',')
      .map((h) => h.trim().toLowerCase())
      .filter(Boolean),
  );
  const workers = int(env, 'WORKERS', 2, 1, 8);
  const fileTimeoutMs = int(env, 'FILE_TIMEOUT_S', 120, 10, 3600) * 1000;
  const claude = claudeConfig(env, fileTimeoutMs);
  if (claude) extractorCmd.push('--claude', '--model', claude.model, '--effort', claude.effort);
  return {
    host,
    port,
    allowedHosts,
    source,
    extractorCmd,
    workbookCmd,
    workers,
    fileTimeoutMs,
    recycleAfter: int(env, 'RECYCLE_AFTER', 200, 1, 100_000),
    maxFileBytes: int(env, 'MAX_FILE_MB', 50, 1, 500) * 1024 * 1024,
    claude,
    database: databaseConfig(env),
  };
}
