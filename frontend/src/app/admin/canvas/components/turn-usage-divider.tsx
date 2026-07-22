'use client';

interface TurnUsageData {
  turn: number;
  inputTokens: number;
  outputTokens: number;
  cumulativeInput: number;
  cumulativeOutput: number;
  cumulativeTotal: number;
  bedrockLatencyMs?: number;
}

export function TurnUsageDivider({ data }: { data: TurnUsageData }) {
  return (
    <div className="col-span-full flex items-center gap-3 border-t border-neutral-200 px-5 py-2">
      <span className="text-xs font-semibold text-neutral-500">Turn {data.turn}</span>
      <div className="h-px flex-1 bg-neutral-200" />
      <div className="flex items-center gap-3 text-[11px] tabular-nums text-neutral-500">
        <span>
          <span className="text-neutral-400">↓</span>{' '}
          <span className="font-medium text-neutral-700">{fmtK(data.inputTokens)}</span>
        </span>
        <span>
          <span className="text-neutral-400">↑</span>{' '}
          <span className="font-medium text-neutral-700">{fmtK(data.outputTokens)}</span>
        </span>
        <span className="text-neutral-300">|</span>
        <span>
          Σ <span className="font-semibold text-neutral-900">{fmtK(data.cumulativeTotal)}</span>
          <span className="text-neutral-400"> ({fmtK(data.cumulativeInput)}↓ {fmtK(data.cumulativeOutput)}↑)</span>
        </span>
        {data.bedrockLatencyMs != null && data.bedrockLatencyMs > 0 && (
          <>
            <span className="text-neutral-300">|</span>
            <span className="text-neutral-400">{data.bedrockLatencyMs}ms</span>
          </>
        )}
      </div>
    </div>
  );
}

function fmtK(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}
