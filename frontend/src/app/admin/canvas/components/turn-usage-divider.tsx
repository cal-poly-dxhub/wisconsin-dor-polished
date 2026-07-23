'use client';

interface TurnStatusBarData {
  turn: number;
  inputTokens?: number;
  outputTokens?: number;
  cumulativeTotal?: number;
  bedrockLatencyMs?: number;
  label?: string;
}

export function TurnStatusBar({ data }: { data: TurnStatusBarData }) {
  const hasUsage = (data.inputTokens ?? 0) > 0 || (data.outputTokens ?? 0) > 0;
  return (
    <div className="flex items-center gap-2 border-b border-neutral-100 bg-neutral-50 px-4 py-1.5">
      <span className="text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        {data.label ?? `Turn ${data.turn}`}
      </span>
      <div className="flex-1" />
      {hasUsage && (
        <div className="flex items-center gap-2 text-[10px] tabular-nums text-neutral-500">
          <span>↓{fmtK(data.inputTokens ?? 0)}</span>
          <span>↑{fmtK(data.outputTokens ?? 0)}</span>
          {(data.cumulativeTotal ?? 0) > 0 && (
            <span className="font-semibold text-neutral-700">Σ{fmtK(data.cumulativeTotal!)}</span>
          )}
        </div>
      )}
      {data.bedrockLatencyMs != null && data.bedrockLatencyMs > 0 && (
        <span className="text-[10px] tabular-nums text-neutral-400">{data.bedrockLatencyMs}ms</span>
      )}
    </div>
  );
}

function fmtK(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}
