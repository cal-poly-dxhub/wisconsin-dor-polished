'use client';

const GRID_COLS = 11;
const GRID_ROWS = 11;
const CELL = 22;
const GAP = 3;
const TOTAL = GRID_COLS * GRID_ROWS;
const GRID_W = GRID_COLS * (CELL + GAP) - GAP;

interface ChunkScore {
  chunkId: string;
  cosine: number;
  zScore: number | null;
  heading: string;
  included: boolean;
}

interface GetSectionData {
  docTitle: string;
  heading: string;
  query: string;
  filtered: boolean;
  sectionChunkCount: number;
  returnedChunkCount: number;
  chunkScores: ChunkScore[];
  latencyMs: number;
}

export function GetSectionPane({ data }: { data: GetSectionData }) {
  const total = data.sectionChunkCount;
  const passed = data.returnedChunkCount;
  const gridCells = Math.min(total, TOTAL);

  // Sort by cosine descending for the heatmap
  const sorted = [...data.chunkScores].sort((a, b) => b.cosine - a.cosine);
  const passedChunks = sorted.filter((c) => c.included);
  const maxCos = Math.max(...sorted.map((c) => c.cosine), 0.001);
  const minCos = Math.min(...sorted.map((c) => c.cosine), 0);
  const range = Math.max(maxCos - minCos, 0.0001);

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">Get Section</h2>
          <span className="text-lg text-neutral-400 font-normal">{data.latencyMs}ms</span>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Doc</span>
          <span className="text-sm text-neutral-600 truncate">{data.docTitle}</span>
        </div>
        {data.heading && (
          <p className="mt-1 text-xs text-neutral-500 pl-1">§ {data.heading}</p>
        )}
      </div>

      {/* Query used for similarity (only present on ranked calls) */}
      {data.filtered && (
        <div className="mb-4">
          <div className="flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
            <span className="text-sm font-semibold text-neutral-500">Q</span>
            <span className="text-sm text-neutral-600">{data.query}</span>
          </div>
        </div>
      )}

      {/* Section grid: black = passed, gray = filtered */}
      <div className="mb-1">
        <h3 className="text-base font-bold text-neutral-900">
          {passed} of {total} Chunks Returned
        </h3>
      </div>
      <div className="relative" style={{ width: GRID_W, height: Math.ceil(gridCells / GRID_COLS) * (CELL + GAP) - GAP }}>
        {Array.from({ length: gridCells }, (_, i) => {
          const col = i % GRID_COLS;
          const row = Math.floor(i / GRID_COLS);
          const isPassed = i < passed;
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
                backgroundColor: isPassed ? '#000' : '#E5E7EB',
              }}
            />
          );
        })}
      </div>

      {/* Ranked-call detail: cosine heatmap + z-score filter outcome */}
      {data.filtered ? (
        <>
          {/* Cosine heatmap of passed chunks */}
          <div className="mt-5">
            <h3 className="text-base font-bold text-neutral-900 mb-2">Cosine Similarity</h3>
            <div className="flex flex-wrap" style={{ gap: GAP }}>
              {passedChunks.map((c, i) => {
                const t = (c.cosine - minCos) / range;
                const lightness = 95 - t * 70; // dark for high, light for low
                return (
                  <div
                    key={c.chunkId || i}
                    style={{
                      width: CELL,
                      height: CELL,
                      borderRadius: 3,
                      backgroundColor: `hsl(0 0% ${lightness}%)`,
                    }}
                    title={`${c.heading || c.chunkId}\ncos: ${c.cosine.toFixed(3)}`}
                  />
                );
              })}
            </div>
          </div>

          {/* Z-score banner */}
          <div className="mt-5 rounded-md bg-neutral-100 px-3 py-2">
            <p className="text-xs font-medium text-neutral-600 tracking-wide uppercase">
              Applying z-score normalization to cosine scores
            </p>
          </div>

          {/* Final set that passed z-score */}
          <div className="mt-5">
            <h3 className="text-base font-bold text-neutral-900 mb-2">
              Passed Z-Score Filter
            </h3>
            <div className="flex flex-wrap" style={{ gap: GAP }}>
              {passedChunks.map((c, i) => (
                <div
                  key={c.chunkId || i}
                  className="flex items-center justify-center"
                  style={{
                    width: CELL,
                    height: CELL,
                    borderRadius: 3,
                    backgroundColor: '#000',
                  }}
                  title={`${c.heading}\nz: ${c.zScore != null ? c.zScore.toFixed(2) : '—'}`}
                />
              ))}
            </div>
            <p className="mt-2 text-sm text-neutral-600">
              <span className="font-semibold">{passedChunks.length}</span> chunks passed
            </p>
          </div>
        </>
      ) : (
        /* Unranked call: all section chunks returned in document order */
        <div className="mt-5 rounded-md bg-neutral-100 px-3 py-2">
          <p className="text-xs font-medium text-neutral-600 tracking-wide uppercase">
            Document-order fetch — all {total} chunks returned, no ranking applied
          </p>
        </div>
      )}
    </div>
  );
}
