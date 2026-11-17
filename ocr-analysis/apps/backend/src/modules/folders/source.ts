import { lstat, readdir, readFile, realpath } from 'node:fs/promises';
import { isAbsolute, join, relative, sep } from 'node:path';
import type { FolderListing } from '@contract-ocr/shared-types';

/** A PDF for the in-app preview: a file to stream, or a download already under way. */
export type PdfBody = Blob | ReadableStream<Uint8Array> | Uint8Array;

/** Where contracts come from: a local folder, or SharePoint through Microsoft Graph. Paths are relative and '/'-separated. */
export interface FolderSource {
  readonly rootName: string;
  list(path: string): Promise<FolderListing>;
  /** Root-relative paths of the PDFs in these folders and their subfolders, without duplicates. Stops once more than
   *  `limit` are found, so the caller can refuse a selection that is too large without walking all of it. */
  pdfPaths(folders: string[], limit: number): Promise<string[]>;
  /** The subfolders of a folder, each with the PDFs its listing counts. */
  subfolderPdfs(path: string): Promise<SubfolderPdfs[]>;
  /** The same PDF files as normalised root-relative paths; each must be a PDF file inside the root. */
  pdfFiles(files: string[]): Promise<string[]>;
  readPdf(path: string, maxBytes: number): Promise<Uint8Array>;
  /** The PDF as a response body for the in-app preview. */
  pdfBody(path: string): Promise<PdfBody>;
}

/** A subfolder and the root-relative paths of the PDFs in it through its own subfolders; `complete` is false when
 *  there are more than a listing counts, so only some are there. */
export interface SubfolderPdfs {
  name: string;
  path: string;
  pdfs: string[];
  complete: boolean;
}

/** A request path that is malformed, escapes the root, or names something that is not there. */
export class SourceError extends Error {
  constructor(readonly code: 'bad_path' | 'not_found' | 'not_pdf' | 'too_large') {
    super(code);
  }
}

/** One folder entry. A link, or anything else that is neither a plain file nor a folder, is 'other' and never followed. */
export interface Entry {
  name: string;
  kind: 'folder' | 'file' | 'other';
}

/** The entries of a root-relative folder; an unreadable folder may resolve to none. */
export type Children = (parts: string[]) => Promise<Entry[]>;

const MAX_PATH_CHARS = 1000;
const MAX_DEPTH = 5; // subfolder levels searched below a folder: vendor / period needs 2
const COUNT_LIMIT = 5000; // a folder's PDF count stops here
export const isPdf = (name: string) => name.toLowerCase().endsWith('.pdf');
const isHidden = (name: string) => name.startsWith('.') || name.startsWith('~$') || name.toLowerCase() === 'desktop.ini';
const byName = (a: string, b: string) => a.localeCompare(b, 'id', { numeric: true, sensitivity: 'base' });

/** Normalise a client path into segments, refusing anything that could step outside the root. */
export function segments(path: string): string[] {
  if (path.length > MAX_PATH_CHARS || path.includes('\0')) throw new SourceError('bad_path');
  const parts = path.split(/[\\/]+/).filter(Boolean);
  if (parts.some((p) => p === '.' || p === '..' || p.includes(':'))) throw new SourceError('bad_path');
  return parts;
}

/** Collect PDFs below the folder `rel` in name order. */
export async function walk(children: Children, rel: string[], depth: number, found: string[], limit: number): Promise<void> {
  const entries = [...(await children(rel))];
  // a folder's own PDFs first, then its subfolders, so each folder's files stay together in the job
  entries.sort((a, b) => Number(a.kind === 'folder') - Number(b.kind === 'folder') || byName(a.name, b.name));
  for (const entry of entries) {
    if (found.length > limit) return;
    if (isHidden(entry.name)) continue;
    if (entry.kind === 'file' && isPdf(entry.name)) found.push([...rel, entry.name].join('/'));
    else if (entry.kind === 'folder' && depth < MAX_DEPTH) await walk(children, [...rel, entry.name], depth + 1, found, limit);
  }
}

/** The subfolders of folder `parts` from its entries, in name order, each with its PDFs through its own subfolders. */
export function subfolders(children: Children, parts: string[], entries: Entry[]): Promise<SubfolderPdfs[]> {
  const names = entries.filter((e) => e.kind === 'folder' && !isHidden(e.name)).map((e) => e.name).sort(byName);
  return Promise.all(
    names.map(async (name) => {
      const found: string[] = [];
      await walk(children, [...parts, name], 1, found, COUNT_LIMIT);
      return { name, path: [...parts, name].join('/'), pdfs: found.slice(0, COUNT_LIMIT), complete: found.length <= COUNT_LIMIT };
    }),
  );
}

/** The listing of folder `parts` from its entries: subfolders with PDF counts through their own subfolders, then PDFs. */
export async function listing(children: Children, parts: string[], entries: Entry[]): Promise<FolderListing> {
  const pdfs = entries.filter((e) => e.kind === 'file' && isPdf(e.name) && !isHidden(e.name)).map((e) => e.name).sort(byName);
  return {
    path: parts.join('/'),
    crumbs: parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') })),
    folders: (await subfolders(children, parts, entries)).map(({ name, path, pdfs }) => ({ name, path, pdfCount: pdfs.length })),
    pdfs,
  };
}

/** Links and junctions are not followed: a Dirent for a link is neither a file nor a directory. */
async function localEntries(abs: string): Promise<Entry[]> {
  const entries = await readdir(abs, { withFileTypes: true });
  return entries.map((e) => ({ name: e.name, kind: e.isDirectory() ? 'folder' : e.isFile() ? 'file' : 'other' }));
}

export class LocalFolderSource implements FolderSource {
  readonly rootName: string;
  private realRoot: Promise<string>;

  constructor(private readonly root: string) {
    this.rootName = root.split(/[\\/]/).filter(Boolean).pop() ?? root;
    this.realRoot = realpath(root);
  }

  /** Subfolders of a folder already resolved inside the root; an unreadable one is skipped rather than failing a whole selection. */
  private children: Children = (parts) => localEntries(join(this.root, ...parts)).catch(() => []);

  /** Resolve inside the root, following links only if they still land inside it. */
  private async resolve(path: string): Promise<string> {
    const target = join(this.root, ...segments(path));
    let real: string;
    try {
      real = await realpath(target);
    } catch {
      throw new SourceError('not_found');
    }
    const root = await this.realRoot;
    const rel = relative(root, real);
    if (rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) {
      throw new SourceError('bad_path');
    }
    return real;
  }

  /** The entries of a folder inside the root. */
  private async folderEntries(path: string): Promise<Entry[]> {
    const abs = await this.resolve(path);
    try {
      return await localEntries(abs);
    } catch {
      throw new SourceError('not_found');
    }
  }

  async list(path: string): Promise<FolderListing> {
    return listing(this.children, segments(path), await this.folderEntries(path));
  }

  async subfolderPdfs(path: string): Promise<SubfolderPdfs[]> {
    return subfolders(this.children, segments(path), await this.folderEntries(path));
  }

  async pdfPaths(folders: string[], limit: number): Promise<string[]> {
    const found: string[] = [];
    for (const folder of folders) {
      const abs = await this.resolve(folder);
      if (!(await lstat(abs)).isDirectory()) throw new SourceError('not_found');
      await walk(this.children, segments(folder), 0, found, limit);
      if (found.length > limit) break;
    }
    return [...new Set(found)]; // a folder selected together with its parent lists each file once
  }

  async pdfFiles(files: string[]): Promise<string[]> {
    const found: string[] = [];
    for (const file of files) {
      await this.pdfFile(file);
      found.push(segments(file).join('/'));
    }
    return [...new Set(found)];
  }

  /** Absolute path of a PDF file inside the root. */
  async pdfFile(path: string): Promise<string> {
    if (!isPdf(path)) throw new SourceError('not_pdf');
    const abs = await this.resolve(path);
    const info = await lstat(abs);
    if (!info.isFile()) throw new SourceError('not_found');
    return abs;
  }

  async readPdf(path: string, maxBytes: number): Promise<Uint8Array> {
    const abs = await this.pdfFile(path);
    if ((await lstat(abs)).size > maxBytes) throw new SourceError('too_large');
    return new Uint8Array(await readFile(abs));
  }

  async pdfBody(path: string): Promise<PdfBody> {
    return Bun.file(await this.pdfFile(path));
  }
}
