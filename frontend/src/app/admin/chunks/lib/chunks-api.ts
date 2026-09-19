import { HTTPError, TimeoutError } from 'ky';
import { http } from '@/lib/http';

export interface DocSummary {
  doc_id: string;
  last_modified: string;
  size_bytes: number;
}

/** Metadata projection of one chunk — everything the grid needs, no text. */
export interface ChunkIndexEntry {
  chunk_id: string;
  /** Position in the document's chunk array; the key the text endpoint takes. */
  pos: number;
  /** The pipeline's own chunk_index metadata (usually equal to pos). */
  idx: number;
  char_count: number;
  heading: string | null;
  subheading: string | null;
  start_page: number | null;
  end_page: number | null;
}

/** An index entry plus the text and reference lists shown in the modal. */
export interface ChunkFull extends ChunkIndexEntry {
  text: string;
  s3_key: string | null;
  statute_refs: string[];
  admin_rule_refs: string[];
  edition_year: number | null;
}

export interface DocMeta {
  doc_id: string;
  title: string | null;
  doc_type: string | null;
  framework_id: string | null;
  authority_level: number | null;
  source_url: string | null;
  chunk_count: number;
  total_chars: number;
  max_chunk_chars: number;
  min_chunk_chars: number;
}

export interface ChunkIndexPage {
  document: DocMeta;
  chunks: ChunkIndexEntry[];
  offset: number;
  limit: number;
  total: number;
  next_offset: number | null;
}

export type ChunksErrorKind =
  | 'forbidden'
  | 'not-found'
  | 'network'
  | 'server'
  | 'unknown';

/** A fetch failure the UI can describe honestly instead of showing "empty". */
export class ChunksError extends Error {
  readonly kind: ChunksErrorKind;
  readonly status?: number;

  constructor(kind: ChunksErrorKind, message: string, status?: number) {
    super(message);
    this.name = 'ChunksError';
    this.kind = kind;
    this.status = status;
  }
}

export function toChunksError(err: unknown): ChunksError {
  if (err instanceof ChunksError) return err;
  if (err instanceof HTTPError) {
    const status = err.response.status;
    if (status === 401 || status === 403) {
      return new ChunksError(
        'forbidden',
        'You are not in the Admins group, so the chunk inspector is not available to you.',
        status
      );
    }
    if (status === 404) {
      return new ChunksError('not-found', 'Not found in the work bucket.', status);
    }
    if (status >= 500) {
      return new ChunksError('server', `The API returned ${status}.`, status);
    }
    return new ChunksError('unknown', `The API returned ${status}.`, status);
  }
  if (err instanceof TimeoutError) {
    return new ChunksError('network', 'The request timed out.');
  }
  if (err instanceof TypeError) {
    return new ChunksError('network', 'Could not reach the API.');
  }
  return new ChunksError('unknown', err instanceof Error ? err.message : String(err));
}

/**
 * The /admin/chunks routes return a real JSON body. Older deploys of the API
 * wrapped every payload in a `{statusCode, body}` envelope where `body` was the
 * JSON-encoded payload; unwrap that if we meet it so a frontend that ships
 * ahead of the backend degrades instead of rendering nothing.
 */
function unwrapEnvelope<T>(response: unknown): T {
  const envelope = response as { statusCode?: unknown; body?: unknown } | null;
  if (
    envelope &&
    typeof envelope === 'object' &&
    envelope.statusCode !== undefined &&
    typeof envelope.body === 'string'
  ) {
    return JSON.parse(envelope.body) as T;
  }
  return response as T;
}

export async function fetchDocuments(signal?: AbortSignal): Promise<DocSummary[]> {
  try {
    const response = await http.get('admin/chunks/documents', { signal }).json<unknown>();
    const data = unwrapEnvelope<{ documents?: DocSummary[] }>(response);
    if (!Array.isArray(data?.documents)) {
      throw new ChunksError('unknown', 'Response did not contain a documents array.');
    }
    return data.documents;
  } catch (err) {
    throw toChunksError(err);
  }
}

export async function fetchChunkIndexPage(
  docId: string,
  options: { offset?: number; limit?: number; signal?: AbortSignal } = {}
): Promise<ChunkIndexPage> {
  const params = new URLSearchParams();
  params.set('offset', String(options.offset ?? 0));
  if (options.limit != null) params.set('limit', String(options.limit));

  try {
    const response = await http
      .get(`admin/chunks/${encodeURIComponent(docId)}/index?${params.toString()}`, {
        signal: options.signal,
      })
      .json<unknown>();
    const page = unwrapEnvelope<ChunkIndexPage>(response);
    if (!page || !Array.isArray(page.chunks) || !page.document) {
      throw new ChunksError('unknown', 'Response did not contain a chunk index.');
    }
    return page;
  } catch (err) {
    throw toChunksError(err);
  }
}

export const CHUNK_INDEX_PAGE_SIZE = 1000;

/**
 * Walk every index page for a document, reporting progress as it goes so the
 * grid can paint the first page while the rest is still arriving.
 */
export async function fetchChunkIndex(
  docId: string,
  options: {
    signal?: AbortSignal;
    onPage?: (page: ChunkIndexPage, accumulated: ChunkIndexEntry[]) => void;
  } = {}
): Promise<{ document: DocMeta; chunks: ChunkIndexEntry[] }> {
  const accumulated: ChunkIndexEntry[] = [];
  let offset: number | null = 0;
  let document: DocMeta | null = null;

  while (offset != null) {
    const page: ChunkIndexPage = await fetchChunkIndexPage(docId, {
      offset,
      limit: CHUNK_INDEX_PAGE_SIZE,
      signal: options.signal,
    });
    document = page.document;
    accumulated.push(...page.chunks);
    options.onPage?.(page, accumulated);
    // Guard against a server that keeps handing back the same offset.
    offset = page.next_offset != null && page.next_offset > offset ? page.next_offset : null;
  }

  if (!document) {
    throw new ChunksError('unknown', 'Chunk index returned no document metadata.');
  }
  return { document, chunks: accumulated };
}

export async function fetchChunkText(
  docId: string,
  options: { offset: number; limit?: number; signal?: AbortSignal }
): Promise<ChunkFull[]> {
  const params = new URLSearchParams();
  params.set('offset', String(options.offset));
  if (options.limit != null) params.set('limit', String(options.limit));

  try {
    const response = await http
      .get(`admin/chunks/${encodeURIComponent(docId)}/text?${params.toString()}`, {
        signal: options.signal,
      })
      .json<unknown>();
    const data = unwrapEnvelope<{ chunks?: ChunkFull[] }>(response);
    if (!Array.isArray(data?.chunks)) {
      throw new ChunksError('unknown', 'Response did not contain chunk text.');
    }
    return data.chunks;
  } catch (err) {
    throw toChunksError(err);
  }
}

/** `${source_url}#page=N` deep link into the published PDF, when both exist. */
export function pageLink(sourceUrl: string | null, page: number | null): string | null {
  if (!sourceUrl || page == null) return null;
  return `${sourceUrl.split('#')[0]}#page=${page}`;
}
