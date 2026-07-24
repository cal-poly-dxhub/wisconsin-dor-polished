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
  docTypeLabel,
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

function MergedDocChunkRow({
  docChunks,
  additiveDocChunks,
}: {
  docChunks: Record<string, number>;
  additiveDocChunks?: Record<string, number>;
}) {
  // Merge by doc-type label: solid count from docChunks, hatched count from additiveDocChunks
  const byType = new Map<string, { label: string; color: string; solid: number; hatched: number }>();
  for (const [docId, count] of Object.entries(docChunks)) {
    const label = docTypeLabel(docId);
    const existing = byType.get(label);
    if (existing) {
      existing.solid += count;
    } else {
      byType.set(label, { label, color: docTypeColor(docId), solid: count, hatched: 0 });
    }
  }
  for (const [docId, count] of Object.entries(additiveDocChunks || {})) {
    const label = docTypeLabel(docId);
    const existing = byType.get(label);
    if (existing) {
      existing.hatched += count;
    } else {
      byType.set(label, { label, color: docTypeColor(docId), solid: 0, hatched: count });
    }
  }
  const groups = Array.from(byType.values());
  if (groups.length === 0) return <p className="text-sm text-neutral-400">No chunks</p>;

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3">
      {groups.map((g) => (
        <div key={g.label}>
          <p className="mb-1.5 text-sm font-bold text-neutral-900">
            {g.solid + g.hatched} {g.label}
          </p>
          <div className="flex flex-wrap" style={{ gap: SQUARE_GAP }}>
            {Array.from({ length: g.solid }, (_, i) => (
              <div
                key={`s-${i}`}
                style={{
                  width: SQUARE_SIZE,
                  height: SQUARE_SIZE,
                  borderRadius: 2,
                  backgroundColor: g.color,
                }}
              />
            ))}
            {Array.from({ length: g.hatched }, (_, i) => (
              <div
                key={`h-${i}`}
                style={{
                  width: SQUARE_SIZE,
                  height: SQUARE_SIZE,
                  borderRadius: 2,
                  backgroundColor: g.color,
                  backgroundImage: `repeating-linear-gradient(-45deg, transparent, transparent 3px, rgba(255,255,255,0.35) 3px, rgba(255,255,255,0.35) 6px)`,
                  border: `2px dashed ${BROAD_ACCENT}`,
                  boxSizing: 'border-box' as const,
                }}
              />
            ))}
          </div>
        </div>
      ))}
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

      <ArmSection title={additiveDocChunks && Object.keys(additiveDocChunks).length > 0 ? "Chunks by source — additive merge only" : "Chunks by source"}>
        <MergedDocChunkRow docChunks={docChunks} additiveDocChunks={additiveDocChunks} />
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

      {((data.statuteBackfill?.length ?? 0) > 0 || (data.caselawBackfill?.length ?? 0) > 0) && (
        <div className="border-t border-neutral-200 pt-5">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_1.5fr] gap-14">
            {/* Left: title + description */}
            <div>
              <h2 className="text-2xl font-bold text-neutral-900">Auto Backfill</h2>
              <p className="mt-3 text-sm leading-6 text-neutral-900">
                Runs on the narrow arm after diversity cap. Takes the top 3 source chunks (non-statute) and follows their citation edges to discover which statute sections they reference, resolving stubs to the actual statute text (cap 3 sections). Then takes those discovered statutes and traverses incoming citation edges from case-law chunks in one batched query (fetch 200, hard cap 300) to find opinions that interpret those statutes, ranked by cosine similarity, diversified to 1 chunk per case, returning up to 5.
              </p>
            </div>

            {/* Right: backfill results stacked */}
            <div className="flex flex-col gap-4">
              {(data.statuteBackfill?.length ?? 0) > 0 && (
                <div>
                  <h3 className="text-base font-bold text-neutral-900">Statute Backfill</h3>
                  <p className="mt-0.5 text-sm text-neutral-500">Follow CITES edges from top chunks</p>
                  <div className="mt-2 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-px bg-neutral-200 rounded-md overflow-hidden border border-neutral-200">
                    {data.statuteBackfill!.map((b) => (
                      <div key={b.chunkId} className="bg-white px-3 py-3 flex items-center gap-2.5">
                        <div style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: COLOR.statute, flexShrink: 0 }} />
                        <div className="min-w-0">
                          <span className="text-sm font-medium text-neutral-800 block">{backfillLabel(b)}</span>
                          <span className="text-xs text-neutral-400">from rank {b.sourceRank} chunk</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(data.caselawBackfill?.length ?? 0) > 0 && (
                <div>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <h3 className="text-base font-bold text-neutral-900">Case-Law Backfill</h3>
                    {data.caselawBackfillMeta && (
                      <span className="rounded-full border border-neutral-300 bg-neutral-50 px-2.5 py-0.5 text-xs font-medium text-neutral-600">
                        Fetched {data.caselawBackfillMeta.candidateCount ?? 0} candidates
                      </span>
                    )}
                    {data.caselawBackfillMeta?.latencyMs != null && data.caselawBackfillMeta.latencyMs > 0 && (
                      <span className="text-lg font-normal text-neutral-400">{data.caselawBackfillMeta.latencyMs}ms</span>
                    )}
                  </div>
                  <p className="mt-0.5 text-sm text-neutral-500">
                    Found via {data.caselawBackfillMeta?.stubsSearched?.map(s => s.replace('WIS-STAT-', '§ ')).join(', ') ?? 'statute stubs'}
                  </p>
                  <div className="mt-2 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-px bg-neutral-200 rounded-md overflow-hidden border border-neutral-200">
                    {data.caselawBackfill!.map((c, i) => (
                      <div key={`${c.caseId}-${i}`} className="bg-white px-3 py-3 flex flex-col gap-1.5">
                        <span className="text-sm font-medium text-neutral-800 line-clamp-2 leading-snug">
                          {c.title || c.citation || c.caseId}
                        </span>
                        <span className="text-xs text-neutral-500 flex items-center gap-1 flex-wrap">
                          <span>cites</span>
                          {c.citedStubs?.map((s, j) => (
                            <span key={j} className="inline-flex items-center gap-0.5">
                              <span style={{ width: 7, height: 7, borderRadius: '50%', backgroundColor: COLOR.statute, display: 'inline-block' }} />
                              <span>{s.replace('WIS-STAT-', '§ ')}</span>
                            </span>
                          )) || <span>statute</span>}
                        </span>
                        {c.relevanceScore != null && (
                          <div className="flex items-center gap-1.5 mt-1">
                            <div className="w-10 h-1 rounded-full bg-neutral-200 overflow-hidden">
                              <div
                                className="h-full rounded-full bg-neutral-600"
                                style={{ width: `${Math.max(0, Math.min(1, c.relevanceScore)) * 100}%` }}
                              />
                            </div>
                            <span className="text-xs tabular-nums text-neutral-400">{c.relevanceScore.toFixed(2)}</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
