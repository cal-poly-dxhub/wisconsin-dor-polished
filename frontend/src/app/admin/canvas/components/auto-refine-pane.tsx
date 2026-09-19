'use client';

import { Wand2 } from 'lucide-react';

// Turn-0 query refinement. The old `refine_query` TOOL is gone; refinement is
// now an automatic stage (agent_tools/stages/auto_refine.py, plus the turn-0
// pass in loop/phase_a.py) that emits a `tool_result` named `auto_refine` with
// no matching tool_call. It only fires when there is chat history to resolve a
// follow-up against.

interface AutoRefineData {
  refined: boolean;
  refinedQuery: string;
  summary: string;
  latencyMs?: number;
}

export function AutoRefinePane({ data }: { data: AutoRefineData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Auto Refine</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>

      <div className="mt-3 flex items-start gap-3">
        <Wand2 className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" />
        {data.refined ? (
          <p className="text-sm leading-relaxed text-neutral-700">
            &ldquo;{data.refinedQuery}&rdquo;
          </p>
        ) : (
          <p className="text-sm text-neutral-500">
            {data.summary || 'No refinement needed — the question stands on its own.'}
          </p>
        )}
      </div>

      {data.refined && (
        <p className="mt-2 pl-8 text-xs text-neutral-400">
          Follow-up resolved against the conversation before the FAQ seed.
        </p>
      )}
    </div>
  );
}
