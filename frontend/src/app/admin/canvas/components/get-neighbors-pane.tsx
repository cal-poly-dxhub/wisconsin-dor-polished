'use client';

import { useMemo } from 'react';

const GRID_COLS = 11;
const GRID_ROWS = 11;
const CELL = 22;
const GAP = 3;
const TOTAL = GRID_COLS * GRID_ROWS;
const GRID_W = GRID_COLS * (CELL + GAP) - GAP;
const GRID_H = GRID_ROWS * (CELL + GAP) - GAP;

const COLOR = {
  gov: '#61B233',
  wpam: '#3958E3',
  admin: '#FD8709',
  statute: '#FD0909',
  chunk: '#000000',
} as const;

function docColor(docId: string): string {
  if (docId.startsWith('wpam-')) return COLOR.wpam;
  if (docId.startsWith('admin_rules-')) return COLOR.admin;
  if (docId.startsWith('statutes-')) return COLOR.statute;
  if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) return COLOR.gov;
  if (docId.startsWith('iaao-') || docId.startsWith('uspap-')) return COLOR.chunk;
  return COLOR.chunk;
}

interface NeighborEntry {
  docId: string;
  title: string;
  chunks?: number;
  relationship?: string;
  rank?: number;
  score?: number;
}

interface GetNeighborsData {
  seedDoc: string;
  neighbors: NeighborEntry[];
  latencyMs: number;
  ranked?: boolean;
  query?: string;
  topK?: number;
  totalCandidates?: number;
}

function cellCenter(idx: number): { cx: number; cy: number } {
  const col = idx % GRID_COLS;
  const row = Math.floor(idx / GRID_COLS);
  return {
    cx: col * (CELL + GAP) + CELL / 2,
    cy: row * (CELL + GAP) + CELL / 2,
  };
}

function seededShuffle(arr: number[], seed: number): number[] {
  const copy = [...arr];
  let s = seed;
  for (let i = copy.length - 1; i > 0; i--) {
    s = (s * 16807 + 0) % 2147483647;
    const j = s % (i + 1);
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

export function GetNeighborsPane({ data }: { data: GetNeighborsData }) {
  const seedIdx = Math.floor(GRID_ROWS / 2) * GRID_COLS + Math.floor(GRID_COLS / 2);

  // Expand neighbors: each chunk gets its own grid cell
  const expandedNeighbors = useMemo(() => {
    const expanded: { docId: string; title: string; color: string }[] = [];
    for (const nb of data.neighbors) {
      const count = nb.chunks ?? 1;
      const color = docColor(nb.docId);
      for (let i = 0; i < count; i++) {
        expanded.push({ docId: nb.docId, title: nb.title, color });
      }
    }
    return expanded;
  }, [data.neighbors]);

  const neighborIndices = useMemo(() => {
    const available = Array.from({ length: TOTAL }, (_, i) => i).filter((i) => i !== seedIdx);
    const shuffled = seededShuffle(available, 42);
    return shuffled.slice(0, expandedNeighbors.length);
  }, [expandedNeighbors.length, seedIdx]);

  const seedCenter = cellCenter(seedIdx);

  // Legend: group by title with count
  const legend = useMemo(() => {
    const map = new Map<string, {
      docId: string;
      title: string;
      color: string;
      count: number;
      relationship?: string;
      rank?: number;
      score?: number;
    }>();
    for (const nb of data.neighbors) {
      const key = nb.docId;
      const existing = map.get(key);
      const chunks = nb.chunks ?? 1;
      if (existing) {
        existing.count += chunks;
      } else {
        map.set(key, {
          docId: nb.docId,
          title: nb.title,
          color: docColor(nb.docId),
          count: chunks,
          relationship: nb.relationship,
          rank: nb.rank,
          score: nb.score,
        });
      }
    }
    return Array.from(map.values());
  }, [data.neighbors]);

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">Graph Neighbors</h2>
          <span className="text-lg text-neutral-400 font-normal">{data.latencyMs}ms</span>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Seed</span>
          <span className="text-sm text-neutral-600 truncate">{data.seedDoc}</span>
        </div>
        {data.ranked && data.query && (
          <div className="mt-2 flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5">
            <span className="text-sm font-semibold text-blue-700">Ranked</span>
            <span className="min-w-0 flex-1 truncate text-sm text-blue-900">{data.query}</span>
            <span className="shrink-0 text-xs text-blue-600">
              {data.neighbors.length}/{data.totalCandidates ?? data.neighbors.length}
              {data.topK ? ` · top ${data.topK}` : ''}
            </span>
          </div>
        )}
      </div>

      {/* Grid */}
      <div className="relative mx-auto" style={{ width: GRID_W, height: GRID_H }}>
        {/* Edge lines (SVG overlay) */}
        <svg
          className="absolute inset-0 pointer-events-none z-10"
          width={GRID_W}
          height={GRID_H}
        >
          {neighborIndices.map((idx) => {
            const c = cellCenter(idx);
            return (
              <line
                key={idx}
                x1={seedCenter.cx}
                y1={seedCenter.cy}
                x2={c.cx}
                y2={c.cy}
                stroke="#000000"
                strokeWidth={1}
              />
            );
          })}
        </svg>

        {/* Grid cells */}
        {Array.from({ length: TOTAL }, (_, i) => {
          const isSeed = i === seedIdx;
          const neighborIdx = neighborIndices.indexOf(i);
          const isNeighbor = neighborIdx !== -1;
          const col = i % GRID_COLS;
          const row = Math.floor(i / GRID_COLS);
          const nb = isNeighbor ? expandedNeighbors[neighborIdx] : null;
          return (
            <div
              key={i}
              className="absolute"
              style={{
                left: col * (CELL + GAP),
                top: row * (CELL + GAP),
                width: CELL,
                height: CELL,
                borderRadius: 3,
                backgroundColor: isSeed ? '#000' : isNeighbor ? nb!.color : '#E5E7EB',
              }}
              title={isSeed ? data.seedDoc : nb?.title}
            />
          );
        })}
      </div>

      {/* Legend */}
      <ul className="mt-4 space-y-1.5">
        {legend.map((entry) => (
          <li key={entry.docId} className="flex items-center gap-2">
            {data.ranked && (
              <span className="w-5 shrink-0 text-right font-mono text-xs font-semibold text-neutral-500">
                {entry.rank ?? '–'}
              </span>
            )}
            <div style={{ width: 12, height: 12, borderRadius: 2, backgroundColor: entry.color }} />
            <span className="text-xs text-neutral-700 truncate flex-1">{entry.title}</span>
            {entry.relationship && !data.ranked && (
              <span className="text-[10px] font-medium text-neutral-400">{entry.relationship}</span>
            )}
            {entry.score != null && (
              <span className="font-mono text-xs font-medium tabular-nums text-neutral-500">
                {entry.score.toFixed(3)}
              </span>
            )}
            {entry.count > 1 && (
              <span className="text-xs text-neutral-400 font-medium">×{entry.count}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
