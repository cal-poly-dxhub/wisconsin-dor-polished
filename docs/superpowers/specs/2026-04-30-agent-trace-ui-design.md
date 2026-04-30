# Agent Trace UI — Design

**Date:** 2026-04-30
**Branch:** `feat/graphrag-migration`
**Status:** Approved for plan-writing

## Problem

The GraphRAG chat UI currently renders a static 3-step placeholder under
"Thinking for Xs" — *Searching knowledge base → Found N sources →
Generating response*. A recent observability commit (`5d96952`) added
structured trace events to the AgenticRetrieval Lambda's CloudWatch logs
(per-turn model responses, per-tool calls, per-tool results). None of
that detail reaches the browser.

The goal is to let users watch the agent's **chain of thought, the
queries it runs, and the documents it decides to traverse, with its
reasoning for each step**, in real time.

## Scope

**In scope:**

- New WebSocket message type `agent-event` that streams per-turn
  reasoning and per-tool-call/result events from the AgenticRetrieval
  Lambda to the frontend during the tool loop.
- Live UI updates in the existing collapsible "Thought for Xs" panel on
  `ChatMessage`, replacing the static 3-step list with a dynamic trace
  driven by received events.
- Dev-mode toggle (`?debug=1` URL param OR `localStorage['wisco:devTrace']
  = '1'`) that reveals the full raw tool inputs, raw result summaries,
  latencies, and token usage inline.

**Out of scope:**

- CloudWatch pull-based trace retrieval (rejected in brainstorming).
- Persisting trace history in DynamoDB or replaying traces on chat
  reload.
- Changes to the legacy OpenSearch RAG path — it continues to show the
  current 3-step placeholder.
- Backend-side filtering by dev mode. The Lambda always emits the full
  payload (Medium fields + `devPayload`); the UI filters.

## High-level flow

```
AgenticRetrieval Lambda loop:
  turn_start → bedrock.converse → [reasoning text] → [tool_use blocks]
                                       │                    │
                                       ▼                    ▼
                                  emit reasoning      emit tool_call
                                                             │
                                                        execute_tool
                                                             │
                                                             ▼
                                                       emit tool_result
  ... repeat ...
  answer tool called → emit loop_complete → return payload to Step Functions

Frontend:
  WebSocket → use-websocket-chat messageHandler → appendAgentTraceEvent
           → chat-store.queries[queryId].agentTrace[]
           → ChatMessage derives steps from agentTrace (replaces static list)
```

## Architectural decisions

| Decision | Chosen | Rationale |
|---|---|---|
| Trace delivery | Live WebSocket push from Lambda (Approach 1) | Matches user expectation of live "thinking" UI; WebSocket infra already in this Lambda for `report_error`. |
| Detail level | Medium by default, Full under dev mode | Medium gives satisfying "what and why" without overwhelming end users; Full is debug-only. |
| Dev mode filtering | UI-side only | Keeps Lambda output stable; enables forensics (dev mode for a shared session). WebSocket payload size is KB-range, so bandwidth is a non-issue. |
| Dev mode toggle | URL param (`?debug=1`) AND localStorage | Shareable debug URLs + sticky per-developer setting. |
| Layout | Compact list, continues existing timeline aesthetic | Scales to many steps; minimal visual change vs. the current steps panel. |
| Completion behavior | Auto-collapse on complete (current behavior) | Zero-lift; detail is one click away. |
| Emission code shape | Inline `_emit_trace` helper parallel to `_log_agent_event` | Smallest diff; emitter lives next to the source-of-truth log call. |

## Contracts

### WebSocket message schema

Discriminated union addition in
`packages/messages/types/message-types.ts`:

```ts
export const AgentEventKindSchema = z.enum([
  'loop_start',
  'reasoning',
  'tool_call',
  'tool_result',
  'loop_complete',
]);

export const AgentEventSchema = z.object({
  responseType: z.literal('agent-event'),
  queryId: z.string(),
  kind: AgentEventKindSchema,
  turn: z.number().int().optional(),
  seq: z.number().int(),
  timestamp: z.number(),
  payload: z.record(z.string(), z.unknown()),
  devPayload: z.record(z.string(), z.unknown()).optional(),
});

// Added to MessageUnionSchema discriminated union.
```

Mirrored in `packages/shared/lambda_layers/websocket_utils/models.py`
as `AgentEventMessage` with `kind`, `turn`, `seq`, `timestamp`,
`payload`, `dev_payload`, using the existing `CamelCaseModel` base.

### Payload shape per `kind`

All fields except those marked dev-only are displayed in Medium mode.

**`loop_start`**
- `payload.maxTurns: number`

**`reasoning`**
- `payload.text: string` — ≤500 chars (same truncation as logs).

**`tool_call`**
- `payload.toolName: string`
- `payload.summary: string` — pre-formatted, e.g. `'"use value
  assessment Wisconsin agricultural"'` for `vector_search`, or `'id =
  "stat-70-32"'` for `get_neighbors`. Lambda builds this from raw
  `tool_input`.
- `devPayload.toolInput: object` — verbatim input dict.
- `devPayload.toolUseId: string`

**`tool_result`**
- `payload.toolName: string`
- `payload.status: 'ok' | 'error' | 'miss' | 'terminal'`
- `payload.summary: string` — e.g. `'Found 6 chunks across 3 docs'`,
  `'Pulled 4 neighbors (1 rule, 2 cases)'`, `'FAQ top score 0.84 (3
  hits)'`. Lambda builds from existing `_summarize_tool_result`
  output.
- `payload.docIds: string[]` — up to 10 doc IDs mentioned in the
  result (medium mode uses for tooltips/counts, dev mode shows all).
- `payload.docTitles: string[]` — titles resolved from Neptune when
  available.
- `devPayload.raw: object` — full output of `_summarize_tool_result`.
- `devPayload.toolLatencyMs: number`

**`loop_complete`**
- `payload.terminalReason: 'answer_tool' | 'assistant_text_or_fallback'
  | 'faq_short_circuit'`
- `payload.turnsUsed: number`
- `payload.elapsedMs: number`
- `payload.citedDocCount: number`
- `devPayload.totalInputTokens?: number`
- `devPayload.totalOutputTokens?: number`

### Ordering

`seq` is a strictly increasing per-query counter (1, 2, 3, …) assigned
at emission time in the Lambda. The frontend dedupes by `seq` on
insert (in case of WebSocket retries from API Gateway) and renders in
arrival order, which equals `seq` order on a healthy connection.

## Backend implementation

### File: `packages/graphrag/lambdas/agentic_retrieval/main.py`

Additions, in order:

1. **Imports:** `itertools` (for the per-query seq counter); nothing
   else — `time`, `os`, `logging` already in scope.
2. **Env flag:** `EMIT_AGENT_TRACE = os.environ.get("EMIT_AGENT_TRACE",
   "true").lower() == "true"`. Default on; env-flippable kill switch.
3. **WebSocket connection lookup** in `handler()`, after
   `process_event()` succeeds and before `run_agentic_loop()`:

   ```python
   ws_server = None
   if EMIT_AGENT_TRACE and session_id:
       try:
           ws_server = get_ws_connection_from_session(session_id)
       except (SessionNotFoundError, SessionLookupError):
           ws_server = None
   ```
4. **Pass `ws_server` + a seq counter into `run_agentic_loop`** via two
   new kwargs: `ws_server: WebSocketServer | None = None`, `trace_seq:
   Callable[[], int] | None = None`. Default-None means callers
   (including tests) can keep the existing signature.
5. **Module-level `_emit_trace` helper** in `main.py`, signature:

   ```python
   def _emit_trace(
       ws_server: WebSocketServer | None,
       trace_seq: Callable[[], int],
       *,
       query_id: str,
       kind: str,
       turn: int | None = None,
       payload: dict | None = None,
       dev_payload: dict | None = None,
   ) -> None:
   ```

   Returns immediately when `ws_server is None` or `EMIT_AGENT_TRACE
   is False`. Builds an `AgentEventMessage`, calls `ws_server.send(...)`
   inside try/except that logs a warning on failure and never raises.
6. **Summary-builder helpers**, co-located with the existing
   `_summarize_tool_result`:

   ```python
   def _build_tool_call_summary(tool_name: str, tool_input: dict) -> str:
       """Short prose describing what a tool call is doing.
       e.g. vector_search({query: "X"}) -> '"X"'
            get_neighbors({doc_id: "stat-70-32"}) -> 'doc stat-70-32'
       """

   def _build_tool_result_summary(
       tool_name: str, result: dict
   ) -> dict:
       """Return:
         {
           "status": 'ok' | 'error' | 'miss' | 'terminal',
           "summary_text": str,         # e.g. "Found 6 chunks across 3 docs"
           "doc_ids": list[str],        # up to 10
           "doc_titles": list[str],     # up to 10, best-effort via neptune.get_document
           "raw": dict,                 # output of _summarize_tool_result
         }
       """
   ```
   `_build_tool_result_summary` calls `_summarize_tool_result`
   internally for the `raw` field so the two stay aligned. Title
   lookups are best-effort — on Neptune errors, `doc_titles` is an
   empty list.
7. **Emission points**, one per existing `_log_agent_event` call:

   | Existing log | New emit |
   |---|---|
   | `agent_loop_start` | `_emit_trace("loop_start", turn=None, payload={"maxTurns": MAX_TURNS})` |
   | `agent_turn_model_response` | `_emit_trace("reasoning", turn=turn_number, payload={"text": text_preview})` **only if** `text_preview` is non-empty |
   | `agent_tool_call` | `_emit_trace("tool_call", turn=turn_number, payload={"toolName": tool_name, "summary": _build_tool_call_summary(tool_name, tool_input)}, dev_payload={"toolInput": tool_input, "toolUseId": tool_use_id})` |
   | `agent_tool_result` (non-answer tools) | `s = _build_tool_result_summary(tool_name, result)` → `_emit_trace("tool_result", turn=turn_number, payload={"toolName": tool_name, "status": s["status"], "summary": s["summary_text"], "docIds": s["doc_ids"], "docTitles": s["doc_titles"]}, dev_payload={"raw": s["raw"], "toolLatencyMs": tool_latency_ms})` |
   | `agent_tool_result` for `answer` | **Skip `tool_result` emission.** The `answer` tool's meaning is captured by `loop_complete`; emitting a separate `tool_result` for it would double-render "Answering" in the UI. |
   | `agent_loop_complete` (answer_tool branch) | `_emit_trace("loop_complete", payload={"terminalReason": "answer_tool", "turnsUsed": turn_number, "elapsedMs": elapsed_ms, "citedDocCount": len(cited)})` |
   | `agent_loop_complete` (fallback branch) | `_emit_trace("loop_complete", payload={"terminalReason": "assistant_text_or_fallback", "turnsUsed": turn_number, "elapsedMs": elapsed_ms, "citedDocCount": len(all_doc_ids)})` |
   | *FAQ short-circuit path* (new) | Synthetic `loop_start` + `loop_complete` pair with `terminalReason: "faq_short_circuit"`, so the UI always gets a bracket-matched pair regardless of path. Emit before the early `return` in `run_agentic_loop`. |

### File: `packages/shared/lambda_layers/websocket_utils/models.py`

Add `AgentEventMessage` as a `WebSocketMessage` subclass with
`response_type: Literal["agent-event"] = "agent-event"`, `query_id`,
`kind`, `turn`, `seq`, `timestamp`, `payload`, `dev_payload` fields.
Alias generator converts to camelCase on the wire.

### File: `packages/graphrag/infra/graphrag-messages-stack.ts`

One new env var on the Lambda:

```ts
environment: {
  // ...existing
  EMIT_AGENT_TRACE: 'true',
}
```

No new IAM: `execute-api:ManageConnections` and sessions-table read
access were added on the in-progress merge (already resolved) and are
used by `report_error` today. No new constructs.

## Frontend implementation

### File: `packages/messages/types/message-types.ts`

Add the `AgentEventSchema` above; extend `MessageUnionSchema` with it.

### File: `packages/webapp/src/stores/types.ts`

```ts
export interface AgentTraceEvent {
  kind: 'loop_start' | 'reasoning' | 'tool_call' | 'tool_result' | 'loop_complete';
  turn?: number;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
  devPayload?: Record<string, unknown>;
}

// On Query:
agentTrace?: AgentTraceEvent[];
```

### File: `packages/webapp/src/stores/chat-store.ts`

New action:

```ts
appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) => void;
```

Implementation pushes to `queries[queryId].agentTrace`, creating the
array lazily. Dedupes by `seq` (skip if an event with the same `seq`
already exists — idempotent under WebSocket retries).

### File: `packages/webapp/src/hooks/use-websocket-chat.ts`

One new case in the discriminated switch:

```ts
case 'agent-event':
  appendAgentTraceEvent(message.queryId, {
    kind: message.kind,
    turn: message.turn,
    seq: message.seq,
    timestamp: message.timestamp,
    payload: message.payload,
    devPayload: message.devPayload,
  });
  break;
```

### File: `packages/webapp/src/hooks/use-dev-trace.ts` (new)

```ts
'use client';
import { useMemo } from 'react';

export function useDevTrace(): boolean {
  return useMemo(() => {
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    if (params.get('debug') === '1') return true;
    return localStorage.getItem('wisco:devTrace') === '1';
  }, []);
}
```

### File: `packages/webapp/src/components/messages/chat-message.tsx`

Replace the static `steps` `useMemo` (current lines 270–280) with a
derivation over `agentTrace`:

```ts
const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
const devTrace = useDevTrace();

const steps = useMemo<TraceStep[]>(() => {
  if (!agentTrace || agentTrace.length === 0) {
    // Fallback to the existing 3-step placeholder for legacy path /
    // FAQ short-circuit without trace emission.
    return buildLegacySteps({ hasResources, isStreaming, streamingComplete, items });
  }
  return agentTrace
    .filter(e => e.kind !== 'loop_start' && e.kind !== 'loop_complete')
    .map(e => renderTraceEvent(e, devTrace));
}, [agentTrace, devTrace, hasResources, items, isStreaming, streamingComplete]);
```

Where `renderTraceEvent(event, devTrace)` produces:

- **reasoning** → label is `payload.text`, italicized class.
- **tool_call** → label is `{verb(payload.toolName)} {payload.summary}`.
- **tool_result** → label is `payload.summary`. "done" dot if
  `payload.status in {'ok', 'terminal'}`; pending dot otherwise.
- Under `devTrace`, append a `<details>` disclosure containing the
  `devPayload` (if present) as prettified JSON.

`verb()` is a small local lookup table:

```ts
const VERBS: Record<string, string> = {
  vector_search: 'Searching for',
  get_neighbors: 'Expanding graph from',
  get_document: 'Fetching document',
  faq_search: 'Checking FAQs for',
  fetch_case_opinion: 'Fetching opinion for',
  get_authority_chain: 'Walking authority chain from',
  list_framework_docs: 'Listing framework docs for',
  refine_query: 'Refining query',
  answer: 'Answering',
};
```

### Fallback + legacy path behavior

When `agentTrace` is undefined or empty, the component renders the
existing 3-step static list exactly as today. This preserves:

- The legacy OpenSearch RAG path (no trace emitter there).
- GraphRAG queries that somehow had WebSocket emission disabled or
  failed silently (`_emit_trace` swallows errors).
- The FAQ short-circuit path if we ever flip `EMIT_AGENT_TRACE=false`
  for a hotfix.

## Testing

### Backend

`packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

- **Happy path (3-turn loop):** Mock `WebSocketServer.send`; assert
  the sequence `loop_start, reasoning?, tool_call, tool_result,
  reasoning?, tool_call, tool_result, loop_complete` with correct
  `seq` ordering and `turn` numbers.
- **FAQ short-circuit:** Mock a high-scoring FAQ hit; assert
  `loop_start` + `loop_complete` with `terminalReason:
  "faq_short_circuit"` and no intervening events.
- **WebSocket send raises:** Assert that `_emit_trace` catches the
  exception, logs a warning, and the loop completes normally with a
  correct final result.
- **EMIT_AGENT_TRACE=false:** Assert no calls to
  `WebSocketServer.send`; loop result unchanged.
- **Summary builder unit tests:** Feed `_build_tool_call_summary` and
  `_build_tool_result_summary` known tool inputs/outputs; assert the
  prose strings match.

### Frontend

`packages/webapp/src/hooks/test/use-websocket-chat.test.tsx`:

- **Agent-event routing:** Send an `agent-event` WebSocket message;
  assert `appendAgentTraceEvent` is called with the expected shape.
- **Seq dedupe:** Send two events with the same `seq`; assert only
  one appears in the store.

New component test (if the existing Jest config supports RTL — otherwise
a Storybook smoke story):

- **Renders trace labels:** Mount `ChatMessage` with a pre-populated
  `agentTrace`; assert the DOM contains the expected verbs and
  payload summaries.
- **Dev mode disclosure:** With `?debug=1` in URL, mount and assert
  `<details>` tags exist; without, assert they don't.
- **Legacy fallback:** Mount with `agentTrace: undefined`; assert
  existing 3-step labels render unchanged.

## Rollout / verification

- Deploy to the GraphRAG test stack in us-east-1
  (`WisconsinBotGraphRAG`). Never deploy from feature branches to
  us-west-2 prod.
- Before deploy: `cd packages/infra && cdk diff -c useGraphRAG=true -c
  stackName=WisconsinBotGraphRAG`. Expect additive changes: one new
  env var on `AgenticRetrievalFunction`. No new IAM statements, no new
  resources.
- Post-deploy manual smoke test: submit a query in the deployed UI,
  confirm (a) trace steps appear in real time during the ~5–30s
  thinking window, (b) auto-collapses on complete, (c) `?debug=1`
  reveals `<details>` disclosures, (d) legacy behavior still works if
  you flip `EMIT_AGENT_TRACE=false` via the Lambda console.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| WebSocket disconnects mid-loop (tab backgrounded, network blip) | `_emit_trace` swallows errors. Retrieval loop continues unaffected; the final `documents`/`answer`/`fragment` stream triggers the completion UI. Missing mid-loop events just mean fewer bullets in the list. |
| API Gateway stale connection after 10-min idle | Same mitigation — emission is best-effort. Timer tops at the Lambda's 120s timeout well before any idle-connection issues. |
| Event ordering out-of-order under WebSocket retries | `seq`-based dedupe on insert; UI renders in seq order. |
| Large `devPayload` blowing past WebSocket message size limits (128 KB hard cap in API Gateway) | `_emit_trace` uses the same `_compact_log_value`/truncation helpers as the log emitter. `tool_input` and `raw` summaries are already bounded to ~500 chars per string, 10 elements per list. |
| Frontend store growing unboundedly over a long session | `agentTrace` is scoped to each `Query`; `clearHistory()` drops it with the rest of the conversation state. Typical loop emits <30 events at <500 bytes each — negligible. |

## Followups (explicitly out of scope)

- Persist `agentTrace` in the chat-history DynamoDB table so reloading
  a past conversation can replay the trace. (Would need a schema
  migration; not needed for MVP since the goal is live visibility.)
- Expose an "export trace" action in `MessageOptionsBar` for debugging
  user reports.
- A richer per-step card view (the rejected Option B from
  brainstorming) if users ask for more structure.
