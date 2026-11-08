import { describe, expect, test } from 'bun:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ConfigError, loadConfig } from '../src/config.ts';
import { testCertificate } from './fixtures/certificate.ts';

const LOCAL = { FOLDER_ROOT: mkdtempSync(join(tmpdir(), 'ocr-config-')), EXTRACTOR_PYTHON: process.execPath };
const SHAREPOINT = {
  SHAREPOINT_TENANT_ID: '11111111-1111-1111-1111-111111111111',
  SHAREPOINT_CLIENT_ID: '22222222-2222-2222-2222-222222222222',
  SHAREPOINT_CLIENT_SECRET: 'test-secret',
  SHAREPOINT_SITE_URL: 'https://contoso.sharepoint.com/sites/HR/',
};

function error(env: Record<string, string>): string {
  try {
    loadConfig(env);
  } catch (e) {
    if (e instanceof ConfigError) return e.message;
    throw e;
  }
  return 'no error';
}

describe('database settings', () => {
  test('without DATABASE_URL the embedded database keeps its files under apps/backend/data', () => {
    const { database } = loadConfig(LOCAL);
    expect(database.kind).toBe('embedded');
    expect(database.kind === 'embedded' && database.dir?.replaceAll('\\', '/')).toEndWith('apps/backend/data/pglite');
    const dir = mkdtempSync(join(tmpdir(), 'ocr-db-'));
    expect(loadConfig({ ...LOCAL, DATABASE_DIR: dir }).database).toEqual({ kind: 'embedded', dir });
  });

  test('DATABASE_URL must be a PostgreSQL URL', () => {
    const url = 'postgres://ocr:test@127.0.0.1:5432/contract_ocr';
    expect(loadConfig({ ...LOCAL, DATABASE_URL: url }).database).toEqual({ kind: 'postgres', url });
    expect(error({ ...LOCAL, DATABASE_URL: 'mysql://127.0.0.1/x' })).toContain('postgres://');
    expect(error({ ...LOCAL, DATABASE_URL: 'not a url' })).toContain('DATABASE_URL');
  });
});

describe('folder source settings', () => {
  test('without SharePoint settings the local folder is used', () => {
    expect(loadConfig(LOCAL).source).toEqual({ kind: 'local', root: LOCAL.FOLDER_ROOT });
    expect(error({ EXTRACTOR_PYTHON: process.execPath })).toContain('FOLDER_ROOT');
  });

  test('with SharePoint settings SharePoint is used and FOLDER_ROOT is not needed', () => {
    const source = loadConfig({ EXTRACTOR_PYTHON: process.execPath, ...SHAREPOINT, SHAREPOINT_FOLDER: 'Kontrak HR' }).source;
    expect(source).toEqual({
      kind: 'sharepoint',
      sharepoint: {
        tenantId: SHAREPOINT.SHAREPOINT_TENANT_ID,
        clientId: SHAREPOINT.SHAREPOINT_CLIENT_ID,
        credential: { secret: 'test-secret' },
        siteUrl: 'https://contoso.sharepoint.com/sites/HR',
        library: '',
        folder: 'Kontrak HR',
      },
    });
  });

  test('half a SharePoint configuration is refused, naming what is missing but never a value', () => {
    const message = error({ ...LOCAL, SHAREPOINT_TENANT_ID: SHAREPOINT.SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_SECRET: 'test-secret' });
    expect(message).toContain('SHAREPOINT_CLIENT_ID, SHAREPOINT_SITE_URL');
    expect(message).not.toContain('test-secret');
    expect(error({ ...LOCAL, SHAREPOINT_FOLDER: 'Kontrak HR' })).toContain('SHAREPOINT_TENANT_ID');
  });

  test('a certificate can come from a file or from PEM text kept on one line, and only one credential is allowed', () => {
    const { SHAREPOINT_CLIENT_SECRET: _secret, ...site } = SHAREPOINT;
    const cert = testCertificate();
    const file = join(LOCAL.FOLDER_ROOT, 'app.pem'); // a temp folder, never the repository
    writeFileSync(file, cert.pem);
    for (const env of [{ SHAREPOINT_CERTIFICATE_PATH: file }, { SHAREPOINT_CERTIFICATE_PEM: cert.pem.replaceAll('\n', '\\n') }]) {
      const source = loadConfig({ ...LOCAL, ...site, ...env }).source;
      expect(source.kind === 'sharepoint' && 'certificate' in source.sharepoint.credential).toBe(true);
    }
    expect(error({ ...LOCAL, ...SHAREPOINT, SHAREPOINT_CERTIFICATE_PATH: file })).toContain('exactly one');
    expect(error({ ...LOCAL, ...site })).toContain('exactly one');
    expect(error({ ...LOCAL, ...site, SHAREPOINT_CERTIFICATE_PATH: join(LOCAL.FOLDER_ROOT, 'missing.pem') })).toContain(
      'SHAREPOINT_CERTIFICATE_PATH',
    );
    const message = error({ ...LOCAL, ...site, SHAREPOINT_CERTIFICATE_PEM: 'not a pem' });
    expect(message).toContain('SHAREPOINT_CERTIFICATE_PEM');
    expect(message).not.toContain('not a pem');
  });

  test('Claude helps by default, with Haiku at max effort through the Claude Code login', () => {
    const { claude, extractorCmd, fileTimeoutMs } = loadConfig(LOCAL);
    expect(claude).toEqual({
      auth: 'claude_code_login',
      model: 'claude-haiku-5-5',
      effort: 'max',
      timeoutMs: fileTimeoutMs + 600_000,
    });
    expect(extractorCmd.slice(-5)).toEqual(['--claude', '--model', 'claude-haiku-5-5', '--effort', 'max']);
    const off = loadConfig({ ...LOCAL, CLAUDE_MODE: 'OFF' });
    expect(off.claude).toBeNull();
    expect(off.extractorCmd).not.toContain('--claude'); // nothing leaves the machine
  });

  test('an API key switches Claude to the Claude API; model and effort can be changed', () => {
    const config = loadConfig({
      ...LOCAL,
      ANTHROPIC_API_KEY: ' sk-ant-api03-test-not-real ',
      CLAUDE_MODEL: 'claude-sonnet-5-5',
      CLAUDE_EFFORT: 'High',
    });
    expect(config.claude?.auth).toBe('api_key');
    expect(config.extractorCmd.slice(-5)).toEqual(['--claude', '--model', 'claude-sonnet-5-5', '--effort', 'high']);
    expect(JSON.stringify(config)).not.toContain('sk-ant'); // the key reaches the workers through their environment only
  });

  test.each([
    [{ CLAUDE_MODE: 'maybe' }, 'CLAUDE_MODE'],
    [{ ANTHROPIC_API_KEY: '"sk-ant-api03-quoted"' }, 'ANTHROPIC_API_KEY'],
    [{ CLAUDE_MODEL: 'claude; rm -rf /' }, 'CLAUDE_MODEL'],
    [{ CLAUDE_EFFORT: 'extreme' }, 'CLAUDE_EFFORT'],
  ])('refuses Claude setting %p', (override, key) => {
    const message = error({ ...LOCAL, ...override });
    expect(message).toContain(key);
    expect(message).not.toContain('sk-ant-api03-quoted');
  });

  test('ALLOW_AUTO reaches the extractor workers as a flag, and only true or false is accepted', () => {
    const flags = (value?: string) =>
      loadConfig({ ...LOCAL, CLAUDE_MODE: 'off', ...(value === undefined ? {} : { ALLOW_AUTO: value }) }).extractorCmd.slice(4);
    expect(flags()).toEqual([]);
    expect(flags('')).toEqual([]);
    expect(flags('true')).toEqual(['--allow-auto']);
    expect(flags('FALSE')).toEqual(['--no-allow-auto']);
    expect(error({ ...LOCAL, ALLOW_AUTO: 'yes' })).toContain('ALLOW_AUTO');
  });

  test.each([
    [{ SHAREPOINT_CLIENT_ID: 'not-a-guid' }, 'SHAREPOINT_CLIENT_ID'],
    [{ SHAREPOINT_TENANT_ID: 'bad tenant/../x' }, 'SHAREPOINT_TENANT_ID'],
    [{ SHAREPOINT_SITE_URL: 'http://contoso.sharepoint.com/sites/HR' }, 'SHAREPOINT_SITE_URL'],
    [{ SHAREPOINT_SITE_URL: 'https://elsewhere.example/sites/HR' }, 'SHAREPOINT_SITE_URL'],
    [{ SHAREPOINT_FOLDER: 'Kontrak/../Rahasia' }, 'SHAREPOINT_FOLDER'],
  ])('refuses %p', (override, key) => {
    expect(error({ ...LOCAL, ...SHAREPOINT, ...override })).toContain(key);
  });
});
