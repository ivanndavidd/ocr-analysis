/** A small Microsoft Graph client: app-only token (client credentials), JSON GETs with paging, and file downloads. */

import { X509Certificate, type KeyObject, constants, createHash, createPrivateKey, randomUUID, sign } from 'node:crypto';

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** An Entra ID app certificate: the private key stays here; Entra holds only the public certificate. */
export interface ClientCertificate {
  key: KeyObject;
  thumbprint: string; // base64url SHA-256 of the certificate, Entra's `x5t#S256`
  expires: Date;
}

/** How the app proves who it is: a certificate (preferred), or a client secret. */
export type ClientCredential = { certificate: ClientCertificate } | { secret: string };

export interface GraphCredentials {
  tenantId: string;
  clientId: string;
  credential: ClientCredential;
}

const PEM_CERTIFICATE = /-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/;
const PEM_KEY = /-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA )?PRIVATE KEY-----/;

/** Read a PEM holding the certificate and its unencrypted RSA private key. Messages never quote the PEM. */
export function clientCertificate(pem: string, now: Date = new Date()): ClientCertificate {
  const certificate = PEM_CERTIFICATE.exec(pem);
  const key = PEM_KEY.exec(pem);
  if (!certificate || !key) throw new Error('needs the certificate and its unencrypted private key in one PEM');
  let x509: X509Certificate;
  let privateKey: KeyObject;
  try {
    x509 = new X509Certificate(certificate[0]);
    privateKey = createPrivateKey(key[0]);
  } catch {
    throw new Error('the certificate or private key could not be read');
  }
  if (privateKey.asymmetricKeyType !== 'rsa') throw new Error('must be an RSA certificate');
  if (!x509.checkPrivateKey(privateKey)) throw new Error('the private key does not belong to the certificate');
  const expires = new Date(x509.validTo);
  if (expires <= now) throw new Error('the certificate has expired');
  return { key: privateKey, thumbprint: createHash('sha256').update(x509.raw).digest('base64url'), expires };
}

const tokenUrl = (tenantId: string) => `https://login.microsoftonline.com/${encodeURIComponent(tenantId)}/oauth2/v2.0/token`;
const base64url = (data: string | Buffer) => Buffer.from(data).toString('base64url');

/** A client assertion: a JWT signed with the certificate's key, valid for ten minutes and for this tenant only. */
export function clientAssertion(certificate: ClientCertificate, tenantId: string, clientId: string, nowMs: number): string {
  const now = Math.floor(nowMs / 1000);
  const header = { alg: 'PS256', typ: 'JWT', 'x5t#S256': certificate.thumbprint };
  const claims = { aud: tokenUrl(tenantId), iss: clientId, sub: clientId, jti: randomUUID(), iat: now, nbf: now, exp: now + 600 };
  const input = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const signature = sign('sha256', Buffer.from(input), {
    key: certificate.key,
    padding: constants.RSA_PKCS1_PSS_PADDING,
    saltLength: constants.RSA_PSS_SALTLEN_DIGEST,
  });
  return `${input}.${base64url(signature)}`;
}

/** Graph refused or failed; `status` is the HTTP status (0: no response). The message never holds a token or a path. */
export class GraphError extends Error {
  constructor(readonly status: number) {
    super(`graph status ${status}`);
    this.name = 'GraphError';
  }
}

const GRAPH = 'https://graph.microsoft.com/v1.0';
const TOKEN_MARGIN_MS = 5 * 60_000; // renew a token this long before it expires
const CALL_TIMEOUT_MS = 30_000;
const DOWNLOAD_TIMEOUT_MS = 120_000;
const MAX_RETRIES = 3; // on 429 and 503, Graph's throttling answers
const MAX_RETRY_WAIT_MS = 30_000;

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

export class GraphClient {
  private token: { value: string; expires: number } | null = null;
  private pending: Promise<string> | null = null;

  constructor(
    private readonly credentials: GraphCredentials,
    private readonly fetchFn: Fetch = fetch,
    private readonly now: () => number = Date.now,
  ) {}

  private async newToken(): Promise<string> {
    const { tenantId, clientId, credential } = this.credentials;
    const proof: Record<string, string> =
      'certificate' in credential
        ? {
            client_assertion_type: 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
            client_assertion: clientAssertion(credential.certificate, tenantId, clientId, this.now()),
          }
        : { client_secret: credential.secret };
    const response = await this.fetchFn(tokenUrl(tenantId), {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: clientId,
        scope: 'https://graph.microsoft.com/.default',
        ...proof,
      }),
      signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
    });
    if (!response.ok) throw new GraphError(response.status); // the body may echo the client id: not kept
    const body = (await response.json()) as { access_token?: string; expires_in?: number };
    if (!body.access_token) throw new GraphError(response.status);
    this.token = { value: body.access_token, expires: this.now() + (body.expires_in ?? 3600) * 1000 };
    return body.access_token;
  }

  /** A valid token; parallel callers share one request. */
  private accessToken(): Promise<string> {
    if (this.token && this.token.expires - TOKEN_MARGIN_MS > this.now()) return Promise.resolve(this.token.value);
    this.pending ??= this.newToken().finally(() => {
      this.pending = null;
    });
    return this.pending;
  }

  /** GET a Graph URL with the token: one new token on 401, and waits on throttling. 404 resolves to null. */
  private async call(url: string): Promise<Response | null> {
    let renewed = false;
    for (let attempt = 0; ; attempt++) {
      const response = await this.fetchFn(url, {
        headers: { authorization: `Bearer ${await this.accessToken()}`, accept: 'application/json' },
        signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
      });
      if (response.ok) return response;
      if (response.status === 404) return null;
      if (response.status === 401 && !renewed) {
        renewed = true;
        this.token = null;
        continue;
      }
      if ((response.status === 429 || response.status === 503) && attempt < MAX_RETRIES) {
        const seconds = Number(response.headers.get('retry-after'));
        await sleep(Math.min(Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 2 ** attempt * 1000, MAX_RETRY_WAIT_MS));
        continue;
      }
      throw new GraphError(response.status);
    }
  }

  /** GET `path` (below /v1.0) as JSON, or null when Graph answers 404. */
  async get<T>(path: string): Promise<T | null> {
    const response = await this.call(`${GRAPH}${path}`);
    return response ? ((await response.json()) as T) : null;
  }

  /** Every `value` item of a collection, following `@odata.nextLink`; null when Graph answers 404. */
  async all<T>(path: string): Promise<T[] | null> {
    const items: T[] = [];
    let url: string | undefined = `${GRAPH}${path}`;
    while (url) {
      const response = await this.call(url);
      if (!response) return null;
      const page = (await response.json()) as { value?: T[]; '@odata.nextLink'?: string };
      items.push(...(page.value ?? []));
      url = page['@odata.nextLink'];
      if (url && !url.startsWith(`${GRAPH}/`)) throw new GraphError(0); // the token only ever goes to Graph
    }
    return items;
  }

  /** Download a pre-authenticated SharePoint URL (an item's `@microsoft.graph.downloadUrl`), at most `maxBytes`.
   *  It carries its own short-lived authorisation, so the Graph token is not sent. */
  async download(url: string, maxBytes: number): Promise<Uint8Array | null> {
    const response = await this.downloadResponse(url);
    const reader = response.body?.getReader();
    if (!reader) return new Uint8Array(0);
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return null; // larger than the file said it was
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  }

  /** The download response, to stream to the browser. */
  async downloadResponse(url: string): Promise<Response> {
    const target = new URL(url);
    if (target.protocol !== 'https:' || !target.hostname.endsWith('.sharepoint.com')) throw new GraphError(0);
    const response = await this.fetchFn(url, { signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS) });
    if (!response.ok) throw new GraphError(response.status);
    return response;
  }
}
