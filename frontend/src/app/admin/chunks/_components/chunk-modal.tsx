'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  fetchChunkText,
  pageLink,
  toChunksError,
  type ChunkFull,
  type ChunkIndexEntry,
  type ChunksError,
  type DocMeta,
} from '../lib/chunks-api';

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
      <p className={`text-sm ${mono ? 'font-mono text-xs break-all' : ''}`}>{value}</p>
    </div>
  );
}

function RefList({ label, refs }: { label: string; refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <div>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="mt-1 flex flex-wrap gap-1">
        {refs.map(r => (
          <Badge key={r} variant="outline" className="text-xs">
            {r}
          </Badge>
        ))}
      </div>
    </div>
  );
}

/**
 * Chunk inspector. The grid only holds metadata, so the text for the open chunk
 * is fetched on demand (and kept for the session, so paging back is instant).
 */
export function ChunkModal({
  docId,
  docMeta,
  chunks,
  activeIndex,
  open,
  onClose,
  onNavigate,
}: {
  docId: string;
  docMeta: DocMeta | null;
  /** The full index list, so prev/next can walk it. */
  chunks: ChunkIndexEntry[];
  /** Position of the open chunk within `chunks`, or -1 when none is open. */
  activeIndex: number;
  open: boolean;
  onClose: () => void;
  onNavigate: (index: number) => void;
}) {
  const [full, setFull] = useState<ChunkFull | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ChunksError | null>(null);
  // Text already fetched this session, so paging back to a chunk is instant.
  const cache = useMemo(() => new Map<string, ChunkFull>(), []);
  const chunk = activeIndex >= 0 ? chunks[activeIndex] : undefined;

  const loadText = useCallback(
    async (entry: ChunkIndexEntry, signal: AbortSignal) => {
      const cached = cache.get(entry.chunk_id);
      if (cached) {
        setFull(cached);
        setError(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const [fetched] = await fetchChunkText(docId, { offset: entry.pos, limit: 1, signal });
        if (signal.aborted) return;
        if (!fetched) {
          setError(toChunksError(new Error('The API returned no chunk at that position.')));
          setFull(null);
          return;
        }
        cache.set(entry.chunk_id, fetched);
        setFull(fetched);
      } catch (err) {
        if (signal.aborted) return;
        setError(toChunksError(err));
        setFull(null);
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [docId, cache]
  );

  useEffect(() => {
    if (!open || !chunk) {
      setFull(null);
      return;
    }
    const controller = new AbortController();
    void loadText(chunk, controller.signal);
    return () => controller.abort();
  }, [open, chunk, loadText]);

  if (!chunk) return null;

  const hasPrev = activeIndex > 0;
  const hasNext = activeIndex < chunks.length - 1;
  const deepLink = pageLink(docMeta?.source_url ?? null, chunk.start_page);
  const pageLabel =
    chunk.end_page && chunk.end_page !== chunk.start_page
      ? `${chunk.start_page}–${chunk.end_page}`
      : `${chunk.start_page}`;

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="!max-w-6xl w-[92vw] h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader className="mb-4">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-sm font-mono">{chunk.chunk_id}</DialogTitle>
            <div className="flex items-center gap-1 mr-6">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={!hasPrev}
                onClick={() => hasPrev && onNavigate(activeIndex - 1)}
                aria-label="Previous chunk"
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <span className="text-xs text-muted-foreground min-w-[6rem] text-center">
                {activeIndex + 1} / {chunks.length}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={!hasNext}
                onClick={() => hasNext && onNavigate(activeIndex + 1)}
                aria-label="Next chunk"
              >
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-hidden grid grid-cols-2 gap-4 min-h-0">
          <div className="overflow-y-auto rounded-md border bg-muted/30 p-4">
            {loading && (
              <div className="space-y-2" aria-label="Loading chunk text">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="h-3 animate-pulse rounded bg-muted" />
                ))}
              </div>
            )}
            {!loading && error && (
              <p className="text-xs text-destructive">
                Could not load this chunk&apos;s text: {error.message}
              </p>
            )}
            {!loading && !error && full && (
              <p className="whitespace-pre-wrap text-xs leading-relaxed font-mono">
                {full.text}
              </p>
            )}
          </div>

          <div className="overflow-y-auto space-y-3 p-2">
            <MetaRow label="Index" value={`#${chunk.idx}`} />
            <MetaRow label="Characters" value={chunk.char_count.toLocaleString()} />
            {chunk.heading && <MetaRow label="Heading" value={chunk.heading} />}
            {chunk.subheading && <MetaRow label="Subheading" value={chunk.subheading} />}
            {chunk.start_page != null && (
              <div>
                <span className="text-xs font-medium text-muted-foreground">Pages</span>
                {deepLink ? (
                  <p className="text-sm">
                    <a
                      href={deepLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
                    >
                      {pageLabel}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </p>
                ) : (
                  <p className="text-sm">{pageLabel}</p>
                )}
              </div>
            )}
            {full?.s3_key && <MetaRow label="S3 Key" value={full.s3_key} mono />}
            {full?.edition_year != null && (
              <MetaRow label="Edition Year" value={String(full.edition_year)} />
            )}
            {full && <RefList label="Statute Refs" refs={full.statute_refs} />}
            {full && <RefList label="Admin Rule Refs" refs={full.admin_rule_refs} />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
