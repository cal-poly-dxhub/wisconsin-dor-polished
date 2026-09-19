'use client';

// The two cheapest phases the Lambda emits before any work happens:
// `request_received` (the true start of the request) and `history_loaded`
// (how many prior turns were folded into the context). They carry no
// metadata worth a pane, so they render as a one-line strip above the turns.

export interface RequestHeaderData {
  received: boolean;
  historyTurns: number | null;
}

export function RequestHeader({ data }: { data: RequestHeaderData }) {
  if (!data.received && data.historyTurns == null) return null;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-1.5">
      <span className="text-xs font-bold uppercase tracking-wide text-neutral-500">
        Request received
      </span>
      <span className="text-xs text-neutral-400">
        {data.historyTurns != null
          ? `${data.historyTurns} prior turn${data.historyTurns === 1 ? '' : 's'} loaded`
          : 'no conversation history'}
      </span>
    </div>
  );
}
