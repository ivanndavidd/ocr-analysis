import { createApp } from './app.ts';
import { ConfigError, loadConfig } from './config.ts';
import { SessionStore } from './modules/auth/sessions.ts';
import { type OpenDatabase, openDatabase } from './modules/database/database.ts';
import { UserStore } from './modules/users/store.ts';
import { WorkerPool } from './modules/workers/worker-pool.ts';
import { GraphError } from './modules/folders/graph.ts';
import { SharePointFolderSource } from './modules/folders/sharepoint.ts';
import { LocalFolderSource } from './modules/folders/source.ts';
import { DatabaseArchive } from './modules/jobs/archive.ts';
import { JobService } from './modules/jobs/service.ts';
import { processWorkbookWriter } from './modules/jobs/workbook.ts';

const EXPORT_TIMEOUT_MS = 120_000;

let config;
try {
  config = loadConfig();
} catch (error) {
  if (error instanceof ConfigError) {
    console.error(`config: ${error.message} (see apps/backend/.env.example)`);
    process.exit(1);
  }
  throw error;
}

let database: OpenDatabase;
try {
  database = await openDatabase(config.database);
} catch (error) {
  // the name and code only: a driver's message may echo the connection string
  const code = error instanceof Error && 'code' in error ? ` ${String(error.code)}` : '';
  console.error(`database: could not open or migrate (${error instanceof Error ? error.name : 'unknown'}${code}); check DATABASE_URL`);
  process.exit(1);
}
const users = new UserStore(database.db);

const source =
  config.source.kind === 'sharepoint'
    ? new SharePointFolderSource(config.source.sharepoint)
    : new LocalFolderSource(config.source.root);
// Tesseract and text patterns, with Claude helping (`--claude`) when it is configured; a worker that cannot reach
// Claude keeps reading without it. With CLAUDE_MODE=off nothing leaves the machine, so the workers get no key.
const pool = new WorkerPool({
  cmd: config.extractorCmd,
  size: config.workers,
  timeoutMs: config.claude?.timeoutMs ?? config.fileTimeoutMs,
  recycleAfter: config.recycleAfter,
  ...(config.claude ? {} : { env: { ANTHROPIC_API_KEY: '' } }),
});
pool.start();
// every run and its files are saved to the database as they go (modules/jobs/archive.ts); runs this backend left
// queued or running when it stopped cannot go on, so they are marked interrupted
const archive = new DatabaseArchive(database.db);
const interrupted = await archive.interruptUnfinished();
if (interrupted) console.info(`${interrupted} run(s) left unfinished by the last backend marked interrupted`);
const jobs = new JobService(source, pool, config.maxFileBytes, config.workers, archive);

const workbook = processWorkbookWriter(config.workbookCmd, EXPORT_TIMEOUT_MS);

createApp({
  allowedHosts: config.allowedHosts,
  source,
  jobs,
  extractor: pool,
  claude: config.claude !== null,
  workbook,
  users,
  sessions: new SessionStore(),
}).listen({
  hostname: config.host,
  port: config.port,
});
const where = config.source.kind === 'sharepoint' ? 'SharePoint' : 'local folder';
console.info(`Contract OCR API on http://${config.host}:${config.port} (${where}: ${source.rootName}, workers: ${config.workers})`);
const db = config.database.kind === 'postgres' ? 'PostgreSQL (DATABASE_URL)' : `embedded PostgreSQL in ${config.database.dir}`;
console.info(`Database (users, OCR runs and results): ${db}; mock sign-in (any email, no password; replace with Apps Gateway)`);
if (config.claude) {
  const auth =
    config.claude.auth === 'api_key' ? 'Claude API with an API key' : "the Claude Code login of this computer's user (testing only)";
  console.info(`Claude helps the patterns: ${auth}, model ${config.claude.model}, effort ${config.claude.effort}`);
} else {
  console.info('Claude: off (CLAUDE_MODE=off); nothing leaves this computer');
}
if (config.source.kind === 'sharepoint' && 'certificate' in config.source.sharepoint.credential) {
  const days = Math.floor((config.source.sharepoint.credential.certificate.expires.getTime() - Date.now()) / 86_400_000);
  if (days < 30) console.warn(`SharePoint: the app certificate expires in ${days} day(s); upload a new one to the app in Entra ID`);
}
if (source instanceof SharePointFolderSource) {
  // Not fatal: SharePoint may be briefly unreachable, and every request tries again.
  source.check().then(
    () => console.info('SharePoint: folder reachable'),
    (error: unknown) => {
      const reason = error instanceof GraphError ? `Microsoft Graph answered ${error.status || 'unexpectedly'}` : 'folder not found';
      console.error(`SharePoint: ${reason}; check the SHAREPOINT_* settings and the app's permission on the site`);
    },
  );
}

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, async () => {
    await pool.close();
    await database.close();
    process.exit(0);
  });
}
