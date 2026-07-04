'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { http } from '@/lib/http';
import {
  Grid3X3,
  Loader2,
  Search,
  RefreshCw,
  ArrowLeft,
  ArrowRight,
  FileText,
  ExternalLink,
  HardDrive,
} from 'lucide-react';

interface DocSummary {
  doc_id: string;
  last_modified: string;
  size_bytes: number;
}

interface ChunkData {
  chunk_id: string;
  text: string;
  char_count: number;
  idx: number;
  heading: string | null;
  subheading: string | null;
  start_page: number | null;
  end_page: number | null;
  s3_key: string | null;
  statute_refs: string[];
  admin_rule_refs: string[];
  edition_year: number | null;
}

interface DocMeta {
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

const DOCS_CACHE_KEY = 'admin_chunks_docs';
const CHUNK_CACHE_PREFIX = 'admin_chunks_doc_';
const CACHE_TTL = 60 * 60 * 1000; // 1 hour
const LOCAL_PREFIX = 'LOCAL:';

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

function setCache<T>(key: string, data: T) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }));
  } catch {
    // storage full, ignore
  }
}

function invalidateDocCache(docId: string) {
  sessionStorage.removeItem(`${CHUNK_CACHE_PREFIX}${docId}`);
}

function invalidateAllCaches() {
  const keys: string[] = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    if (k && (k === DOCS_CACHE_KEY || k.startsWith(CHUNK_CACHE_PREFIX))) {
      keys.push(k);
    }
  }
  keys.forEach(k => sessionStorage.removeItem(k));
}

function getHeadingPrefix(heading: string | null): string {
  if (!heading) return '';
  const match = heading.match(/^(\d+\.\d+)/);
  return match ? match[1] : heading;
}

interface ChunkGroup {
  prefix: string;
  chunks: ChunkData[];
}

function groupChunksByHeading(chunks: ChunkData[]): ChunkGroup[] {
  const groups: ChunkGroup[] = [];
  let currentPrefix: string | null = null;
  let currentChunks: ChunkData[] = [];

  for (const chunk of chunks) {
    const prefix = getHeadingPrefix(chunk.heading);
    if (prefix !== currentPrefix) {
      if (currentChunks.length > 0) {
        groups.push({ prefix: currentPrefix || '', chunks: currentChunks });
      }
      currentPrefix = prefix;
      currentChunks = [chunk];
    } else {
      currentChunks.push(chunk);
    }
  }
  if (currentChunks.length > 0) {
    groups.push({ prefix: currentPrefix || '', chunks: currentChunks });
  }
  return groups;
}

function ChunkGrid({
  chunks,
  docMeta,
  maxChars,
  onChunkClick,
  grouped,
}: {
  chunks: ChunkData[];
  docMeta: DocMeta;
  maxChars: number;
  onChunkClick: (chunk: ChunkData) => void;
  grouped: boolean;
}) {
  const groups = groupChunksByHeading(chunks);
  const hasGroups = groups.some(g => g.chunks.length > 1);

  return (
    <div className="space-y-4">
      {/* Stats bar */}
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span>{docMeta.chunk_count} chunks</span>
        <span className="text-border">|</span>
        <span>{docMeta.total_chars.toLocaleString()} total chars</span>
        <span className="text-border">|</span>
        <span>range: {docMeta.min_chunk_chars}–{docMeta.max_chunk_chars}</span>
        {docMeta.doc_type && (
          <>
            <span className="text-border">|</span>
            <Badge variant="secondary">{docMeta.doc_type}</Badge>
          </>
        )}
        {docMeta.authority_level != null && (
          <Badge variant="outline">authority {docMeta.authority_level}</Badge>
        )}
        {hasGroups && (
          <>
            <span className="text-border">|</span>
            <span>{groups.length} sections</span>
          </>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <div className="flex items-center gap-1">
          <div className="h-3 w-3 rounded-sm bg-emerald-400" />
          <span>small</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="h-3 w-3 rounded-sm bg-emerald-700" />
          <span>medium</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="h-3 w-3 rounded-sm bg-emerald-950" />
          <span>large (near max)</span>
        </div>
      </div>

      {/* Grid */}
      {grouped ? (
        <div className="flex flex-wrap gap-1.5 items-start">
          {groups.map((group, gi) => (
            <div
              key={gi}
              className={
                group.chunks.length > 1
                  ? 'flex flex-wrap gap-1 rounded-lg border border-foreground/50 p-1'
                  : 'flex flex-wrap gap-1 rounded-lg border border-foreground/15 p-1'
              }
              title={group.prefix ? `§${group.prefix}` : undefined}
            >
              {group.chunks.map((chunk, ci) => {
                const ratio = maxChars > 0 ? chunk.char_count / maxChars : 0;
                const lightness = Math.round(85 - ratio * 60);
                const saturation = Math.round(50 + ratio * 30);
                return (
                  <button
                    key={ci}
                    onClick={() => onChunkClick(chunk)}
                    className="h-6 w-6 rounded-sm border border-border/30 transition-all hover:scale-150 hover:border-foreground hover:z-10 cursor-pointer"
                    style={{
                      backgroundColor: `hsl(160, ${saturation}%, ${lightness}%)`,
                    }}
                    title={`#${chunk.idx} §${group.prefix} — ${chunk.char_count} chars`}
                  />
                );
              })}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-1">
          {chunks.map((chunk, i) => {
            const ratio = maxChars > 0 ? chunk.char_count / maxChars : 0;
            const lightness = Math.round(85 - ratio * 60);
            const saturation = Math.round(50 + ratio * 30);
            return (
              <button
                key={i}
                onClick={() => onChunkClick(chunk)}
                className="h-6 w-6 rounded-sm border border-border/30 transition-all hover:scale-150 hover:border-foreground hover:z-10 cursor-pointer"
                style={{
                  backgroundColor: `hsl(160, ${saturation}%, ${lightness}%)`,
                }}
                title={`#${chunk.idx} — ${chunk.char_count} chars`}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function ChunkModal({
  chunk,
  chunks,
  open,
  onClose,
  onNavigate,
}: {
  chunk: ChunkData | null;
  chunks: ChunkData[];
  open: boolean;
  onClose: () => void;
  onNavigate: (chunk: ChunkData) => void;
}) {
  if (!chunk) return null;

  const currentIdx = chunks.findIndex(c => c.chunk_id === chunk.chunk_id);
  const hasPrev = currentIdx > 0;
  const hasNext = currentIdx < chunks.length - 1;

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="!max-w-6xl w-[92vw] h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader className="mb-4">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-sm font-mono">
              {chunk.chunk_id}
            </DialogTitle>
            <div className="flex items-center gap-1 mr-6">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={!hasPrev}
                onClick={() => hasPrev && onNavigate(chunks[currentIdx - 1])}
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <span className="text-xs text-muted-foreground min-w-[4rem] text-center">
                {currentIdx + 1} / {chunks.length}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={!hasNext}
                onClick={() => hasNext && onNavigate(chunks[currentIdx + 1])}
              >
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </DialogHeader>
        <div className="flex-1 overflow-hidden grid grid-cols-2 gap-4 min-h-0">
          {/* Left: chunk text */}
          <div className="overflow-y-auto rounded-md border bg-muted/30 p-4">
            <p className="whitespace-pre-wrap text-xs leading-relaxed font-mono">
              {chunk.text}
            </p>
          </div>

          {/* Right: metadata */}
          <div className="overflow-y-auto space-y-3 p-2">
            <MetaRow label="Index" value={`#${chunk.idx}`} />
            <MetaRow label="Characters" value={chunk.char_count.toLocaleString()} />
            {chunk.heading && <MetaRow label="Heading" value={chunk.heading} />}
            {chunk.subheading && <MetaRow label="Subheading" value={chunk.subheading} />}
            {chunk.start_page != null && (
              <MetaRow
                label="Pages"
                value={
                  chunk.end_page && chunk.end_page !== chunk.start_page
                    ? `${chunk.start_page}–${chunk.end_page}`
                    : `${chunk.start_page}`
                }
              />
            )}
            {chunk.s3_key && <MetaRow label="S3 Key" value={chunk.s3_key} mono />}
            {chunk.edition_year && (
              <MetaRow label="Edition Year" value={String(chunk.edition_year)} />
            )}
            {chunk.statute_refs.length > 0 && (
              <div>
                <span className="text-xs font-medium text-muted-foreground">
                  Statute Refs
                </span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {chunk.statute_refs.map(r => (
                    <Badge key={r} variant="outline" className="text-xs">
                      {r}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {chunk.admin_rule_refs.length > 0 && (
              <div>
                <span className="text-xs font-medium text-muted-foreground">
                  Admin Rule Refs
                </span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {chunk.admin_rule_refs.map(r => (
                    <Badge key={r} variant="outline" className="text-xs">
                      {r}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function MetaRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <p className={`text-sm ${mono ? 'font-mono text-xs break-all' : ''}`}>
        {value}
      </p>
    </div>
  );
}

export default function AdminChunksPage() {
  const [documents, setDocuments] = useState<DocSummary[]>([]);
  const [localDocs, setLocalDocs] = useState<DocSummary[]>([]);
  const [filteredDocs, setFilteredDocs] = useState<DocSummary[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunkData[]>([]);
  const [docMeta, setDocMeta] = useState<DocMeta | null>(null);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [selectedChunk, setSelectedChunk] = useState<ChunkData | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [invalidated, setInvalidated] = useState(false);
  const [grouped, setGrouped] = useState(true);
  const fetchedRef = useRef(false);

  const fetchDocuments = useCallback(async (skipCache = false) => {
    if (!skipCache) {
      const cached = getCached<DocSummary[]>(DOCS_CACHE_KEY);
      if (cached) {
        setDocuments(cached);
        setFilteredDocs(cached);
      }
    }

    setLoading(true);
    try {
      const [remoteRes, localRes] = await Promise.all([
        http
          .get('admin/chunks/documents')
          .json<{ statusCode?: number; body?: string; documents?: DocSummary[] }>()
          .catch(() => ({ documents: [] as DocSummary[] }) as { statusCode?: number; body?: string; documents?: DocSummary[] }),
        fetch('/api/local-chunks')
          .then(r => r.json())
          .catch(() => ({ documents: [] as DocSummary[] })),
      ]);
      const remoteData = remoteRes.body ? JSON.parse(remoteRes.body) : remoteRes;
      const docs = (remoteData.documents || []) as DocSummary[];
      const local = (localRes.documents || []) as DocSummary[];
      setDocuments(docs);
      setLocalDocs(local);
      setFilteredDocs(docs);
      setCache(DOCS_CACHE_KEY, docs);
    } catch (err) {
      console.error('Failed to fetch documents:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchChunks = useCallback(
    async (docId: string, skipCache = false) => {
      if (!skipCache) {
        const cached = getCached<{ meta: DocMeta; chunks: ChunkData[] }>(
          `${CHUNK_CACHE_PREFIX}${docId}`
        );
        if (cached) {
          setDocMeta(cached.meta);
          setChunks(cached.chunks);
          return;
        }
      }

      setChunksLoading(true);
      try {
        if (docId.startsWith(LOCAL_PREFIX)) {
          const file = docId.slice(LOCAL_PREFIX.length);
          const res = await fetch(`/api/local-chunks/${encodeURIComponent(file)}`).then(r => r.json());
          const meta = res.document as DocMeta;
          const chks = res.chunks as ChunkData[];
          setDocMeta(meta);
          setChunks(chks);
          if (meta) setCache(`${CHUNK_CACHE_PREFIX}${docId}`, { meta, chunks: chks });
        } else {
          const res = await http
            .get(`admin/chunks/${docId}`)
            .json<{ statusCode?: number; body?: string; document?: DocMeta; chunks?: ChunkData[] }>();
          const data = res.body ? JSON.parse(res.body) : res;
          const meta = data.document as DocMeta;
          const chks = data.chunks as ChunkData[];
          setDocMeta(meta);
          setChunks(chks);
          setCache(`${CHUNK_CACHE_PREFIX}${docId}`, { meta, chunks: chks });
        }
      } catch (err) {
        console.error('Failed to fetch chunks:', err);
      } finally {
        setChunksLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (!fetchedRef.current) {
      fetchedRef.current = true;
      fetchDocuments();
    }
  }, [fetchDocuments]);

  useEffect(() => {
    if (!searchQuery.trim()) {
      setFilteredDocs(documents);
    } else {
      const q = searchQuery.toLowerCase();
      setFilteredDocs(documents.filter(d => d.doc_id.toLowerCase().includes(q)));
    }
  }, [searchQuery, documents]);

  const handleDocSelect = (docId: string) => {
    setSelectedDocId(docId);
    setChunks([]);
    setDocMeta(null);
    fetchChunks(docId, invalidated);
  };

  const handleBack = () => {
    setSelectedDocId(null);
    setChunks([]);
    setDocMeta(null);
  };

  const handleRefreshDoc = () => {
    if (selectedDocId) {
      invalidateDocCache(selectedDocId);
      fetchChunks(selectedDocId, true);
    }
  };

  const handleInvalidateAll = () => {
    invalidateAllCaches();
    setInvalidated(true);
    setSelectedDocId(null);
    setChunks([]);
    setDocMeta(null);
    fetchDocuments(true);
  };

  const handleChunkClick = (chunk: ChunkData) => {
    setSelectedChunk(chunk);
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
              disabled={loading}
            >
              <RefreshCw className="mr-1 h-3 w-3" />
              Invalidate All
            </Button>
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-4 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Filter documents..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="pl-8"
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {/* Local chunks from pdf_chunking/final_chunks */}
            {localDocs.map(doc => (
              <button
                key={`local-${doc.doc_id}`}
                onClick={() => handleDocSelect(`${LOCAL_PREFIX}${doc.doc_id}`)}
                className="flex items-center gap-3 rounded-md border border-dashed border-amber-500/50 bg-amber-500/5 p-3 text-left transition-colors hover:bg-amber-500/10 cursor-pointer"
              >
                <HardDrive className="h-4 w-4 shrink-0 text-amber-600" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {doc.doc_id.replace(/\.jsonl$/, '').replace(/_\d{8}_\d{6}$/, '')}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {(doc.size_bytes / 1024).toFixed(0)} KB
                  </p>
                </div>
                <Badge variant="outline" className="text-xs border-amber-500/50 text-amber-600">
                  local
                </Badge>
              </button>
            ))}

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
            {filteredDocs.length === 0 && !loading && (
              <p className="col-span-full py-8 text-center text-sm text-muted-foreground">
                {documents.length === 0
                  ? 'No extracted documents found in work bucket.'
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
          <Button variant="ghost" size="icon" onClick={handleBack}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              {selectedDocId?.startsWith(LOCAL_PREFIX)
                ? selectedDocId.slice(LOCAL_PREFIX.length).replace(/\.jsonl$/, '').replace(/_\d{8}_\d{6}$/, '')
                : selectedDocId}
            </h1>
            {docMeta?.title && (
              <p className="text-sm text-muted-foreground">{docMeta.title}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {docMeta?.source_url && (
            <Button
              variant="outline"
              size="sm"
              asChild
            >
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
          <Button
            variant="outline"
            size="sm"
            onClick={handleInvalidateAll}
          >
            Invalidate All
          </Button>
        </div>
      </div>

      {chunksLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
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
              chunks={chunks}
              docMeta={docMeta}
              maxChars={docMeta.max_chunk_chars}
              onChunkClick={handleChunkClick}
              grouped={grouped}
            />
          </CardContent>
        </Card>
      ) : (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No chunks found for this document.
        </p>
      )}

      <ChunkModal
        chunk={selectedChunk}
        chunks={chunks}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onNavigate={(c) => setSelectedChunk(c)}
      />
    </div>
  );
}
