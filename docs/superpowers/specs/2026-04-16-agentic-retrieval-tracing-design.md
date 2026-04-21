# Agentic Retrieval Query Tracing

Structured JSON logging for the GraphRAG agentic retrieval Lambda that captures the full decision trail for every query: FAQ evaluation, Claude's reasoning, tool calls with results, graph traversal, sources returned, and final answer generation.

## Scope

- **In scope:** GraphRAG path only (`packages/graphrag/lambdas/agentic_retrieval/`)
- **Out of scope:** Legacy path (classifier/retrieval), frontend, sessions API
- **Always on:** No conditional verbosity — every query gets full tracing
- **No new dependencies:** Uses stdlib `dataclasses`, `json`, `logging`
- **No new files:** All changes in `main.py`

## Data Model

All trace types are plain `@dataclass` classes (not Pydantic) — they exist only for logging, never cross Lambda boundaries.

### TraceContext

Created at handler entry. Accumulates data through the agentic loop. Emits a single consolidated JSON log line at the end.

```python
@dataclass
class TraceContext:
    query_id: str
    session_id: str
    query: str
    started_at: float           # time.time()
    steps: list[TraceStep]      # appended during the loop
    faq_decision: FaqDecision | None = None
    answer_summary: AnswerSummary | None = None
    error: str | None = None
    max_turns_exhausted: bool = False
```

### TraceStep

One per tool call in the agentic loop.

```python
@dataclass
class TraceStep:
    turn: int
    tool_name: str
    tool_input: dict            # full input, not truncated
    tool_result: dict           # full result from execute_tool()
    reasoning_text: str | None  # Claude's text blocks BEFORE this tool call
    duration_ms: float          # wall-clock time for execute_tool()
```

### FaqDecision

Derived after the first `faq_search` call. Captures whether Claude accepted the FAQ results or continued to graph search.

```python
@dataclass
class FaqDecision:
    query_used: str
    num_results: int
    top_scores: list[float]
    accepted: bool              # True if answer() called in same Bedrock response (parallel tool use) or next turn
    reasoning: str              # Claude's text explaining its FAQ evaluation (from the turn after faq_search)
```

### AnswerSummary

Set when the `answer` tool is called or the loop terminates.

```python
@dataclass
class AnswerSummary:
    cited_doc_ids: list[str]
    num_sources: int
    answer_length: int
    tools_used: list[str]       # ordered list of all tools called
    total_turns: int
    total_duration_ms: float
```

## Integration Points

All changes are in `packages/graphrag/lambdas/agentic_retrieval/main.py`. Three touch points:

### 1. Handler — create and emit trace

```python
def handler(event, context):
    trace = TraceContext(...)      # created after process_event()
    try:
        answer, doc_ids, docs = run_agentic_loop(user_query.query, trace)
        trace.finalize(doc_ids, docs)
    except Exception as e:
        trace.error = str(e)
        raise
    finally:
        trace.emit()              # always fires, even on error
```

### 2. Agentic loop — capture reasoning and tool calls

Inside `run_agentic_loop()`, for each turn:

1. **Extract reasoning text:** Before processing `tool_uses`, collect any `text` blocks from Claude's response — these contain the agent's reasoning about what to do next.
2. **Time and capture tool calls:** Wrap `execute_tool()` with `time.time()` and capture the full result dict.
3. **Append TraceStep:** One per tool call, with reasoning text from the same turn.
4. **Derive FaqDecision:** After a `faq_search` call, check the result scores and set `faq_decision.accepted` based on whether `answer` is called in the same Bedrock response (Claude can return multiple tool_use blocks in one response — parallel tool use) or if the loop continues to additional turns. The `reasoning` field comes from Claude's text blocks in the turn following `faq_search` (or the same turn if text precedes the `answer` call).

### 3. TraceContext.emit() — structured JSON log

```python
def emit(self):
    log_entry = {
        "trace_type": "query_trace",
        "query_id": self.query_id,
        "session_id": self.session_id,
        "query": self.query,
        "started_at": datetime.fromtimestamp(self.started_at, UTC).isoformat(),
        "duration_ms": (time.time() - self.started_at) * 1000,
        "faq_decision": asdict(self.faq_decision) if self.faq_decision else None,
        "steps": [asdict(s) for s in self.steps],
        "answer_summary": asdict(self.answer_summary) if self.answer_summary else None,
        "error": self.error,
        "max_turns_exhausted": self.max_turns_exhausted,
    }
    logger.info(json.dumps(log_entry))
```

## Log Output

Two types of log lines:

### Per-step logs (enhanced existing logs)

Emitted during the loop for real-time tailing. Include `query_id` for correlation:

```json
{"query_id": "abc-123", "turn": 1, "tool": "faq_search", "duration_ms": 450, "result_summary": "3 FAQs, top score 0.82"}
```

### Consolidated trace (new)

One JSON log line per query, emitted at the end. Contains the full TraceContext. Example:

```json
{
  "trace_type": "query_trace",
  "query_id": "abc-123",
  "session_id": "sess-456",
  "query": "What is the equalized value for manufacturing property?",
  "started_at": "2026-04-16T14:30:00Z",
  "duration_ms": 3420,
  "faq_decision": {
    "query_used": "equalized value manufacturing property",
    "num_results": 3,
    "top_scores": [0.82, 0.65, 0.41],
    "accepted": false,
    "reasoning": "The FAQ results mention equalized values generally but don't address manufacturing property specifically."
  },
  "steps": [
    {
      "turn": 1,
      "tool_name": "faq_search",
      "tool_input": {"query": "equalized value manufacturing property", "top_k": 5},
      "tool_result": {"faqs": [{"text": "...", "score": 0.82}], "count": 3},
      "reasoning_text": null,
      "duration_ms": 450
    },
    {
      "turn": 2,
      "tool_name": "vector_search",
      "tool_input": {"query": "manufacturing property equalized value statute"},
      "tool_result": {"chunks": ["..."]},
      "reasoning_text": "The FAQ results are partially relevant but don't address manufacturing property specifically...",
      "duration_ms": 820
    }
  ],
  "answer_summary": {
    "cited_doc_ids": ["WIS-STAT-70.995", "WPAM-CH14"],
    "num_sources": 2,
    "answer_length": 1247,
    "tools_used": ["faq_search", "vector_search", "get_neighbors", "get_authority_chain", "answer"],
    "total_turns": 4,
    "total_duration_ms": 3420
  }
}
```

## CloudWatch Logs Insights Queries

```sql
-- All queries where FAQ was rejected
filter trace_type = "query_trace" and faq_decision.accepted = false
| display query_id, query, faq_decision.reasoning, faq_decision.top_scores

-- Slow queries (>5s)
filter trace_type = "query_trace" and duration_ms > 5000
| sort duration_ms desc

-- Tool usage frequency
filter trace_type = "query_trace"
| stats count(*) by answer_summary.tools_used

-- Full trace for a specific query
filter trace_type = "query_trace" and query_id = "abc-123"

-- Queries that exhausted max turns
filter trace_type = "query_trace" and max_turns_exhausted = true
| display query_id, query, answer_summary.total_turns, error
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Max turns exhausted | `max_turns_exhausted = true`, trace emits with all accumulated steps |
| Error mid-loop | `error` field populated, trace emits in `finally` block with partial steps |
| FAQ KB not configured | `faq_search` returns error, `faq_decision.num_results = 0`, loop continues |
| Large tool results | Logged in full; worst-case ~100KB, within CloudWatch 256KB limit |
| Claude returns no text blocks | `reasoning_text = None` for that step |

## Files Changed

| File | Change |
|------|--------|
| `packages/graphrag/lambdas/agentic_retrieval/main.py` | Add dataclasses (~60 lines), modify `handler()` and `run_agentic_loop()` to create/populate/emit TraceContext |

No new files. No new dependencies. No changes to `tools.py` or `neptune_client.py`.
