// Hand-maintained mirror of the agent's tool registry.
//
// SOURCE OF TRUTH: backend/lambdas/agentic_retrieval/agent_tools/definitions.py
// (TOOL_DEFINITIONS) — every `"name"` in that list, in declaration order. The
// executor dispatches these in agent_tools/executor.py.
//
// The canvas renders one pane per tool; anything without a pane falls back to
// PlaceholderPane ("Coming soon"), which is a silent degradation. The test in
// test/pane-map.test.tsx asserts this list is fully covered by the canvas pane
// map, so adding a backend tool without a pane fails CI instead of quietly
// rendering a placeholder.
//
// NOT in this list, deliberately:
//   - `clarify`   — executable in executor.py but absent from TOOL_DEFINITIONS,
//                   so the model can never call it. Its replacement is the
//                   post-retrieval adequacy judge.
//   - `refine_query` — removed; refinement is now the `auto_refine` pipeline
//                   stage, which emits a tool_result under that name.
export const BACKEND_TOOL_NAMES = [
  'faq_search',
  'vector_search',
  'search_document',
  'list_sections',
  'get_section',
  'get_document',
  'get_neighbors',
  'get_authority_chain',
  'list_framework_docs',
  'find_case_law',
  'fetch_case_opinion',
  'list_worksheets',
  'get_worksheet',
  'list_flowcharts',
  'get_flowchart',
  'prepare_answer',
] as const;

export type BackendToolName = (typeof BACKEND_TOOL_NAMES)[number];

// Pipeline stages that emit a `tool_result` without a matching `tool_call`.
// They are not model-callable tools, but they do reach the canvas as panes.
// See agent_tools/pipeline.py (VECTOR_SEARCH_STAGES) and loop/phase_a.py.
export const SYNTHETIC_TOOL_NAMES = ['auto_refine'] as const;
