'use client';

export const COLOR = {
  chunk: '#000000',
  gov: '#61B233',
  wpam: '#3958E3',
  admin: '#FD8709',
  statute: '#FD0909',
  caseLaw: '#9333EA',
  faq: '#CA8A04',
  iaao: '#EC4899',
  uspap: '#F472B6',
  constitution: '#4F46E5',
} as const;

export const GRAY = '#D1D5DB';
export const LIGHT_GRAY = '#E5E7EB';
export const BROAD_ACCENT = '#0D9488';

export const DIVERSITY_CAP = 5;
export const SQUARE_SIZE = 18;
export const SQUARE_GAP = 3;

const AUTHORITY_LABELS: Record<string, { label: string; color: string }> = {
  '1': { label: 'Constitution', color: COLOR.constitution },
  '2': { label: 'Statute', color: COLOR.statute },
  '3': { label: 'Case Law', color: COLOR.caseLaw },
  '4': { label: 'Admin Rule', color: COLOR.admin },
  '5': { label: 'WPAM', color: COLOR.wpam },
  '6': { label: 'FAQ', color: COLOR.faq },
  '7': { label: 'Gov. Pub.', color: COLOR.gov },
  '8': { label: 'IAAO', color: COLOR.iaao },
  '9': { label: 'USPAP', color: COLOR.uspap },
};

export function docTypeColor(docId: string): string {
  if (docId.startsWith('wpam-')) return COLOR.wpam;
  if (docId.startsWith('admin_rules-')) return COLOR.admin;
  if (docId.startsWith('statutes-')) return COLOR.statute;
  if (docId.startsWith('case_law-') || docId.startsWith('case-law-')) return COLOR.caseLaw;
  if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) return COLOR.gov;
  if (docId.startsWith('iaao-')) return COLOR.iaao;
  if (docId.startsWith('uspap-')) return COLOR.uspap;
  return COLOR.chunk;
}

export function docTypeLabel(docId: string): string {
  if (docId.startsWith('wpam-')) return 'WPAM';
  if (docId.startsWith('admin_rules-')) return 'Admin Rule';
  if (docId.startsWith('statutes-')) return 'Statute';
  if (docId.startsWith('case_law-') || docId.startsWith('case-law-')) return 'Case Law';
  if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) return 'Gov. Pub.';
  if (docId.startsWith('iaao-')) return 'IAAO';
  if (docId.startsWith('uspap-')) return 'USPAP';
  return 'Document';
}

export function authorityMeta(level: string): { label: string; color: string } {
  return AUTHORITY_LABELS[level] ?? { label: `Level ${level}`, color: COLOR.chunk };
}

export function backfillLabel(chunkId: string): string {
  const match = chunkId.match(/statutes-(\d+)_chunk_(\d+)/);
  if (match) return `Statute-${match[1]}.${match[2]}`;
  return chunkId.replace(/_chunk_\d+$/, '');
}

export function groupDocChunks(docChunks: Record<string, number>) {
  const byType = new Map<string, { label: string; color: string; count: number }>();
  for (const [docId, count] of Object.entries(docChunks)) {
    const label = docTypeLabel(docId);
    const existing = byType.get(label);
    if (existing) {
      existing.count += count;
    } else {
      byType.set(label, { label, color: docTypeColor(docId), count });
    }
  }
  return Array.from(byType.values());
}

export function SquareGrid({
  count,
  color,
  fadedCount = 0,
  maxDisplay,
  hatched = false,
}: {
  count: number;
  color: string;
  fadedCount?: number;
  maxDisplay?: number;
  hatched?: boolean;
}) {
  const display = maxDisplay != null ? Math.min(count, maxDisplay) : count;
  const solidCount = Math.min(count - fadedCount, display);
  const remainder = count - display;

  return (
    <div>
      <div className="flex flex-wrap" style={{ gap: SQUARE_GAP }}>
        {Array.from({ length: display }, (_, i) => (
          <div
            key={i}
            style={{
              width: SQUARE_SIZE,
              height: SQUARE_SIZE,
              borderRadius: 2,
              backgroundColor: i < solidCount ? color : GRAY,
              opacity: i < solidCount ? 1 : 0.5,
              backgroundImage: hatched && i < solidCount
                ? `repeating-linear-gradient(-45deg, transparent, transparent 3px, rgba(255,255,255,0.35) 3px, rgba(255,255,255,0.35) 6px)`
                : undefined,
              border: hatched && i < solidCount ? `2px dashed ${BROAD_ACCENT}` : undefined,
              boxSizing: 'border-box',
            }}
          />
        ))}
        {remainder > 0 && (
          <div
            className="flex items-center text-xs font-medium text-neutral-400"
            style={{ height: SQUARE_SIZE, paddingLeft: 4 }}
          >
            +{remainder >= 1000 ? `${(remainder / 1000).toFixed(1)}K` : remainder} More
          </div>
        )}
      </div>
    </div>
  );
}

export function inferAuthorityFromDocChunks(
  docChunks: Record<string, number>,
): Record<string, number> {
  const breakdown: Record<string, number> = {};
  for (const [docId, count] of Object.entries(docChunks)) {
    let level = '9';
    if (docId.startsWith('statutes-')) level = '2';
    else if (docId.startsWith('case_law-') || docId.startsWith('case-law-')) level = '3';
    else if (docId.startsWith('admin_rules-')) level = '4';
    else if (docId.startsWith('wpam-')) level = '5';
    else if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) level = '7';
    else if (docId.startsWith('iaao-')) level = '8';
    breakdown[level] = (breakdown[level] ?? 0) + count;
  }
  return breakdown;
}

export function scoreBucketsFromScores(scores: number[]): Record<string, number> {
  const buckets: Record<string, number> = {};
  for (const s of scores) {
    let key: string;
    if (s >= 0.9) key = '0.9+';
    else if (s >= 0.8) key = '0.8-0.9';
    else if (s >= 0.7) key = '0.7-0.8';
    else key = '<0.7';
    buckets[key] = (buckets[key] ?? 0) + 1;
  }
  return buckets;
}

export function resolveAuthorityBreakdown(
  breakdown: Record<string, number> | undefined,
  docChunks: Record<string, number>,
): Record<string, number> {
  if (breakdown && Object.keys(breakdown).length > 0) return breakdown;
  return inferAuthorityFromDocChunks(docChunks);
}

export function resolveScoreBuckets(
  buckets: Record<string, number> | undefined,
  scores: number[],
): Record<string, number> {
  if (buckets && Object.keys(buckets).length > 0) return buckets;
  if (scores.length > 0) return scoreBucketsFromScores(scores);
  return {};
}

export function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-neutral-200 py-5 first-of-type:border-t-0 first-of-type:pt-0">
      <h3 className="text-base font-bold text-neutral-900">{title}</h3>
      {subtitle && <p className="mt-0.5 text-sm text-neutral-500">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </div>
  );
}

export function AuthorityBreakdownBar({
  breakdown,
}: {
  breakdown: Record<string, number>;
}) {
  const total = Object.values(breakdown).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const entries = Object.entries(breakdown)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([level, count]) => ({ ...authorityMeta(level), count }));

  return (
    <div>
      <div className="flex h-4 w-full overflow-hidden rounded-sm">
        {entries.map((e) => (
          <div
            key={e.label}
            style={{ width: `${(e.count / total) * 100}%`, backgroundColor: e.color }}
            title={`${e.label}: ${e.count}`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {entries.map((e) => (
          <span key={e.label} className="flex items-center gap-1.5 text-xs text-neutral-600">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: e.color }} />
            {e.label} {e.count}
          </span>
        ))}
      </div>
    </div>
  );
}

export function ScoreBucketsChart({
  buckets,
}: {
  buckets: Record<string, number>;
}) {
  const order = ['0.9+', '0.8-0.9', '0.7-0.8', '<0.7'];
  const entries = order
    .filter((k) => buckets[k] != null && buckets[k] > 0)
    .map((k) => ({ label: k, count: buckets[k] }));
  const max = Math.max(...entries.map((e) => e.count), 1);

  return (
    <div className="flex items-end gap-2" style={{ height: 72 }}>
      {entries.map((e) => (
        <div key={e.label} className="flex flex-1 flex-col items-center justify-end h-full">
          <div
            className="w-full rounded-t-sm bg-neutral-800"
            style={{ height: `${(e.count / max) * 100}%`, minHeight: e.count > 0 ? 4 : 0 }}
          />
          <span className="mt-1 text-[10px] tabular-nums text-neutral-400">{e.count}</span>
          <span className="text-[10px] text-neutral-500">{e.label}</span>
        </div>
      ))}
    </div>
  );
}

export interface VectorSearchBaseData {
  query: string;
  latencyMs: number;
  preDedupCount: number;
  chunkCount: number;
  broadChunkCount?: number;
  totalChunkCount?: number;
  topK?: number;
  diversityCapPerDoc?: number;
  docCount?: number;
  topScore?: number;
  docChunks: Record<string, number>;
  authorityBreakdown?: Record<string, number>;
  scoreBuckets?: Record<string, number>;
  targetWpamYear?: number;
  caseLawCount?: number;
  autoEnrichedCount?: number;
  statuteBackfill?: { chunkId: string; docId: string; sourceRank: number }[];
  caselawBackfill?: { caseId: string; title: string; citation: string; summary: string; relevanceScore?: number; contentRole?: string }[];
  caselawBackfillMeta?: { stubsSearched?: string[]; candidateCount?: number; fetchSaturated?: boolean; fetchK?: number; latencyMs?: number };
}
