'use client';

import { CORPUS_TOTAL_CHUNKS } from '../fixtures/demo-trace';
import {
  AuthorityBreakdownBar,
  BROAD_ACCENT,
  COLOR,
  DIVERSITY_CAP,
  LIGHT_GRAY,
  ScoreBucketsChart,
  SquareGrid,
  SQUARE_GAP,
  SQUARE_SIZE,
  VectorSearchBaseData,
  backfillLabel,
  docTypeColor,
  groupDocChunks,
  resolveAuthorityBreakdown,
  resolveScoreBuckets,
  Section,
} from './vector-search-shared';

export interface InitialVectorSearchData extends VectorSearchBaseData {
  refinedQuery?: string;
  broadQuery?: string;
  broadSkipped?: boolean;
  broadDiscovery?: { docId: string; score: number }[];
  broadDocChunks?: Record<string, number>;
  broadFullDocChunks?: Record<string, number>;
  broadPreDedupCount?: number;
  broadKeptCount?: number;
  broadAuthorityBreakdown?: Record<string, number>;
  broadScoreBuckets?: Record<string, number>;
  broadTopScore?: number;
}

interface ArmColumnProps {
  title: string;
  queryHint: string;
  query?: string;
  armLimits: string;
  preDedupCount: number;
  keptCount: number;
  additiveCount?: number;
  docChunks: Record<string, number>;
  additiveDocChunks?: Record<string, number>;
  authorityBreakdown?: Record<string, number>;
  scoreBuckets?: Record<string, number>;
  scoreFallback?: number[];
  topScore?: number;
  skipped?: boolean;
  skippedMessage?: string;
  accent?: boolean;
}

function ArmSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-neutral-200 py-3 first:border-t-0 first:pt-0">
      <h4 className="text-xs font-bold uppercase tracking-wide text-neutral-900">{title}</h4>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function DocChunkRow({
  docChunks,
  hatched = false,
}: {
  docChunks: Record<string, number>;
  hatched?: boolean;
}) {
  const groups = groupDocChunks(docChunks);
  if (groups.length === 0) {
    return <p className="text-sm text-neutral-400">No chunks</p>;
  }

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3">
      {groups.map((g) => (
        <div key={g.label}>
          <p className="mb-1.5 text-sm font-bold text-neutral-900">
            {g.count} {g.label}
          </p>
          <SquareGrid count={g.count} color={g.color} hatched={hatched} />
        </div>
      ))}
    </div>
  );
}

function PipelineInline({
  preDedup,
  kept,
  topScore,
  additiveCount,
}: {
  preDedup: number;
  kept: number;
  topScore?: number;
  additiveCount?: number;
}) {
  return (
    <p className="text-xs tabular-nums text-neutral-600">
      <span className="font-medium text-neutral-800">{preDedup}</span>
      <span className="mx-1 text-neutral-300">→</span>
      <span className="font-semibold text-neutral-900">{kept} kept</span>
      {topScore != null && (
        <span className="text-neutral-500"> · top {topScore.toFixed(3)}</span>
      )}
      {additiveCount != null && (
        <span className="text-teal-700"> · {additiveCount} additive</span>
      )}
    </p>
  );
}

function ArmColumn({
  title,
  queryHint,
  query,
  armLimits,
  preDedupCount,
  keptCount,
  additiveCount,
  docChunks,
  additiveDocChunks,
  authorityBreakdown,
  scoreBuckets,
  scoreFallback = [],
  topScore,
  skipped,
  skippedMessage,
  accent,
}: ArmColumnProps) {
  const capPerDoc = DIVERSITY_CAP;
  const authority = resolveAuthorityBreakdown(authorityBreakdown, docChunks);
  const scores = resolveScoreBuckets(scoreBuckets, scoreFallback);
  const hasAuthority = Object.keys(authority).length > 0;
  const hasScores = Object.keys(scores).length > 0;

  const diversityEntries = Object.entries(docChunks).map(([docId, total]) => ({
    docId,
    color: docTypeColor(docId),
    total,
    kept: Math.min(total, capPerDoc),
  }));

  if (skipped) {
    return (
      <div
        className="flex flex-col rounded-lg border border-dashed p-4"
        style={{ borderColor: BROAD_ACCENT }}
      >
        <h3 className="text-sm font-bold uppercase tracking-wide text-neutral-900">{title}</h3>
        <p className="mt-3 text-sm text-neutral-500">{skippedMessage}</p>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col"
      style={accent ? { borderLeft: `3px solid ${BROAD_ACCENT}`, paddingLeft: 16 } : undefined}
    >
      <div className="mb-3">
        <h3 className="text-sm font-bold uppercase tracking-wide text-neutral-900">{title}</h3>
        <p className="mt-0.5 text-xs text-neutral-500">{queryHint} · {armLimits}</p>
        {query && (
          <div
            className={`mt-2 rounded-md px-2.5 py-1.5 text-xs ${
              accent ? 'bg-teal-50 text-teal-900' : 'bg-neutral-50 text-neutral-700'
            }`}
          >
            {query}
          </div>
        )}
        <div className="mt-2">
          <PipelineInline
            preDedup={preDedupCount}
            kept={keptCount}
            topScore={topScore}
            additiveCount={additiveCount}
          />
        </div>
      </div>

      <ArmSection
        title={`${preDedupCount} Neptune hits (of ${(CORPUS_TOTAL_CHUNKS / 1000).toFixed(1)}K corpus)`}
      >
        <SquareGrid
          count={CORPUS_TOTAL_CHUNKS}
          color={COLOR.chunk}
          fadedCount={CORPUS_TOTAL_CHUNKS - preDedupCount}
          maxDisplay={72}
        />
      </ArmSection>

      <ArmSection title="Chunks by source">
        <DocChunkRow docChunks={docChunks} hatched={accent && !additiveDocChunks} />
        {additiveDocChunks && Object.keys(additiveDocChunks).length > 0 && (
          <div className="mt-3 border-t border-neutral-100 pt-3">
            <p className="mb-2 text-xs font-medium text-neutral-500">Additive merge only</p>
            <DocChunkRow docChunks={additiveDocChunks} hatched />
          </div>
        )}
      </ArmSection>

      <ArmSection title="Authority mix">
        {hasAuthority ? (
          <AuthorityBreakdownBar breakdown={authority} />
        ) : (
          <p className="text-xs text-neutral-400">No authority data</p>
        )}
      </ArmSection>

      <ArmSection title="Score distribution">
        {hasScores ? (
          <ScoreBucketsChart buckets={scores} />
        ) : (
          <p className="text-xs text-neutral-400">No score data</p>
        )}
      </ArmSection>

      {diversityEntries.length > 0 && (
        <ArmSection title="Diversity cap">
          <p className="mb-2 text-xs text-neutral-500">Max {capPerDoc} chunks per document</p>
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
        </ArmSection>
      )}
    </div>
  );
}

export function InitialVectorSearchPane({ data }: { data: InitialVectorSearchData }) {
  const broadAdditive = data.broadDocChunks ?? {};
  const broadTotal = data.broadChunkCount ?? Object.values(broadAdditive).reduce((a, b) => a + b, 0);
  const narrowTotal = data.chunkCount;
  const mergedTotal = data.totalChunkCount ?? narrowTotal + broadTotal;
  const topK = data.topK ?? 15;
  const capPerDoc = data.diversityCapPerDoc ?? DIVERSITY_CAP;
  const armLimits = `Cap ${capPerDoc}/doc · max ${topK}`;

  const broadFull = data.broadFullDocChunks ?? broadAdditive;
  const broadKept = data.broadKeptCount ?? Object.values(broadFull).reduce((a, b) => a + b, 0);
  const broadScoreFallback = (data.broadDiscovery ?? []).map((d) => d.score);

  return (
    <div className="flex flex-col px-5 py-5">
      <div className="mb-6">
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">Initial Vector Search</h2>
          <span className="rounded-full border border-neutral-300 bg-neutral-50 px-2.5 py-0.5 text-xs font-medium text-neutral-600">
            Seeded · Dual-arm
          </span>
          {data.latencyMs > 0 && (
            <span className="text-lg font-normal text-neutral-400">{data.latencyMs}ms</span>
          )}
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Q</span>
          <span className="text-sm text-neutral-600">{data.query}</span>
        </div>
        <p className="mt-2 text-sm text-neutral-600">
          <span className="font-semibold text-neutral-900">{narrowTotal} narrow</span>
          {data.broadSkipped ? (
            <> · broad arm skipped</>
          ) : (
            <>
              {' '}
              + <span className="font-semibold text-neutral-900">{broadTotal} additive broad</span>
            </>
          )}
          {' '}
          = <span className="font-semibold text-neutral-900">{mergedTotal} chunks to model</span>
          <span className="text-neutral-400"> (max {topK * 2})</span>
        </p>
      </div>

      <div className="grid items-start gap-x-8 gap-y-6 md:grid-cols-2">
        <ArmColumn
          title="Narrow arm"
          queryHint="Auto-refined query"
          query={data.refinedQuery}
          armLimits={armLimits}
          preDedupCount={data.preDedupCount}
          keptCount={narrowTotal}
          docChunks={data.docChunks}
          authorityBreakdown={data.authorityBreakdown}
          scoreBuckets={data.scoreBuckets}
          topScore={data.topScore}
        />

        <ArmColumn
          title="Broad arm"
          queryHint="Verbatim question"
          query={data.broadQuery}
          armLimits={armLimits}
          preDedupCount={data.broadPreDedupCount ?? 0}
          keptCount={broadKept}
          additiveCount={data.broadSkipped ? undefined : broadTotal}
          docChunks={broadFull}
          additiveDocChunks={data.broadSkipped ? undefined : broadAdditive}
          authorityBreakdown={data.broadAuthorityBreakdown}
          scoreBuckets={data.broadScoreBuckets}
          scoreFallback={broadScoreFallback}
          topScore={data.broadTopScore}
          skipped={data.broadSkipped}
          skippedMessage="Skipped — refined query matches verbatim question"
          accent
        />
      </div>

      {(data.targetWpamYear != null ||
        (data.statuteBackfill?.length ?? 0) > 0 ||
        (data.caselawBackfill?.length ?? 0) > 0 ||
        (data.caseLawCount ?? 0) > 0) && (
        <Section title="Enrichments (shared)">
          <div className="space-y-2 text-sm text-neutral-600">
            {data.targetWpamYear != null && <p>WPAM edition filter → {data.targetWpamYear}</p>}
            {(data.caseLawCount ?? 0) > 0 && (
              <p>
                {data.caseLawCount} case law citation{data.caseLawCount === 1 ? '' : 's'} discovered
              </p>
            )}
          </div>
        </Section>
      )}

      {(data.caselawBackfill?.length ?? 0) > 0 && (
        <Section
          title="Case-Law Backfill"
          subtitle="Chunk-level CITES edges from statute stubs"
        >
          {data.caselawBackfillMeta && (
            <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-500">
              <span>
                {data.caselawBackfillMeta.stubsSearched?.length ?? 0} stub{(data.caselawBackfillMeta.stubsSearched?.length ?? 0) === 1 ? '' : 's'} searched
              </span>
              <span>{data.caselawBackfillMeta.candidateCount ?? 0} candidates (fetch {data.caselawBackfillMeta.fetchK ?? 0})</span>
              {data.caselawBackfillMeta.fetchSaturated && (
                <span className="font-medium text-amber-600">⚠ saturated</span>
              )}
              {(data.caselawBackfillMeta.latencyMs ?? 0) > 0 && (
                <span>{data.caselawBackfillMeta.latencyMs}ms</span>
              )}
            </div>
          )}
          <div className="flex flex-col gap-3">
            {data.caselawBackfill!.map((c) => (
              <div key={c.caseId} className="flex items-center gap-3">
                <div style={{ width: 18, height: 18, borderRadius: '50%', backgroundColor: COLOR.statute }} title="Statute stub" />
                <svg width="60" height="16" className="shrink-0">
                  <line x1="0" y1="8" x2="42" y2="8" stroke={COLOR.caseLaw} strokeWidth="2" />
                  <polygon points="42,4 50,8 42,12" fill={COLOR.caseLaw} />
                  <text x="25" y="5" textAnchor="middle" fontSize="7" fill={COLOR.caseLaw}>CITES</text>
                </svg>
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <div style={{ width: 18, height: 18, borderRadius: 2, backgroundColor: COLOR.caseLaw }} />
                  <div className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-neutral-700">
                      {c.title || c.citation || c.caseId}
                    </span>
                    <span className="flex gap-2 text-[10px] text-neutral-400">
                      {c.contentRole && <span className="rounded bg-purple-50 px-1 text-purple-700">{c.contentRole}</span>}
                      {c.relevanceScore != null && <span>score {c.relevanceScore.toFixed(3)}</span>}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {(data.statuteBackfill?.length ?? 0) > 0 && (
        <Section title="Auto-Backfill" subtitle="Follow CITES edges to statutes">
          <div className="flex flex-col gap-3">
            {data.statuteBackfill!.map((b) => (
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
