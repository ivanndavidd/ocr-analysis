import { describe, expect, test } from 'bun:test';
import { constants, createHash, verify } from 'node:crypto';
import { GraphClient, GraphError, clientCertificate } from '../src/modules/folders/graph.ts';
import { type SharePointConfig, SharePointFolderSource } from '../src/modules/folders/sharepoint.ts';
import { SourceError } from '../src/modules/folders/source.ts';
import { testCertificate } from './fixtures/certificate.ts';

/** A fake Microsoft Graph and SharePoint over an in-memory library. Names and contents are synthetic. */
interface Node {
  name: string;
  children?: Node[];
  data?: string;
}

const LIBRARY: Node = {
  name: '',
  children: [
    {
      name: 'Kontrak HR',
      children: [
        {
          name: 'PT Bina Semesta',
          children: [
            {
              name: 'Januari 2041 - Maret 2041',
              children: [
                { name: 'Budi Santoso - 1jan-31mar41.pdf', data: '%PDF-1.4 a' },
                { name: 'Andi - 1 Jan - 31 Mar 2041.PDF', data: '%PDF-1.4 b' },
                { name: 'catatan.txt', data: 'not a pdf' },
              ],
            },
            { name: 'Kosong', children: [] },
          ],
        },
      ],
    },
    { name: 'Rahasia', children: [{ name: 'secret.pdf', data: '%PDF-1.4 secret' }] },
  ],
};

const CONFIG: SharePointConfig = {
  tenantId: '11111111-1111-1111-1111-111111111111',
  clientId: '22222222-2222-2222-2222-222222222222',
  credential: { secret: 'test-secret' },
  siteUrl: 'https://contoso.sharepoint.com/sites/HR',
  library: 'Dokumen Kontrak',
  folder: 'Kontrak HR',
};

const GRAPH = 'https://graph.microsoft.com/v1.0';
const PAGE = 2; // small pages, so listings go through @odata.nextLink

interface Call {
  url: string;
  auth: string | null;
}

function fakeGraph(options: { status?: (url: string) => number | undefined; downloadHost?: string } = {}) {
  const calls: Call[] = [];
  let tokens = 0;
  const find = (parts: string[]) => {
    let node: Node | undefined = LIBRARY;
    for (const part of parts) node = node?.children?.find((c) => c.name === part);
    return node;
  };
  const item = (node: Node, parts: string[]) => ({
    name: node.name,
    ...(node.children ? { folder: { childCount: node.children.length } } : { file: {}, size: node.data?.length ?? 0 }),
    ...(node.data
      ? { '@microsoft.graph.downloadUrl': `https://${options.downloadHost ?? 'contoso.sharepoint.com'}/dl/${parts.map(encodeURIComponent).join('/')}` }
      : {}),
  });
  const fetchFn = async (input: string, init?: RequestInit): Promise<Response> => {
    const auth = new Headers(init?.headers).get('authorization');
    calls.push({ url: input, auth });
    const url = new URL(input);
    if (url.hostname === 'login.microsoftonline.com') {
      tokens += 1;
      return Response.json({ access_token: `token-${tokens}`, expires_in: 3600 });
    }
    const forced = options.status?.(input);
    if (forced) return new Response(null, { status: forced, headers: { 'retry-after': '0.01' } });
    if (url.hostname.endsWith('.sharepoint.com')) {
      const node = find(url.pathname.split('/').slice(2).map(decodeURIComponent));
      return node?.data ? new Response(node.data) : new Response(null, { status: 404 });
    }
    if (auth !== `Bearer token-${tokens}`) return new Response(null, { status: 401 });
    const path = url.pathname.replace('/v1.0', '');
    if (path === '/sites/contoso.sharepoint.com:/sites/HR') return Response.json({ id: 'site-1' });
    if (path === '/sites/site-1/drives') return Response.json({ value: [{ id: 'drive-1', name: 'Dokumen Kontrak' }] });
    if (path === '/sites/site-1/drive') return Response.json({ id: 'drive-1' });
    const match = /^\/drives\/drive-1\/root(?::\/(.+):)?(\/children)?$/.exec(path);
    if (!match) return new Response(null, { status: 404 });
    const parts = match[1] ? match[1].split('/').map(decodeURIComponent) : [];
    const node = find(parts);
    if (!node) return new Response(null, { status: 404 });
    if (!match[2]) return Response.json(item(node, parts));
    const skip = Number(url.searchParams.get('$skiptoken') ?? 0);
    const value = (node.children ?? []).slice(skip, skip + PAGE).map((c) => item(c, [...parts, c.name]));
    const next = skip + PAGE < (node.children?.length ?? 0) ? `${GRAPH}${path}?$skiptoken=${skip + PAGE}` : undefined;
    return Response.json({ value, ...(next ? { '@odata.nextLink': next } : {}) });
  };
  return { fetchFn, calls, tokenCount: () => tokens };
}

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof SourceError) return error.code;
    if (error instanceof GraphError) return `graph ${error.status}`;
    throw error;
  }
  return 'no error';
}

const PERIOD = 'PT Bina Semesta/Januari 2041 - Maret 2041';
const BUDI = `${PERIOD}/Budi Santoso - 1jan-31mar41.pdf`;

describe('SharePointFolderSource', () => {
  test('lists the configured folder only, with PDF counts through subfolders', async () => {
    const source = new SharePointFolderSource(CONFIG, fakeGraph().fetchFn);
    expect(source.rootName).toBe('Kontrak HR');
    const root = await source.list('');
    expect(root.folders).toEqual([{ name: 'PT Bina Semesta', path: 'PT Bina Semesta', pdfCount: 2 }]); // no Rahasia
    const vendor = await source.list('PT Bina Semesta');
    expect(vendor.crumbs).toEqual([{ name: 'PT Bina Semesta', path: 'PT Bina Semesta' }]);
    expect(vendor.folders.map((f) => [f.name, f.pdfCount])).toEqual([
      ['Januari 2041 - Maret 2041', 2],
      ['Kosong', 0],
    ]);
    expect((await source.list(PERIOD)).pdfs).toEqual(['Andi - 1 Jan - 31 Mar 2041.PDF', 'Budi Santoso - 1jan-31mar41.pdf']);
    // the folder marks: each subfolder with the PDFs its count is made of
    expect((await source.subfolderPdfs('PT Bina Semesta')).map((f) => [f.path, f.pdfs, f.complete])).toEqual([
      [PERIOD, [`${PERIOD}/Andi - 1 Jan - 31 Mar 2041.PDF`, BUDI], true],
      ['PT Bina Semesta/Kosong', [], true],
    ]);
    expect((await source.subfolderPdfs('')).map((f) => f.path)).toEqual(['PT Bina Semesta']); // no Rahasia
    expect(await code(source.subfolderPdfs('Tidak Ada'))).toBe('not_found');
  });

  test('collects PDFs through subfolders and refuses paths outside the folder', async () => {
    const source = new SharePointFolderSource(CONFIG, fakeGraph().fetchFn);
    expect(await source.pdfPaths(['PT Bina Semesta'], 100)).toEqual([
      `${PERIOD}/Andi - 1 Jan - 31 Mar 2041.PDF`,
      `${PERIOD}/Budi Santoso - 1jan-31mar41.pdf`,
    ]);
    expect(await source.pdfPaths(['', PERIOD], 100)).toHaveLength(2);
    expect(await code(source.pdfPaths([BUDI], 100))).toBe('not_found'); // a file is not a folder
    expect(await code(source.list('../Rahasia'))).toBe('bad_path');
    expect(await code(source.list('Tidak Ada'))).toBe('not_found');
    expect(await code(source.pdfFiles([`${PERIOD}/catatan.txt`]))).toBe('not_pdf');
    expect(await code(source.pdfFiles([`${PERIOD}/Siapa.pdf`]))).toBe('not_found');
    expect(await source.pdfFiles([BUDI, BUDI.replaceAll('/', '\\')])).toEqual([BUDI]);
  });

  test('downloads a PDF without sending the Graph token, within the size limit', async () => {
    const graph = fakeGraph();
    const source = new SharePointFolderSource(CONFIG, graph.fetchFn);
    expect(new TextDecoder().decode(await source.readPdf(BUDI, 1024))).toBe('%PDF-1.4 a');
    expect(await code(source.readPdf(BUDI, 3))).toBe('too_large');
    const download = graph.calls.find((c) => new URL(c.url).hostname === 'contoso.sharepoint.com');
    expect(download?.auth).toBeNull();
    expect(graph.calls.filter((c) => c.url.startsWith(GRAPH)).every((c) => c.auth?.startsWith('Bearer '))).toBe(true);
    expect(await new Response(await source.pdfBody(BUDI)).text()).toBe('%PDF-1.4 a');
  });

  test('uses the site default library when none is named', async () => {
    const source = new SharePointFolderSource({ ...CONFIG, library: '', folder: '' }, fakeGraph().fetchFn);
    expect(source.rootName).toBe('HR');
    expect((await source.list('')).folders.map((f) => f.name)).toEqual(['Kontrak HR', 'Rahasia']);
  });

  test('a wrong library or a refused permission fails, and is tried again later', async () => {
    const missing = new SharePointFolderSource({ ...CONFIG, library: 'Tidak Ada' }, fakeGraph().fetchFn);
    expect(await code(missing.check())).toBe('graph 404');
    expect(await code(missing.check())).toBe('graph 404');
    const refused = new SharePointFolderSource(CONFIG, fakeGraph({ status: (url) => (url.includes('/sites/') ? 403 : undefined) }).fetchFn);
    expect(await code(refused.list(''))).toBe('graph 403');
  });
});

describe('GraphClient', () => {
  test('reuses its token until it expires, and renews it once when Graph refuses it', async () => {
    const graph = fakeGraph(); // accepts only the newest token
    const first = new GraphClient(CONFIG, graph.fetchFn);
    await first.get('/sites/site-1/drive');
    await first.get('/sites/site-1/drive');
    expect(graph.tokenCount()).toBe(1);
    await new GraphClient(CONFIG, graph.fetchFn).get('/sites/site-1/drive');
    expect(await first.get('/sites/site-1/drive')).toEqual({ id: 'drive-1' }); // 401, new token, same call again
    expect(graph.tokenCount()).toBe(3);
    let now = Date.now();
    const clock = new GraphClient(CONFIG, graph.fetchFn, () => now);
    await clock.get('/sites/site-1/drive');
    now += 3600_000;
    await clock.get('/sites/site-1/drive');
    expect(graph.tokenCount()).toBe(5);
  });

  test('waits and tries again when Graph throttles', async () => {
    let throttled = 2;
    const graph = fakeGraph({ status: () => (throttled-- > 0 ? 429 : undefined) });
    expect(await new GraphClient(CONFIG, graph.fetchFn).get<{ id: string }>('/sites/site-1/drive')).toEqual({ id: 'drive-1' });
  });

  test('never follows a next link or a download URL off Microsoft hosts', async () => {
    const offsite = async () => Response.json({ value: [], '@odata.nextLink': 'https://elsewhere.example/steal' });
    const client = new GraphClient(CONFIG, async (url, init) =>
      new URL(url).hostname === 'login.microsoftonline.com' ? Response.json({ access_token: 't', expires_in: 3600 }) : offsite(),
    );
    expect(await code(client.all('/drives/drive-1/root/children'))).toBe('graph 0');
    const source = new SharePointFolderSource(CONFIG, fakeGraph({ downloadHost: 'elsewhere.example' }).fetchFn);
    expect(await code(source.readPdf(BUDI, 1024))).toBe('graph 0');
  });
});

describe('certificate login', () => {
  test('reads the certificate and its key, and refuses an expired, mismatched or incomplete one', () => {
    const cert = testCertificate();
    expect(clientCertificate(cert.pem).thumbprint).toBe(createHash('sha256').update(cert.der).digest('base64url'));
    const expired = testCertificate(new Date(Date.now() - 2 * 86_400_000), new Date(Date.now() - 86_400_000));
    expect(() => clientCertificate(expired.pem)).toThrow('expired');
    const other = testCertificate();
    const mixed = cert.pem.slice(0, cert.pem.indexOf('-----BEGIN CERTIFICATE')) + other.pem.slice(other.pem.indexOf('-----BEGIN CERTIFICATE'));
    expect(() => clientCertificate(mixed)).toThrow('does not belong');
    expect(() => clientCertificate(cert.pem.slice(cert.pem.indexOf('-----BEGIN CERTIFICATE')))).toThrow('private key');
  });

  test('asks for a token with a signed ten-minute assertion, never a secret', async () => {
    const cert = testCertificate();
    const bodies: URLSearchParams[] = [];
    const client = new GraphClient(
      { ...CONFIG, credential: { certificate: clientCertificate(cert.pem) } },
      async (url, init) => {
        if (new URL(url).hostname === 'login.microsoftonline.com') {
          bodies.push(new URLSearchParams(String(init?.body)));
          return Response.json({ access_token: 'token', expires_in: 3600 });
        }
        return Response.json({ id: 'drive-1' });
      },
    );
    await client.get('/sites/site-1/drive');
    const [body] = bodies;
    expect(body?.get('client_secret')).toBeNull();
    expect(body?.get('client_assertion_type')).toBe('urn:ietf:params:oauth:client-assertion-type:jwt-bearer');
    const [header = '', claims = '', signature = ''] = (body?.get('client_assertion') ?? '').split('.');
    const decode = (part: string) => JSON.parse(Buffer.from(part, 'base64url').toString());
    expect(decode(header)).toEqual({ alg: 'PS256', typ: 'JWT', 'x5t#S256': createHash('sha256').update(cert.der).digest('base64url') });
    const payload = decode(claims);
    expect(payload).toMatchObject({
      aud: `https://login.microsoftonline.com/${CONFIG.tenantId}/oauth2/v2.0/token`,
      iss: CONFIG.clientId,
      sub: CONFIG.clientId,
    });
    expect(payload.exp - payload.nbf).toBe(600);
    const signed = verify('sha256', Buffer.from(`${header}.${claims}`), {
      key: cert.publicKey,
      padding: constants.RSA_PKCS1_PSS_PADDING,
      saltLength: constants.RSA_PSS_SALTLEN_DIGEST,
    }, Buffer.from(signature, 'base64url'));
    expect(signed).toBe(true);
  });
});
