'use client';

// Fallback titles only. Every tool in backend-tools.ts has a real pane in the
// canvas pane map (TOOL_PANES in canvas-view.tsx) — this map exists for the
// case the map is missing one, and for the synthetic pipeline-stage names the
// backend may add later.
const TOOL_TITLES: Record<string, string> = {
  get_neighbors: 'Graph Neighbors',
  get_section: 'Get Section',
  search_document: 'Document Search',
  list_sections: 'List Sections',
  get_document: 'Get Document',
  faq_search: 'FAQ Search',
  auto_refine: 'Auto Refine',
  find_case_law: 'Case Law Search',
  list_worksheets: 'Worksheet Registry',
  get_worksheet: 'TID Worksheet',
  list_flowcharts: 'Flowchart Registry',
  get_flowchart: 'Decision Flowchart',
  prepare_answer: 'Answer Synthesis',
};

export function PlaceholderPane({
  toolName,
  summary,
  latencyMs,
}: {
  toolName: string;
  summary: string;
  latencyMs?: number;
}) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-xl font-bold text-neutral-900">
          {TOOL_TITLES[toolName] ?? toolName}
        </h2>
        {latencyMs != null && (
          <span className="text-lg text-neutral-400 font-normal">{latencyMs}ms</span>
        )}
      </div>
      {summary && (
        <p className="mt-2 text-sm text-neutral-500">{summary}</p>
      )}
      <div className="mt-6 flex-1 flex items-center justify-center">
        <span className="text-xs text-neutral-300 uppercase tracking-widest">Coming soon</span>
      </div>
    </div>
  );
}
