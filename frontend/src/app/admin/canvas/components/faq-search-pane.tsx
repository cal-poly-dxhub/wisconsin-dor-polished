'use client';

const BAR_HEIGHT = 140;
const FAQ_COLOR = '#CA8A04';
const FAQ_FADED = '#E5E7EB';
const THRESHOLD_COLOR = '#EF4444';

export interface FaqSearchData {
  query: string;
  latencyMs?: number;
  faqCount: number;
  topScore: number;
  faqScoreThreshold: number;
  faqScores: number[];
  topFaqSnippet: string;
  transitionLabel?: string;
}

export function FaqSearchPane({ data }: { data: FaqSearchData }) {
  const threshold = data.faqScoreThreshold;
  const scores =
    data.faqScores.length > 0
      ? data.faqScores
      : Array.from({ length: data.faqCount }, (_, i) => (i === 0 ? data.topScore : 0));
  const ceilScore = Math.max(...scores, threshold, 0.01) * 1.15;
  const thresholdPct = (threshold / ceilScore) * 100;
  const aboveCount = scores.filter((s) => s >= threshold).length;
  const highConfidence = data.topScore >= threshold;

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">FAQ Search</h2>
          {data.latencyMs != null && (
            <span className="text-lg text-neutral-400 font-normal">{data.latencyMs}ms</span>
          )}
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Q</span>
          <span className="text-sm text-neutral-600">{data.query}</span>
        </div>
      </div>

      {data.faqCount === 0 ? (
        <p className="text-sm text-neutral-500">No FAQ matches</p>
      ) : (
        <>
          {/* Status badge */}
          <div
            className={`mb-4 inline-flex w-fit rounded-full px-3 py-1 text-xs font-medium ${
              highConfidence
                ? 'bg-amber-50 text-amber-800 border border-amber-200'
                : 'bg-neutral-50 text-neutral-600 border border-neutral-200'
            }`}
          >
            {data.transitionLabel ??
              (highConfidence
                ? 'High-confidence match — supplementing with graph search'
                : 'Below threshold — searching knowledge graph')}
          </div>

          {/* Top snippet */}
          {data.topFaqSnippet && (
            <p className="mb-4 text-sm leading-relaxed text-neutral-600 line-clamp-3">
              {data.topFaqSnippet}
            </p>
          )}

          {/* Score bars */}
          <h3 className="text-base font-bold text-neutral-900 mb-2">
            Semantic Match Scores
          </h3>
          <div className="relative w-full overflow-visible" style={{ height: BAR_HEIGHT }}>
            <div className="flex h-full w-full items-end gap-1.5">
              {scores.map((score, i) => {
                const above = score >= threshold;
                const heightPct = (score / ceilScore) * 100;
                return (
                  <div
                    key={i}
                    className="relative flex h-full flex-1 flex-col items-center justify-end"
                  >
                    <div
                      className="w-full rounded-t-sm"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: above ? FAQ_COLOR : FAQ_FADED,
                        opacity: above ? 1 : 0.7,
                      }}
                    />
                    <span className="mt-1.5 text-[10px] tabular-nums text-neutral-400">
                      {score.toFixed(2)}
                    </span>
                  </div>
                );
              })}
            </div>
            <div
              className="pointer-events-none absolute left-0 right-0 z-10"
              style={{ bottom: `${thresholdPct}%` }}
            >
              <div
                className="w-full"
                style={{
                  height: 2,
                  backgroundImage: `repeating-linear-gradient(to right, ${THRESHOLD_COLOR}, ${THRESHOLD_COLOR} 6px, transparent 6px, transparent 12px)`,
                }}
              />
              <span className="absolute right-0 top-1.5 text-[10px] font-medium tabular-nums text-red-500">
                {threshold.toFixed(2)}
              </span>
            </div>
          </div>

          <p className="mt-3 text-xs text-neutral-500">
            {aboveCount} above threshold · {data.faqCount} total · top {data.topScore.toFixed(2)}
          </p>
        </>
      )}
    </div>
  );
}
