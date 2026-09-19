'use client';

import { Gavel } from 'lucide-react';

// `find_case_law` resolves a case name or citation against the cached CaseLaw
// index. tracing/summaries.py has no branch for it, so the wire event carries
// only the generic "find_case_law complete" line plus latency — the search
// terms come from the tool input (devPayload.toolInput) and any resolved case
// ids ride on the result's docIds/docTitles.

interface FindCaseLawData {
  searchText: string;
  statuteId?: string;
  cases: { docId: string; title: string }[];
  status: 'ok' | 'miss';
  summary: string;
  latencyMs?: number;
}

export function FindCaseLawPane({ data }: { data: FindCaseLawData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Case Law Search</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <Gavel className="h-5 w-5 shrink-0 text-purple-500" />
        <span className="text-sm text-neutral-700">
          {data.searchText ? `“${data.searchText}”` : data.summary || 'case lookup'}
        </span>
      </div>
      {data.statuteId && (
        <p className="mt-1 pl-8 text-xs text-neutral-500">scoped to {data.statuteId}</p>
      )}

      {data.cases.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {data.cases.map((c) => (
            <li key={c.docId} className="flex items-center gap-2">
              <div className="h-3 w-3 shrink-0 rounded-sm bg-purple-400" />
              <span className="truncate text-xs text-neutral-700">{c.title || c.docId}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3 rounded-md bg-neutral-50 px-3 py-2">
          <p className="text-xs text-neutral-500">
            {data.status === 'miss'
              ? 'No CaseLaw node matches — the case is not in the graph.'
              : data.summary || 'No matching case node returned.'}
          </p>
        </div>
      )}
    </div>
  );
}
