'use client';

interface TurnStatusBarData {
  turn: number;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadInputTokens?: number;
  cacheWriteInputTokens?: number;
  cumulativeTotal?: number;
  bedrockLatencyMs?: number;
  label?: string;
}

export function TurnStatusBar({ data }: { data: TurnStatusBarData }) {
  const cacheRead = data.cacheReadInputTokens ?? 0;
  const cacheWrite = data.cacheWriteInputTokens ?? 0;
  const uncached = data.inputTokens ?? 0;
  const effectiveInput = cacheRead + cacheWrite + uncached;
  const output = data.outputTokens ?? 0;
  const hasUsage = effectiveInput > 0 || output > 0;

  return (
    <div className="flex items-center gap-2 border-b border-neutral-100 bg-neutral-50 px-4 py-1.5">
      <span className="text-xs font-bold uppercase tracking-wide text-neutral-500">
        {data.label ?? `Turn ${data.turn}`}
      </span>
      <div className="flex-1" />
      {hasUsage && (
        <div className="flex items-center gap-2 text-[11px] tabular-nums text-neutral-500">
          <span>
            ↓ <span className="text-neutral-400">{fmtK(cacheRead)}</span>
            <span className="text-neutral-400"> cached</span>
            {cacheWrite > 0 && (
              <> + <span className="font-medium text-neutral-700">{fmtK(cacheWrite)}</span>
              <span className="text-neutral-400"> new</span></>
            )}
          </span>
          <span>
            ↑ <span className="font-medium text-neutral-700">{fmtK(output)}</span>
          </span>
          <span className="font-semibold text-neutral-700">Σ{fmtK(effectiveInput + output)}</span>
        </div>
      )}
      {data.bedrockLatencyMs != null && data.bedrockLatencyMs > 0 && (
        <span className="text-[11px] tabular-nums text-neutral-400">{fmtMs(data.bedrockLatencyMs)}</span>
      )}
    </div>
  );
}

function fmtK(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}
