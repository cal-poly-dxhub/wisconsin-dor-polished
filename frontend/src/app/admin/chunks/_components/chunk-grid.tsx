'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import type { ChunkIndexEntry, DocMeta } from '../lib/chunks-api';
import { groupChunksByHeading, type ChunkGroup } from '../lib/headings';

/**
 * Cells per windowed block. A block is the unit that is mounted or swapped for
 * a spacer, so it has to be big enough that the observer count stays small and
 * small enough that a few hundred cells is all that is ever in the DOM.
 */
const BLOCK_CELL_TARGET = 300;

/** Cell + gap, used only to estimate the height of a block never yet rendered. */
const CELL_PX = 28;
const ESTIMATED_CELLS_PER_ROW = 30;

interface Block {
  key: number;
  cellCount: number;
  groups: ChunkGroup[];
}

function buildBlocks(groups: ChunkGroup[]): Block[] {
  const blocks: Block[] = [];
  let current: ChunkGroup[] = [];
  let cells = 0;

  for (const group of groups) {
    current.push(group);
    cells += group.chunks.length;
    if (cells >= BLOCK_CELL_TARGET) {
      blocks.push({ key: blocks.length, cellCount: cells, groups: current });
      current = [];
      cells = 0;
    }
  }
  if (current.length > 0) {
    blocks.push({ key: blocks.length, cellCount: cells, groups: current });
  }
  return blocks;
}

function cellStyle(charCount: number, maxChars: number) {
  const ratio = maxChars > 0 ? Math.min(charCount / maxChars, 1) : 0;
  const lightness = Math.round(85 - ratio * 60);
  const saturation = Math.round(50 + ratio * 30);
  return { backgroundColor: `hsl(160, ${saturation}%, ${lightness}%)` };
}

function ChunkCell({
  chunk,
  label,
  maxChars,
  onSelect,
}: {
  chunk: ChunkIndexEntry;
  label: string;
  maxChars: number;
  onSelect: (chunk: ChunkIndexEntry) => void;
}) {
  const description = `#${chunk.idx}${label ? ` ${label}` : ''} — ${chunk.char_count} chars`;
  return (
    <button
      type="button"
      onClick={() => onSelect(chunk)}
      // No transform/transition here: with thousands of cells a `transition-all`
      // hover that scales the element is what made this grid crawl.
      className="h-6 w-6 rounded-sm border border-border/30 cursor-pointer hover:ring-2 hover:ring-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground"
      style={cellStyle(chunk.char_count, maxChars)}
      title={description}
      aria-label={description}
    />
  );
}

/**
 * One windowed block. Off-screen it renders a spacer of its last measured
 * height (or an estimate), so scrolling stays stable while the DOM stays small.
 */
function GridBlock({
  block,
  grouped,
  maxChars,
  onSelect,
  heights,
}: {
  block: Block;
  grouped: boolean;
  maxChars: number;
  onSelect: (chunk: ChunkIndexEntry) => void;
  heights: Map<number, number>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // The first block paints immediately; without IntersectionObserver (jsdom,
  // very old browsers) everything paints and windowing simply does not apply.
  const [visible, setVisible] = useState(
    () => block.key === 0 || typeof IntersectionObserver === 'undefined'
  );

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) setVisible(entry.isIntersecting);
      },
      // Render a screenful ahead so a fast scroll does not show empty space.
      { rootMargin: '800px 0px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (visible && ref.current) {
      const measured = ref.current.offsetHeight;
      if (measured > 0) heights.set(block.key, measured);
    }
  }, [visible, block.key, heights, grouped]);

  if (!visible) {
    const fallback = Math.ceil(block.cellCount / ESTIMATED_CELLS_PER_ROW) * CELL_PX;
    return (
      <div ref={ref} style={{ height: heights.get(block.key) ?? fallback }} aria-hidden />
    );
  }

  return (
    <div ref={ref} className="flex flex-wrap items-start gap-1.5">
      {grouped
        ? block.groups.map((group, gi) => (
            <div
              key={`${block.key}-${gi}`}
              className={
                group.chunks.length > 1
                  ? 'flex flex-wrap gap-1 rounded-lg border border-foreground/50 p-1'
                  : 'flex flex-wrap gap-1 rounded-lg border border-foreground/15 p-1'
              }
              title={group.label || undefined}
            >
              {group.chunks.map(chunk => (
                <ChunkCell
                  key={chunk.chunk_id}
                  chunk={chunk}
                  label={group.label}
                  maxChars={maxChars}
                  onSelect={onSelect}
                />
              ))}
            </div>
          ))
        : block.groups.flatMap(group =>
            group.chunks.map(chunk => (
              <ChunkCell
                key={chunk.chunk_id}
                chunk={chunk}
                label={group.label}
                maxChars={maxChars}
                onSelect={onSelect}
              />
            ))
          )}
    </div>
  );
}

export function ChunkGrid({
  chunks,
  docMeta,
  grouped,
  loadedCount,
  onSelect,
}: {
  chunks: ChunkIndexEntry[];
  docMeta: DocMeta;
  grouped: boolean;
  /** How many chunks have arrived so far, when the index is still paging in. */
  loadedCount?: number;
  onSelect: (chunk: ChunkIndexEntry) => void;
}) {
  const groups = useMemo(() => groupChunksByHeading(chunks), [chunks]);
  const blocks = useMemo(() => buildBlocks(groups), [groups]);
  // Measured block heights, so a block that scrolls out leaves a spacer the
  // exact size it was instead of collapsing the page.
  const heights = useMemo(() => new Map<number, number>(), []);
  const sectionCount = useMemo(
    () => groups.filter(g => g.chunks.length > 1).length,
    [groups]
  );
  const maxChars = docMeta.max_chunk_chars;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span>{docMeta.chunk_count.toLocaleString()} chunks</span>
        <span className="text-border">|</span>
        <span>{docMeta.total_chars.toLocaleString()} total chars</span>
        <span className="text-border">|</span>
        <span>
          range: {docMeta.min_chunk_chars.toLocaleString()}–
          {docMeta.max_chunk_chars.toLocaleString()}
        </span>
        {docMeta.doc_type && (
          <>
            <span className="text-border">|</span>
            <Badge variant="secondary">{docMeta.doc_type}</Badge>
          </>
        )}
        {docMeta.authority_level != null && (
          <Badge variant="outline">authority {docMeta.authority_level}</Badge>
        )}
        {sectionCount > 0 && (
          <>
            <span className="text-border">|</span>
            <span>{sectionCount.toLocaleString()} sections</span>
          </>
        )}
        {loadedCount != null && loadedCount < docMeta.chunk_count && (
          <>
            <span className="text-border">|</span>
            <span className="text-amber-600">
              loading {loadedCount.toLocaleString()} / {docMeta.chunk_count.toLocaleString()}
            </span>
          </>
        )}
      </div>

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

      <div className="space-y-1.5">
        {blocks.map(block => (
          <GridBlock
            key={block.key}
            block={block}
            grouped={grouped}
            maxChars={maxChars}
            onSelect={onSelect}
            heights={heights}
          />
        ))}
      </div>
    </div>
  );
}
