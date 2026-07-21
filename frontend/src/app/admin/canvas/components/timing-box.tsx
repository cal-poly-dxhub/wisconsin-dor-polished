'use client';

function fmt(ms: number): string {
  const total = Math.max(0, ms);
  const s = Math.floor(total / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  const cs = String(Math.floor((total % 1000) / 10)).padStart(2, '0');
  return `${mm}:${ss}.${cs}`;
}

export function TimingBox({
  totalMs,
  turnMs,
  currentTurn,
  done,
}: {
  totalMs: number;
  turnMs: number;
  currentTurn: number | null;
  done: boolean;
}) {
  return (
    <div className="absolute top-4 right-4 z-20 select-none rounded-xl border border-neutral-200 bg-white px-5 py-3 shadow-sm">
      <div className="font-mono text-4xl font-semibold tabular-nums leading-none tracking-tight text-neutral-900">
        {fmt(totalMs)}
      </div>
      <div className="mt-2 flex items-center justify-between gap-6 text-xs text-neutral-500">
        <span className="font-medium">
          {currentTurn != null ? `Turn ${currentTurn}` : done ? 'Done' : 'Idle'}
        </span>
        <span className="font-mono tabular-nums">{fmt(turnMs)}</span>
      </div>
    </div>
  );
}
