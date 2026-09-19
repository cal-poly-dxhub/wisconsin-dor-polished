'use client';

import { GitBranch, ListTree } from 'lucide-react';

// Decision-flowchart panes.
//
// `get_flowchart` arrives on the trace in two shapes and both land here:
//   - ROUTER SEED (turn 0, no matching tool_call): emitted by the pre-loop
//     flowchart router in loop/phase_a.py. Metadata carries `seeded: true`
//     plus the gate values `routerScore` / `routerMatchedOn` (which candidate
//     phrasing matched: original | history | refined).
//   - TOOL CALL: the agent fetched the chart itself. Same metadata minus the
//     seed/router fields (see tracing/summaries.py `get_flowchart`).
// Both carry flowchartId / wpamPage / sourceUrl, and the chart title rides on
// the result's docTitles[0].

interface GetFlowchartData {
  flowchartId: string;
  title: string;
  seeded: boolean;
  routerScore?: number;
  routerMatchedOn?: string;
  wpamPage?: string;
  sourceUrl?: string;
  summary: string;
  latencyMs?: number;
}

const MATCHED_ON_LABEL: Record<string, string> = {
  original: "the user's own words",
  history: 'the previous question',
  refined: 'the auto-refined query',
};

export function GetFlowchartPane({ data }: { data: GetFlowchartData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Decision Flowchart</h2>
        {data.seeded ? (
          <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-violet-700">
            Router seed
          </span>
        ) : (
          <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-neutral-500">
            Tool call
          </span>
        )}
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <GitBranch className="h-5 w-5 shrink-0 text-violet-500" />
        <span className="text-sm font-medium text-neutral-800">
          {data.title || data.flowchartId || 'unknown chart'}
        </span>
      </div>
      {data.flowchartId && (
        <p className="mt-1 pl-8 font-mono text-[11px] text-neutral-400">{data.flowchartId}</p>
      )}

      {/* Router gate values — why this chart was seeded (or why it wasn't a
          close call). Only the seed path carries them. */}
      {data.routerScore != null && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-violet-50 px-3 py-2 text-xs text-violet-900">
          <span>
            Router score <span className="font-mono font-semibold tabular-nums">{data.routerScore.toFixed(3)}</span>
          </span>
          {data.routerMatchedOn && (
            <span className="text-violet-700/80">
              matched on {MATCHED_ON_LABEL[data.routerMatchedOn] ?? data.routerMatchedOn}
            </span>
          )}
        </div>
      )}

      <dl className="mt-3 space-y-1 text-xs text-neutral-500">
        {data.wpamPage && (
          <div className="flex gap-2">
            <dt className="text-neutral-400">WPAM page</dt>
            <dd className="text-neutral-700">{data.wpamPage}</dd>
          </div>
        )}
        {data.sourceUrl && (
          <div className="flex gap-2 overflow-hidden">
            <dt className="shrink-0 text-neutral-400">Source</dt>
            <dd className="truncate text-neutral-600">{data.sourceUrl}</dd>
          </div>
        )}
      </dl>

      {data.summary && <p className="mt-3 text-xs text-neutral-500">{data.summary}</p>}
    </div>
  );
}

interface ListFlowchartsData {
  summary: string;
  latencyMs?: number;
}

export function ListFlowchartsPane({ data }: { data: ListFlowchartsData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Flowchart Registry</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <ListTree className="h-5 w-5 text-neutral-400" />
        <span className="text-sm text-neutral-700">{data.summary || 'Listed decision flowcharts'}</span>
      </div>
    </div>
  );
}
