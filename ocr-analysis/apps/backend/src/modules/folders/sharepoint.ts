import type { FolderListing } from '@contract-ocr/shared-types';
import { type ClientCredential, type Fetch, GraphClient, GraphError } from './graph.ts';
import {
  type Children,
  type Entry,
  type FolderSource,
  type PdfBody,
  SourceError,
  type SubfolderPdfs,
  isPdf,
  listing,
  segments,
  subfolders,
  walk,
} from './source.ts';

/** A SharePoint document library read through Microsoft Graph with an Entra ID app's own (app-only) access. */
export interface SharePointConfig {
  tenantId: string;
  clientId: string;
  credential: ClientCredential;
  siteUrl: string; // https://<company>.sharepoint.com/sites/<site>
  library: string; // document library name; '' = the site's default library
  folder: string; // folder in the library the website may browse; '' = the whole library
}

interface DriveItem {
  name: string;
  size?: number;
  folder?: object;
  file?: object;
  '@microsoft.graph.downloadUrl'?: string;
}

const ITEM_FIELDS = 'name,size,folder,file';
const PAGE_SIZE = 200;

const encodePath = (parts: string[]) => parts.map(encodeURIComponent).join('/');
const kind = (item: DriveItem): Entry['kind'] => (item.folder ? 'folder' : item.file ? 'file' : 'other');

export class SharePointFolderSource implements FolderSource {
  readonly rootName: string;
  private readonly graph: GraphClient;
  private readonly root: string[];
  private drive: Promise<string> | null = null;

  constructor(
    private readonly config: SharePointConfig,
    fetchFn?: Fetch,
  ) {
    this.graph = new GraphClient(config, fetchFn);
    this.root = segments(config.folder);
    const site = new URL(config.siteUrl).pathname.split('/').filter(Boolean).pop();
    this.rootName = this.root.at(-1) ?? (config.library || site || 'SharePoint');
  }

  /** The library's drive id, looked up once; a failed lookup is tried again on the next request. */
  private driveId(): Promise<string> {
    this.drive ??= this.findDrive().catch((error: unknown) => {
      this.drive = null;
      throw error;
    });
    return this.drive;
  }

  private async findDrive(): Promise<string> {
    const url = new URL(this.config.siteUrl);
    const sitePath = url.pathname.split('/').filter(Boolean);
    const site = await this.graph.get<{ id: string }>(
      `/sites/${url.hostname}${sitePath.length ? `:/${encodePath(sitePath)}` : ''}?$select=id`,
    );
    if (!site) throw new GraphError(404);
    if (!this.config.library) {
      const drive = await this.graph.get<{ id: string }>(`/sites/${site.id}/drive?$select=id`);
      if (!drive) throw new GraphError(404);
      return drive.id;
    }
    const drives = (await this.graph.all<{ id: string; name: string }>(`/sites/${site.id}/drives?$select=id,name`)) ?? [];
    const wanted = this.config.library.toLowerCase();
    const drive = drives.find((d) => d.name.toLowerCase() === wanted);
    if (!drive) throw new GraphError(404);
    return drive.id;
  }

  /** Graph address of a root-relative item, inside the configured folder. */
  private async address(parts: string[]): Promise<string> {
    const full = [...this.root, ...parts];
    const drive = await this.driveId();
    return full.length ? `/drives/${drive}/root:/${encodePath(full)}:` : `/drives/${drive}/root`;
  }

  private async item(parts: string[], fields = ITEM_FIELDS): Promise<DriveItem | null> {
    return this.graph.get<DriveItem>(`${await this.address(parts)}?$select=${fields}`);
  }

  private async entries(parts: string[]): Promise<Entry[] | null> {
    const items = await this.graph.all<DriveItem>(`${await this.address(parts)}/children?$select=${ITEM_FIELDS}&$top=${PAGE_SIZE}`);
    return items && items.map((item) => ({ name: item.name, kind: kind(item) }));
  }

  /** A subfolder removed while it is being walked has no entries; any other Graph failure fails the request, so a
   *  job never silently misses a folder. */
  private children: Children = async (parts) => (await this.entries(parts)) ?? [];

  /** Throws unless the configured folder can be read: for a startup check of the settings and the app's permission. */
  async check(): Promise<void> {
    if (!(await this.item([]))?.folder) throw new SourceError('not_found');
  }

  /** The entries of a folder inside the configured one. */
  private async folderEntries(parts: string[]): Promise<Entry[]> {
    const [item, entries] = await Promise.all([this.item(parts), this.entries(parts)]);
    if (!item?.folder || !entries) throw new SourceError('not_found');
    return entries;
  }

  async list(path: string): Promise<FolderListing> {
    const parts = segments(path);
    return listing(this.children, parts, await this.folderEntries(parts));
  }

  async subfolderPdfs(path: string): Promise<SubfolderPdfs[]> {
    const parts = segments(path);
    return subfolders(this.children, parts, await this.folderEntries(parts));
  }

  async pdfPaths(folders: string[], limit: number): Promise<string[]> {
    const found: string[] = [];
    for (const folder of folders) {
      const parts = segments(folder);
      if (!(await this.item(parts))?.folder) throw new SourceError('not_found');
      await walk(this.children, parts, 0, found, limit);
      if (found.length > limit) break;
    }
    return [...new Set(found)]; // a folder selected together with its parent lists each file once
  }

  async pdfFiles(files: string[]): Promise<string[]> {
    const found: string[] = [];
    for (const file of files) {
      await this.pdfItem(file);
      found.push(segments(file).join('/'));
    }
    return [...new Set(found)];
  }

  /** A PDF file inside the folder, with its size and a short-lived download URL. */
  private async pdfItem(path: string): Promise<{ size: number; url: string }> {
    if (!isPdf(path)) throw new SourceError('not_pdf');
    const item = await this.item(segments(path), `${ITEM_FIELDS},@microsoft.graph.downloadUrl`);
    if (!item?.file) throw new SourceError('not_found');
    const url = item['@microsoft.graph.downloadUrl'];
    if (!url) throw new GraphError(0);
    return { size: item.size ?? 0, url };
  }

  async readPdf(path: string, maxBytes: number): Promise<Uint8Array> {
    const { size, url } = await this.pdfItem(path);
    if (size > maxBytes) throw new SourceError('too_large');
    const bytes = await this.graph.download(url, maxBytes);
    if (!bytes) throw new SourceError('too_large');
    return bytes;
  }

  async pdfBody(path: string): Promise<PdfBody> {
    const response = await this.graph.downloadResponse((await this.pdfItem(path)).url);
    return response.body ?? new Uint8Array(0);
  }
}
