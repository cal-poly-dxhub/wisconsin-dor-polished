'use client';

interface GetAuthorityChainData {
  chainLength: number;
  latencyMs?: number;
  summary: string;
}

export function GetAuthorityChainPane({ data }: { data: GetAuthorityChainData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Authority Chain</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <p className="mt-3 text-sm text-neutral-600">{data.summary}</p>
      <div className="mt-3 flex items-center gap-2">
        {Array.from({ length: data.chainLength }, (_, i) => (
          <div key={i} className="flex items-center gap-1">
            <div className="h-4 w-4 rounded-sm bg-neutral-800" />
            {i < data.chainLength - 1 && (
              <span className="text-xs text-neutral-300">→</span>
            )}
          </div>
        ))}
        {data.chainLength === 0 && (
          <span className="text-sm text-neutral-400">Empty chain</span>
        )}
      </div>
    </div>
  );
}
