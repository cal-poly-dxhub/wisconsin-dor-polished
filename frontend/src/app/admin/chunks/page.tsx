'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  Grid3X3,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { ChunkGrid } from './_components/chunk-grid';
import { ChunkModal } from './_components/chunk-modal';
import {
  fetchChunkIndex,
  fetchDocuments,
  toChunksError,
  type ChunkIndexEntry,
  type ChunksError,
  type DocMeta,
  type DocSummary,
} from './lib/chunks-api';

const DOCS_CACHE_KEY = 'admin_chunks_docs';
const CHUNK_CACHE_PREFIX = 'admin_chunks_index_';
const CACHE_TTL = 60 * 60 * 1000; // 1 hour
const SEARCH_DEBOUNCE_MS = 250;
// sessionStorage tops out around 5 MB per origin and throws when it is full.
// The index projection is small, but a huge document plus the other admin
// caches can still get there, so refuse to even try past this size.
const MAX_CACHE_BYTES = 1_500_000;

function getCached<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const { data, ts } = JSON.parse(raw);
    if (Date.now() - ts > CACHE_TTL) {
      sessionStorage.removeItem(key);
      return null;
    }
    return data as T;
  } catch {
    return null;
  }
}

function setCache<T>(key: string, data: T): void {
  try {
    const payload = JSON.stringify({ data, ts: Date.now() });
    if (payload.length > MAX_CACHE_BYTES) return;
    sessionStorage.setItem(key, payload);
  } catch {
    // Quota exceeded or storage unavailable — the page works without the cache.
  }
}

function invalidateAllCaches() {
  try {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && (k === DOCS_CACHE_KEY || k.startsWith(CHUNK_CACHE_PREFIX))) keys.push(k);
    }
    keys.forEach(k => sessionStorage.removeItem(k));
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}

function ErrorPanel({
  error,
  what,
  onRetry,
}: {
  error: ChunksError;
  what: string;
  onRetry: () => void;
}) {
  const heading =
    error.kind === 'forbidden'
      ? 'Not authorized'
      : error.kind === 'not-found'
        ? `${what} not found`
        : error.kind === 'network'
          ? 'Could not reach the API'
          : `Could not load ${what.toLowerCase()}`;

  return (
    <div className="flex flex-col items-center gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
      <ShieldAlert className="h-5 w-5 text-destructive" />
      <div>
        <p className="text-sm font-medium text-foreground">{heading}</p>
        <p className="mt-1 text-sm text-muted-foreground">{error.message}</p>
      </div>
      {error.kind !== 'forbidden' && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-1 h-3 w-3" />
          Retry
        </Button>
      )}
    </div>
  );
}

export default function AdminChunksPage() {
  const [documents, setDocuments] = useState<DocSummary[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [docsLoading, setDocsLoading] = useState(false);
  const [docsError, setDocsError] = useState<ChunksError | null>(null);

  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunkIndexEntry[]>([]);
  const [docMeta, setDocMeta] = useState<DocMeta | null>(null);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [chunksError, setChunksError] = useState<ChunksError | null>(null);

  const [activeIndex, setActiveIndex] = useState(-1);
  const [modalOpen, setModalOpen] = useState(false);
  const [grouped, setGrouped] = useState(true);
  const [invalidated, setInvalidated] = useState(false);

  const fetchedRef = useRef(false);
  const chunkRequestRef = useRef<AbortController | null>(null);

  const loadDocuments = useCallback(async (skipCache = false) => {
    if (!skipCache) {
      const cached = getCached<DocSummary[]>(DOCS_CACHE_KEY);
      if (cached) setDocuments(cached);
    }

    setDocsLoading(true);
    setDocsError(null);
    try {
      const docs = await fetchDocuments();
      setDocuments(docs);
      setCache(DOCS_CACHE_KEY, docs);
    } catch (err) {
      // A 403 used to look exactly like an empty corpus. Keep them apart.
      setDocsError(toChunksError(err));
      setDocuments([]);
    } finally {
      setDocsLoading(false);
    }
  }, []);

  const loadChunkIndex = useCallback(async (docId: string, skipCache = false) => {
    chunkRequestRef.current?.abort();
    const controller = new AbortController();
    chunkRequestRef.current = controller;

    if (!skipCache) {
      const cached = getCached<{ meta: DocMeta; chunks: ChunkIndexEntry[] }>(
        `${CHUNK_CACHE_PREFIX}${docId}`
      );
      if (cached) {
        setDocMeta(cached.meta);
        setChunks(cached.chunks);
        setChunksError(null);
        setChunksLoading(false);
        return;
      }
    }

    setChunksLoading(true);
    setChunksError(null);
    setChunks([]);
    setDocMeta(null);
    try {
      const { document, chunks: entries } = await fetchChunkIndex(docId, {
        signal: controller.signal,
        // Paint the first page while the rest is still arriving.
        onPage: (page, accumulated) => {
          if (controller.signal.aborted) return;
          setDocMeta(page.document);
          setChunks([...accumulated]);
        },
      });
      if (controller.signal.aborted) return;
      setDocMeta(document);
      setChunks(entries);
      setCache(`${CHUNK_CACHE_PREFIX}${docId}`, { meta: document, chunks: entries });
    } catch (err) {
      if (controller.signal.aborted) return;
      setChunksError(toChunksError(err));
      setChunks([]);
      setDocMeta(null);
    } finally {
      if (!controller.signal.aborted) setChunksLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!fetchedRef.current) {
      fetchedRef.current = true;
      void loadDocuments();
    }
  }, [loadDocuments]);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(searchQuery), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [searchQuery]);

  useEffect(() => () => chunkRequestRef.current?.abort(), []);

  const filteredDocs = useMemo(() => {
    const q = debouncedQuery.trim().toLowerCase();
    if (!q) return documents;
    return documents.filter(d => d.doc_id.toLowerCase().includes(q));
  }, [documents, debouncedQuery]);

  const handleDocSelect = (docId: string) => {
    setSelectedDocId(docId);
    setActiveIndex(-1);
    setModalOpen(false);
    void loadChunkIndex(docId, invalidated);
  };

  const handleBack = () => {
    chunkRequestRef.current?.abort();
    setSelectedDocId(null);
    setChunks([]);
    setDocMeta(null);
    setChunksError(null);
  };

  const handleRefreshDoc = () => {
    if (selectedDocId) {
      try {
        sessionStorage.removeItem(`${CHUNK_CACHE_PREFIX}${selectedDocId}`);
      } catch {
        // ignore
      }
      void loadChunkIndex(selectedDocId, true);
    }
  };

  const handleInvalidateAll = () => {
    invalidateAllCaches();
    setInvalidated(true);
    setSelectedDocId(null);
    setChunks([]);
    setDocMeta(null);
    setChunksError(null);
    void loadDocuments(true);
  };

  const handleChunkSelect = (chunk: ChunkIndexEntry) => {
    const index = chunks.findIndex(c => c.chunk_id === chunk.chunk_id);
    if (index < 0) return;
    setActiveIndex(index);
    setModalOpen(true);
  };

  // Document list view
  if (!selectedDocId) {
    return (
      <div className="px-6 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Chunk Visualizer</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Select a document to view its chunks as a heatmap grid
            </p>
          </div>
          <div className="flex items-center gap-2">
            {invalidated && (
              <Badge variant="secondary" className="text-xs">
                cache cleared
              </Badge>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={handleInvalidateAll}
              disabled={docsLoading}
            >
              <RefreshCw className="mr-1 h-3 w-3" />
              Invalidate All
            </Button>
          </div>
        </div>

        <div className="relative mb-4 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Filter documents..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="pl-8"
          />
        </div>

        {docsError ? (
          <ErrorPanel
            error={docsError}
            what="Documents"
            onRetry={() => void loadDocuments(true)}
          />
        ) : docsLoading && documents.length === 0 ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 9 }).map((_, i) => (
              <div key={i} className="h-16 animate-pulse rounded-md bg-muted/50" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {filteredDocs.map(doc => (
              <button
                key={doc.doc_id}
                onClick={() => handleDocSelect(doc.doc_id)}
                className="flex items-center gap-3 rounded-md border p-3 text-left transition-colors hover:bg-accent/50 cursor-pointer"
              >
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{doc.doc_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {(doc.size_bytes / 1024).toFixed(0)} KB
                  </p>
                </div>
              </button>
            ))}
            {filteredDocs.length === 0 && (
              <p className="col-span-full py-8 text-center text-sm text-muted-foreground">
                {documents.length === 0
                  ? 'No extracted documents found in the work bucket.'
                  : 'No documents match your filter.'}
              </p>
            )}
          </div>
        )}
      </div>
    );
  }

  // Chunk grid view
  return (
    <div className="px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={handleBack} aria-label="Back to documents">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-lg font-semibold text-foreground">{selectedDocId}</h1>
            {docMeta?.title && (
              <p className="text-sm text-muted-foreground">{docMeta.title}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {docMeta?.source_url && (
            <Button variant="outline" size="sm" asChild>
              <a href={docMeta.source_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="mr-1 h-3 w-3" />
                View Source
              </a>
            </Button>
          )}
          <Button
            variant={grouped ? 'default' : 'outline'}
            size="sm"
            onClick={() => setGrouped(g => !g)}
          >
            <Grid3X3 className="mr-1 h-3 w-3" />
            Group
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefreshDoc}
            disabled={chunksLoading}
          >
            <RefreshCw className="mr-1 h-3 w-3" />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={handleInvalidateAll}>
            Invalidate All
          </Button>
        </div>
      </div>

      {chunksError ? (
        <ErrorPanel
          error={chunksError}
          what="Document"
          onRetry={() => selectedDocId && void loadChunkIndex(selectedDocId, true)}
        />
      ) : chunksLoading && chunks.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading chunk index...
        </div>
      ) : docMeta && chunks.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Grid3X3 className="h-4 w-4" />
              Chunk Heatmap
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ChunkGrid
              key={selectedDocId}
              chunks={chunks}
              docMeta={docMeta}
              grouped={grouped}
              loadedCount={chunksLoading ? chunks.length : undefined}
              onSelect={handleChunkSelect}
            />
          </CardContent>
        </Card>
      ) : (
        <p className="py-8 text-center text-sm text-muted-foreground">
          This document extracted 0 chunks.
        </p>
      )}

      <ChunkModal
        docId={selectedDocId}
        docMeta={docMeta}
        chunks={chunks}
        activeIndex={activeIndex}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onNavigate={setActiveIndex}
      />
    </div>
  );
}
