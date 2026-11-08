/** A throwaway self-signed RSA certificate built in memory, so no key file is ever kept in the repository. */
import { type KeyObject, generateKeyPairSync, sign } from 'node:crypto';

function length(n: number): Buffer {
  if (n < 0x80) return Buffer.from([n]);
  const bytes: number[] = [];
  for (let rest = n; rest > 0; rest >>= 8) bytes.unshift(rest & 0xff);
  return Buffer.from([0x80 | bytes.length, ...bytes]);
}

const der = (tag: number, ...parts: Buffer[]) => {
  const body = Buffer.concat(parts);
  return Buffer.concat([Buffer.from([tag]), length(body.length), body]);
};
const seq = (...parts: Buffer[]) => der(0x30, ...parts);

function oid(dotted: string): Buffer {
  const [a = 0, b = 0, ...rest] = dotted.split('.').map(Number);
  const out = [40 * a + b];
  for (const value of rest) {
    const chunk = [value & 0x7f];
    for (let v = value >> 7; v > 0; v >>= 7) chunk.unshift(0x80 | (v & 0x7f));
    out.push(...chunk);
  }
  return der(0x06, Buffer.from(out));
}

const utcTime = (d: Date) => der(0x17, Buffer.from(`${d.toISOString().replace(/[-:T]/g, '').slice(2, 14)}Z`));

export interface TestCertificate {
  pem: string; // private key, then certificate
  der: Buffer;
  publicKey: KeyObject;
}

export function testCertificate(from = new Date(Date.now() - 60_000), to = new Date(Date.now() + 30 * 86_400_000)): TestCertificate {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
  const sha256WithRsa = seq(oid('1.2.840.113549.1.1.11'), der(0x05));
  const name = seq(der(0x31, seq(oid('2.5.4.3'), der(0x0c, Buffer.from('contract-ocr-test')))));
  const tbs = seq(
    der(0xa0, der(0x02, Buffer.from([2]))), // version 3
    der(0x02, Buffer.from([1])), // serial
    sha256WithRsa,
    name,
    seq(utcTime(from), utcTime(to)),
    name,
    publicKey.export({ type: 'spki', format: 'der' }),
  );
  const certificate = seq(tbs, sha256WithRsa, der(0x03, Buffer.from([0]), sign('sha256', tbs, privateKey)));
  const body = certificate.toString('base64').match(/.{1,64}/g)?.join('\n');
  const pem = `${privateKey.export({ type: 'pkcs8', format: 'pem' })}-----BEGIN CERTIFICATE-----\n${body}\n-----END CERTIFICATE-----\n`;
  return { pem, der: certificate, publicKey };
}
