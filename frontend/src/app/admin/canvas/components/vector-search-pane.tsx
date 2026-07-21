'use client';

import { CORPUS_TOTAL_CHUNKS } from '../fixtures/demo-trace';
import {
  AuthorityBreakdownBar,
  COLOR,
  DIVERSITY_CAP,
  LIGHT_GRAY,
  ScoreBucketsChart,
  Section,
  SquareGrid,
  SQUARE_GAP,
  SQUARE_SIZE,
  VectorSearchBaseData,
  backfillLabel,
  docTypeColor,
  groupDocChunks,
} from './vector-search-shared';

export function VectorSearchPane({ data }: { data: VectorSearchBaseData }) {
  const fetched = data.preDedupCount;
  const sourceGroups = groupDocChunks(data.docChunks);

  const diversityEntries = Object.entries(data.docChunks).map(([docId, total]) => ({
    docId,
    color: docTypeColor(docId),
    total,
    kept: Math.min(total, DIVERSITY_CAP),
  }));

  const backfill = data.statuteBackfill ?? [];

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">Vector Search</h2>
          <span className="text-lg font-normal text-neutral-400">{data.latencyMs}ms</span>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Q</span>
          <span className="text-sm text-neutral-600">{data.query}</span>
        </div>
        <p className="mt-2 text-xs text-neutral-400">
          Broad arm already ran on initial search
        </p>
      </div>

      {/* Fetched out of total */}
      <Section title={`Fetched ${fetched} out of ${(CORPUS_TOTAL_CHUNKS / 1000).toFixed(1)}K Chunks`}>
        <SquareGrid
          count={CORPUS_TOTAL_CHUNKS}
          color={COLOR.chunk}
          fadedCount={CORPUS_TOTAL_CHUNKS - fetched}
          maxDisplay={90}
        />
      </Section>

      {/* Summary stats */}
      {(data.topScore != null || data.docCount != null) && (
        <Section title="">
          <p className="text-sm text-neutral-600">
            {data.chunkCount} chunks across {data.docCount ?? sourceGroups.length} sources
            {data.topScore != null && ` · top ${data.topScore.toFixed(3)}`}
          </p>
        </Section>
      )}

      {/* Per-source breakdown */}
      <Section title="">
        <div className="flex flex-wrap gap-x-8 gap-y-4">
          {sourceGroups.map((g) => (
            <div key={g.label}>
              <p className="mb-2 text-sm font-bold text-neutral-900">
                {g.count} {g.label} Chunks
              </p>
              <SquareGrid count={g.count} color={g.color} />
            </div>
          ))}
        </div>
      </Section>

      {/* Authority + scores */}
      {((data.authorityBreakdown && Object.keys(data.authorityBreakdown).length > 0) ||
        (data.scoreBuckets && Object.keys(data.scoreBuckets).length > 0)) && (
        <div className="grid gap-6 border-t border-neutral-200 py-5 md:grid-cols-2">
          {data.authorityBreakdown && Object.keys(data.authorityBreakdown).length > 0 && (
            <div>
              <h3 className="text-base font-bold text-neutral-900">Authority mix</h3>
              <div className="mt-3">
                <AuthorityBreakdownBar breakdown={data.authorityBreakdown} />
              </div>
            </div>
          )}
          {data.scoreBuckets && Object.keys(data.scoreBuckets).length > 0 && (
            <div>
              <h3 className="text-base font-bold text-neutral-900">Score distribution</h3>
              <div className="mt-3">
                <ScoreBucketsChart buckets={data.scoreBuckets} />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Diversity cap */}
      <Section title="Diversity Cap" subtitle={`Max ${DIVERSITY_CAP} chunks per document`}>
        <div className="flex flex-wrap" style={{ gap: SQUARE_GAP }}>
          {diversityEntries.flatMap((entry) =>
            Array.from({ length: entry.total }, (_, i) => (
              <div
                key={`${entry.docId}-${i}`}
                style={{
                  width: SQUARE_SIZE,
                  height: SQUARE_SIZE,
                  borderRadius: 2,
                  backgroundColor: i < entry.kept ? entry.color : LIGHT_GRAY,
                  opacity: i < entry.kept ? 1 : 0.5,
                }}
              />
            ))
          )}
        </div>
      </Section>

      {/* Enrichments */}
      {(data.targetWpamYear != null || (data.caseLawCount ?? 0) > 0) && (
        <Section title="Enrichments">
          <div className="space-y-1 text-sm text-neutral-600">
            {data.targetWpamYear != null && <p>WPAM edition filter → {data.targetWpamYear}</p>}
            {(data.caseLawCount ?? 0) > 0 && (
              <p>
                {data.caseLawCount} case law citation{data.caseLawCount === 1 ? '' : 's'} discovered
              </p>
            )}
          </div>
        </Section>
      )}

      {/* Auto-backfill */}
      {backfill.length > 0 && (
        <Section title="Auto-Backfill" subtitle="Follow CITES edges to statutes">
          <div className="flex flex-col gap-3">
            {backfill.map((b) => (
              <div key={b.chunkId} className="flex items-center gap-3">
                <div style={{ width: 20, height: 20, borderRadius: 2, backgroundColor: COLOR.wpam }} />
                <svg width="100" height="16" className="shrink-0">
                  <line x1="0" y1="8" x2="80" y2="8" stroke={COLOR.wpam} strokeWidth="2" />
                  <polygon points="80,4 88,8 80,12" fill={COLOR.wpam} />
                </svg>
                <div className="flex items-center gap-2">
                  <div style={{ width: 18, height: 18, borderRadius: '50%', backgroundColor: COLOR.statute }} />
                  <span className="text-sm text-neutral-700">{backfillLabel(b.chunkId)}</span>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
