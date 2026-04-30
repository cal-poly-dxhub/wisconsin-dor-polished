# Agent Trace UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the AgenticRetrieval Lambda's per-turn reasoning and per-tool-call/result events over a new `agent-event` WebSocket message, and render them live in the chat UI's collapsible "Thought for Xs" panel.

**Architecture:** The agentic Lambda emits events mid-loop via a best-effort `_emit_trace` helper that wraps the existing `WebSocketServer` it already uses for `report_error`. The frontend receives them through the same validated WebSocket hook that handles streaming fragments, appends them to a new `agentTrace` array on each `Query` in the Zustand store, and renders them as bullets in place of the current static 3-step placeholder. A `useDevTrace` hook (URL `?debug=1` param OR `localStorage['wisco:devTrace']`) reveals a raw `devPayload` disclosure per step.

**Tech Stack:** Python 3.12 Lambda + boto3 + Pydantic v2 (backend); Next.js + Zustand + Zod + bun:test (frontend); AWS CDK for infra; pytest (backend tests); `@testing-library/react` + bun:test (frontend tests).

**Spec:** `docs/superpowers/specs/2026-04-30-agent-trace-ui-design.md`

---

## Pre-flight

- Verify the current branch has the resolved merge already committed/staged and the working tree builds:
  ```bash
  git status --short
  bun install
  cd packages/infra && cdk synth -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --quiet
  ```
- Run the existing Python tests to confirm a baseline:
  ```bash
  cd /Users/jonahchan/dev/dxhub/wisco && uv run pytest packages/graphrag/lambdas/test -v
  ```
  Expected: all pass.

---

## Task 1: `AgentEventMessage` Pydantic model in the shared websocket_utils layer

**Files:**
- Modify: `packages/shared/lambda_layers/websocket_utils/models.py`
- Modify: `packages/shared/lambda_layers/websocket_utils/__init__.py`
- Test: `packages/shared/lambda_layers/test/test_websocket_utils.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/shared/lambda_layers/test/test_websocket_utils.py`:

```python
# At top of file, ensure this import exists:
from websocket_utils.models import AgentEventMessage


def test_agent_event_message_camelcase_serialization():
    msg = AgentEventMessage(
        query_id="q-1",
        kind="tool_call",
        turn=2,
        seq=7,
        timestamp=1700000000000,
        payload={"toolName": "vector_search", "summary": '"ag use value"'},
        dev_payload={"toolInput": {"query": "ag use value"}, "toolUseId": "t-1"},
    )
    dumped = msg.model_dump(by_alias=True)
    assert dumped["responseType"] == "agent-event"
    assert dumped["queryId"] == "q-1"
    assert dumped["kind"] == "tool_call"
    assert dumped["turn"] == 2
    assert dumped["seq"] == 7
    assert dumped["timestamp"] == 1700000000000
    assert dumped["payload"] == {"toolName": "vector_search", "summary": '"ag use value"'}
    assert dumped["devPayload"] == {"toolInput": {"query": "ag use value"}, "toolUseId": "t-1"}


def test_agent_event_message_optional_dev_payload():
    msg = AgentEventMessage(
        query_id="q-1",
        kind="loop_start",
        seq=1,
        timestamp=1700000000000,
        payload={"maxTurns": 10},
    )
    dumped = msg.model_dump(by_alias=True)
    # dev_payload defaults to {} for consistent schema on the wire.
    assert dumped["devPayload"] == {}
    assert "turn" in dumped and dumped["turn"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/shared/lambda_layers/test/test_websocket_utils.py::test_agent_event_message_camelcase_serialization -v
```

Expected: FAIL with `ImportError: cannot import name 'AgentEventMessage'`.

- [ ] **Step 3: Add the model to `models.py`**

Append to `packages/shared/lambda_layers/websocket_utils/models.py`:

```python
class AgentEventMessage(WebSocketMessage):
    """Trace event emitted by the GraphRAG agent loop.

    Delivered to the frontend during the tool loop so the UI can render
    the agent's chain-of-thought live. Best-effort — the loop must not
    block on emission failures.
    """

    response_type: Literal["agent-event"] = "agent-event"
    query_id: str
    kind: Literal[
        "loop_start",
        "reasoning",
        "tool_call",
        "tool_result",
        "loop_complete",
    ]
    turn: int | None = None
    seq: int
    timestamp: int  # epoch ms at emission
    payload: dict = Field(default_factory=dict)
    dev_payload: dict = Field(default_factory=dict)
```

Ensure `Field` is already imported at the top — it is (`from pydantic import BaseModel, ConfigDict, Field`).

- [ ] **Step 4: Re-export the new model from `__init__.py`**

Edit `packages/shared/lambda_layers/websocket_utils/__init__.py`:

```python
# Add this to the existing explicit re-export of `.models`:
from .models import (
    AgentEventMessage,
    DocumentsContent,
    DocumentsMessage,
    FAQContent,
    FAQMessage,
    SourceDocument,
)

# Add "AgentEventMessage" to __all__:
__all__ = [
    "AgentEventMessage",
    "WebSocketMessage",
    "PlainWebSocketMessage",
    # ...rest unchanged
]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest packages/shared/lambda_layers/test/test_websocket_utils.py -v
```

Expected: all tests pass, including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add packages/shared/lambda_layers/websocket_utils/models.py \
        packages/shared/lambda_layers/websocket_utils/__init__.py \
        packages/shared/lambda_layers/test/test_websocket_utils.py
git commit -m "feat(shared): add AgentEventMessage pydantic model"
```

---

## Task 2: Handle `AgentEventMessage` in `WebSocketServer.send_json`

**Files:**
- Modify: `packages/shared/lambda_layers/websocket_utils/utils.py:49-84`
- Test: `packages/shared/lambda_layers/test/test_websocket_utils.py`

The `send_json` `match` statement must have a case for `AgentEventMessage`, otherwise it will raise `InvalidMessageError`.

- [ ] **Step 1: Write the failing test**

Append to `packages/shared/lambda_layers/test/test_websocket_utils.py`:

```python
import json
from unittest.mock import patch, MagicMock


@patch.dict(os.environ, {"WEBSOCKET_CALLBACK_URL": "wss://example/stage"})
def test_send_json_routes_agent_event_message():
    from websocket_utils.utils import WebSocketServer
    from websocket_utils.models import AgentEventMessage

    with patch("boto3.client") as boto_client:
        mock_client = MagicMock()
        mock_client.post_to_connection.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        boto_client.return_value = mock_client

        server = WebSocketServer(connection_id="conn-1")
        msg = AgentEventMessage(
            query_id="q-1",
            kind="reasoning",
            seq=3,
            timestamp=1700000000000,
            payload={"text": "I need to look this up"},
        )

        import asyncio
        asyncio.run(server.send_json(msg))

        mock_client.post_to_connection.assert_called_once()
        kwargs = mock_client.post_to_connection.call_args.kwargs
        assert kwargs["ConnectionId"] == "conn-1"
        sent = json.loads(kwargs["Data"])
        assert sent["streamId"] == "agent-trace"
        assert sent["body"]["responseType"] == "agent-event"
        assert sent["body"]["kind"] == "reasoning"
```

Also add `import os` at the top of the test file if it isn't there already.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/shared/lambda_layers/test/test_websocket_utils.py::test_send_json_routes_agent_event_message -v
```

Expected: FAIL with `InvalidMessageError` (no case matches `AgentEventMessage`).

- [ ] **Step 3: Add the match case to `send_json`**

Edit `packages/shared/lambda_layers/websocket_utils/utils.py`. In the `match body:` block inside `send_json`, add a new case **before** the `_` catch-all:

```python
            case AgentEventMessage():
                message = {
                    "streamId": "agent-trace",
                    "body": body.model_dump(by_alias=True),
                }
```

Also add `AgentEventMessage` to the import list at the top of the file (alongside `AnswerEventType`, `DocumentsMessage`, etc.):

```python
from websocket_utils.models import (
    AgentEventMessage,
    AnswerEventType,
    DocumentsMessage,
    ErrorMessage,
    FAQMessage,
    FragmentContent,
    FragmentMessage,
    PlainWebSocketMessage,
    WebSocketMessage,
)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest packages/shared/lambda_layers/test/test_websocket_utils.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/shared/lambda_layers/websocket_utils/utils.py \
        packages/shared/lambda_layers/test/test_websocket_utils.py
git commit -m "feat(shared): route AgentEventMessage through WebSocketServer"
```

---

## Task 3: `AgentEvent` Zod schema in `@messages` types

**Files:**
- Modify: `packages/messages/types/message-types.ts`
- Test: *none — this schema is consumed by Task 10–12 tests*

- [ ] **Step 1: Add the schema**

Edit `packages/messages/types/message-types.ts`. Add **above** `MessageUnionSchema`:

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
  turn: z.number().int().nullable().optional(),
  seq: z.number().int(),
  timestamp: z.number(),
  payload: z.record(z.string(), z.unknown()).default({}),
  devPayload: z.record(z.string(), z.unknown()).default({}),
});
```

Extend `MessageUnionSchema` to include the new schema:

```ts
export const MessageUnionSchema = z.discriminatedUnion('responseType', [
  DocumentsMessageSchema,
  FAQMessageSchema,
  ErrorMessageSchema,
  FragmentMessageSchema,
  AnswerEventTypeSchema,
  AgentEventSchema,
]);
```

Add the `streamId` literal `'agent-trace'` to `WebSocketMessageSchema`:

```ts
export const WebSocketMessageSchema = z.object({
  streamId: z.enum(['answer-event', 'answer', 'resources', 'error', 'agent-trace']),
  body: MessageUnionSchema,
});
```

Add type exports at the bottom, grouped with the other `z.infer` statements:

```ts
export type AgentEventKind = z.infer<typeof AgentEventKindSchema>;
export type AgentEvent = z.infer<typeof AgentEventSchema>;
```

- [ ] **Step 2: Typecheck**

```bash
cd packages/messages && bunx tsc --noEmit
cd ../.. && bunx tsc -p packages/webapp/tsconfig.json --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add packages/messages/types/message-types.ts
git commit -m "feat(messages): add agent-event schema to websocket union"
```

---

## Task 4: `_build_tool_call_summary` helper

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py` (add helper near other `_summarize_*` helpers, around line ~168)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

This helper turns raw tool input into a short human-friendly string. Pure function, easy to test in isolation.

- [ ] **Step 1: Write the failing tests**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_build_tool_call_summary_vector_search():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "vector_search", {"query": "ag use value"}
    ) == '"ag use value"'


def test_build_tool_call_summary_get_neighbors():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "get_neighbors", {"doc_id": "stat-70-32"}
    ) == "doc stat-70-32"


def test_build_tool_call_summary_faq_search():
    from main import _build_tool_call_summary
    assert _build_tool_call_summary(
        "faq_search", {"query": "what is TID"}
    ) == '"what is TID"'


def test_build_tool_call_summary_answer():
    from main import _build_tool_call_summary
    assert _build_tool_call_summary(
        "answer",
        {"response": "Use value...", "cited_doc_ids": ["a", "b", "c"]},
    ) == "with 3 cited doc(s)"


def test_build_tool_call_summary_unknown_tool_returns_empty():
    from main import _build_tool_call_summary
    assert _build_tool_call_summary("mystery_tool", {"foo": "bar"}) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "build_tool_call_summary"
```

Expected: FAIL with `ImportError: cannot import name '_build_tool_call_summary'`.

- [ ] **Step 3: Add the helper**

Insert into `packages/graphrag/lambdas/agentic_retrieval/main.py`, immediately after `_summarize_tool_result` (~line 257):

```python
def _build_tool_call_summary(tool_name: str, tool_input: dict) -> str:
    """Short prose describing a tool call for the UI trace.

    Returns an empty string for unknown tools — the UI then shows just
    the verb, which is still informative.
    """
    if tool_name in ("vector_search", "faq_search", "refine_query"):
        query = tool_input.get("query", "")
        return f'"{query}"' if query else ""
    if tool_name == "get_neighbors":
        doc_id = tool_input.get("doc_id", "")
        return f"doc {doc_id}" if doc_id else ""
    if tool_name == "get_document":
        doc_id = tool_input.get("doc_id", "")
        return doc_id
    if tool_name == "get_authority_chain":
        doc_id = tool_input.get("doc_id", "")
        return f"doc {doc_id}" if doc_id else ""
    if tool_name == "list_framework_docs":
        framework = tool_input.get("framework_name", "")
        return framework
    if tool_name == "fetch_case_opinion":
        citation = tool_input.get("citation", "")
        return citation
    if tool_name == "answer":
        cited = tool_input.get("cited_doc_ids", []) or []
        return f"with {len(cited)} cited doc(s)"
    return ""
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "build_tool_call_summary"
```

Expected: all 5 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): add _build_tool_call_summary helper"
```

---

## Task 5: `_build_tool_result_summary` helper

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py`
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_build_tool_result_summary_vector_search_ok():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary

    result = {
        "chunks": [
            {"doc_id": "doc-a", "text": "x"},
            {"doc_id": "doc-a", "text": "y"},
            {"doc_id": "doc-b", "text": "z"},
        ],
        "graph_context": {"doc-a": [{"id": "doc-c"}]},
    }
    s = _build_tool_result_summary("vector_search", result)
    assert s["status"] == "ok"
    assert "3 chunks" in s["summary_text"]
    assert set(s["doc_ids"]) == {"doc-a", "doc-b"}
    assert isinstance(s["doc_titles"], list)
    # `raw` is the same dict produced by _summarize_tool_result.
    assert s["raw"]["tool_name"] == "vector_search"
    assert s["raw"]["chunk_count"] == 3


def test_build_tool_result_summary_get_neighbors():
    from main import _build_tool_result_summary
    result = {
        "neighbors": [
            {"id": "d1", "relationship": "CITES"},
            {"id": "d2", "relationship": "IMPLEMENTS"},
        ]
    }
    s = _build_tool_result_summary("get_neighbors", result)
    assert s["status"] == "ok"
    assert "2 neighbor" in s["summary_text"]
    assert set(s["doc_ids"]) == {"d1", "d2"}


def test_build_tool_result_summary_faq_search_with_scores():
    from main import _build_tool_result_summary
    result = {
        "faqs": [
            {"text": "Q: x\nA: y", "score": 0.84},
            {"text": "Q: p\nA: q", "score": 0.71},
        ],
        "count": 2,
    }
    s = _build_tool_result_summary("faq_search", result)
    assert s["status"] == "ok"
    assert "top score 0.84" in s["summary_text"]
    assert s["doc_ids"] == []


def test_build_tool_result_summary_error_tool_result():
    from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "get_document", {"error": "not found", "fallback_matches": []}
    )
    assert s["status"] == "error"
    assert "not found" in s["summary_text"]


def test_build_tool_result_summary_answer_terminal():
    from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "answer", {"response": "Use value...", "cited_doc_ids": ["a", "b"]}
    )
    assert s["status"] == "terminal"
    assert "2 cited" in s["summary_text"]
    assert s["doc_ids"] == ["a", "b"]


def test_build_tool_result_summary_fetch_opinion_miss():
    from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "fetch_case_opinion", {"found": False, "citation": "123 Wis. 2d 45"}
    )
    assert s["status"] == "miss"
    assert "123 Wis. 2d 45" in s["summary_text"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "build_tool_result_summary"
```

Expected: FAIL with `ImportError: cannot import name '_build_tool_result_summary'`.

- [ ] **Step 3: Add the helper**

Insert into `packages/graphrag/lambdas/agentic_retrieval/main.py`, immediately after `_build_tool_call_summary`:

```python
def _build_tool_result_summary(tool_name: str, result: dict) -> dict:
    """Build a UI-friendly summary of a tool result.

    Returns a dict with:
      - status: 'ok' | 'error' | 'miss' | 'terminal'
      - summary_text: one-line human-readable string
      - doc_ids: list of up to 10 document IDs referenced in the result
      - doc_titles: list aligned with doc_ids (best-effort; empty on failure)
      - raw: output of _summarize_tool_result (dev-mode payload)
    """
    raw = _summarize_tool_result(tool_name, result)
    status = raw.get("status", "ok")
    doc_ids: list[str] = []
    summary_text = ""

    if "error" in result:
        return {
            "status": "error",
            "summary_text": str(result.get("error") or "tool error"),
            "doc_ids": [],
            "doc_titles": [],
            "raw": raw,
        }

    if tool_name == "vector_search":
        chunks = result.get("chunks", [])
        unique_docs = {c.get("doc_id") for c in chunks if c.get("doc_id")}
        doc_ids = list(unique_docs)[:10]
        summary_text = (
            f"Found {len(chunks)} chunks across {len(unique_docs)} doc(s)"
        )

    elif tool_name == "faq_search":
        faqs = result.get("faqs", [])
        top = faqs[0].get("score", 0.0) if faqs else 0.0
        summary_text = (
            f"FAQ top score {top:.2f} ({len(faqs)} hit(s))"
            if faqs
            else "No FAQ matches"
        )

    elif tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        doc_ids = [n["id"] for n in neighbors if n.get("id")][:10]
        summary_text = f"Pulled {len(neighbors)} neighbor(s)"

    elif tool_name == "get_document":
        doc = result.get("document")
        if doc:
            doc_ids = [doc.get("id")] if doc.get("id") else []
            summary_text = f"Fetched {doc.get('doc_type', 'document')} {doc.get('id', '')}"
        else:
            summary_text = "Document not found"
            status = "miss"

    elif tool_name == "get_authority_chain":
        chain = result.get("authority_chain", [])
        doc_ids = [n["id"] for n in chain if n.get("id")][:10]
        summary_text = f"Walked authority chain ({len(chain)} node(s))"

    elif tool_name == "list_framework_docs":
        docs = result.get("documents", [])
        doc_ids = [d["id"] for d in docs if d.get("id")][:10]
        summary_text = f"Listed {len(docs)} framework doc(s)"

    elif tool_name == "fetch_case_opinion":
        citation = result.get("citation", "")
        if result.get("found"):
            summary_text = f"Fetched opinion for {citation}"
        else:
            summary_text = f"No opinion found for {citation}"
            status = "miss"

    elif tool_name == "refine_query":
        refined = result.get("refined_query", "")
        summary_text = f'Refined to "{refined}"' if refined else "No refinement"

    elif tool_name == "answer":
        cited = result.get("cited_doc_ids", []) or []
        doc_ids = list(cited)[:10]
        summary_text = f"Answer with {len(cited)} cited doc(s)"
        status = "terminal"

    else:
        summary_text = f"{tool_name} complete"

    # Best-effort title resolution. Neptune failures must not break the loop.
    doc_titles: list[str] = []
    for doc_id in doc_ids:
        try:
            info = neptune.get_document(doc_id)
            doc_titles.append((info or {}).get("title") or doc_id)
        except Exception:  # noqa: BLE001
            doc_titles.append(doc_id)

    return {
        "status": status,
        "summary_text": summary_text,
        "doc_ids": doc_ids,
        "doc_titles": doc_titles,
        "raw": raw,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "build_tool_result_summary"
```

Expected: all 6 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): add _build_tool_result_summary helper"
```

---

## Task 6: `_emit_trace` helper

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py` (imports + helper, near top)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_emit_trace_sends_agent_event_message():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main
            import itertools

    mock_ws = MagicMock()
    mock_ws.send_json = MagicMock(return_value=None)
    # Work around the async call: patch asyncio.run to invoke synchronously.
    with patch("main.asyncio.run", side_effect=lambda coro: coro.close() or None) as run_mock:
        main._emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="reasoning",
            turn=2,
            payload={"text": "thinking"},
            dev_payload={"foo": "bar"},
        )
        assert run_mock.called


def test_emit_trace_noop_when_ws_is_none():
    from main import _emit_trace
    import itertools
    # Must not raise, must not attempt send.
    _emit_trace(
        None,
        itertools.count(1).__next__,
        query_id="q-1",
        kind="loop_start",
        payload={"maxTurns": 10},
    )


def test_emit_trace_swallows_ws_exceptions():
    from main import _emit_trace
    import itertools
    mock_ws = MagicMock()
    # asyncio.run raises; _emit_trace must catch.
    with patch("main.asyncio.run", side_effect=RuntimeError("boom")):
        _emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="reasoning",
            payload={"text": "x"},
        )
    # No exception propagated — pytest would fail otherwise.


def test_emit_trace_respects_emit_agent_trace_false():
    import itertools
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "false",
    }, clear=False):
        # Force re-import so the module-level constant picks up the new env value.
        import importlib, sys
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    mock_ws = MagicMock()
    with patch("main.asyncio.run") as run_mock:
        main._emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="loop_start",
            payload={"maxTurns": 10},
        )
    run_mock.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "emit_trace"
```

Expected: FAIL with `ImportError: cannot import name '_emit_trace'`.

- [ ] **Step 3: Add `itertools` import and the `EMIT_AGENT_TRACE` flag**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`, near the top with the other imports:

```python
import itertools
import time  # already present, keep as-is
```

Add near the other `LOG_*` env reads (~line 72):

```python
EMIT_AGENT_TRACE = os.environ.get("EMIT_AGENT_TRACE", "true").lower() == "true"
```

- [ ] **Step 4: Add the `_emit_trace` helper**

Insert into `main.py` after `_discovery_summary` (~line 263):

```python
def _emit_trace(
    ws_server,
    trace_seq,
    *,
    query_id: str,
    kind: str,
    turn: int | None = None,
    payload: dict | None = None,
    dev_payload: dict | None = None,
) -> None:
    """Push an AgentEventMessage to the client. Best-effort — never raises.

    ws_server may be None (trace disabled or session lookup failed); in that
    case this is a no-op. Any WebSocket exception is logged and swallowed so
    the agentic loop is never blocked by client-side issues.
    """
    if not EMIT_AGENT_TRACE or ws_server is None:
        return
    try:
        from websocket_utils.models import AgentEventMessage

        message = AgentEventMessage(
            query_id=query_id,
            kind=kind,
            turn=turn,
            seq=trace_seq(),
            timestamp=int(time.time() * 1000),
            payload=payload or {},
            dev_payload=_compact_log_value(dev_payload or {}),
        )
        asyncio.run(ws_server.send_json(message))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit agent-trace event", exc_info=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "emit_trace"
```

Expected: all 4 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): add _emit_trace helper for WebSocket trace events"
```

---

## Task 7: Wire emissions into `run_agentic_loop` (tool-loop path)

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:380-778`
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Extend `run_agentic_loop` signature**

Change the signature of `run_agentic_loop` (at ~line 380) to accept the WebSocket server and a seq counter:

```python
def run_agentic_loop(
    query: str,
    chat_history: list[dict] | None = None,
    *,
    query_id: str = "",
    session_id: str = "",
    request_id: str = "",
    ws_server=None,
    trace_seq=None,
) -> tuple[str, list[str], list[RAGDocument], FAQResource | None]:
```

Inside the function, right after `chat_history = chat_history or []`, add:

```python
    if trace_seq is None:
        trace_seq = itertools.count(1).__next__
```

- [ ] **Step 2: (Intentionally skipped — `loop_start` emission is added in Task 8)**

`loop_start` has to land *before* the FAQ short-circuit so the UI sees a consistent open-event regardless of path. Task 8 restructures that region; adding `loop_start` now would just get moved. Proceed directly to Step 3.

- [ ] **Step 3: Emit `reasoning` when the model writes text**

After the `_log_agent_event("agent_turn_model_response", ...)` block (~line 566-573), add:

```python
    summary = _summarize_assistant_message(assistant_message)
    if summary["text_preview"]:
        _emit_trace(
            ws_server,
            trace_seq,
            query_id=query_id,
            kind="reasoning",
            turn=turn_number,
            payload={"text": summary["text_preview"]},
        )
```

Note: `_summarize_assistant_message` is already being called inside the log helper via unpacking. Move its call out into a local variable so we can reuse it:

Replace:
```python
        _log_agent_event(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            **_summarize_bedrock_response(response),
            assistant=_summarize_assistant_message(assistant_message),
        )
```
with:
```python
        assistant_summary = _summarize_assistant_message(assistant_message)
        _log_agent_event(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            **_summarize_bedrock_response(response),
            assistant=assistant_summary,
        )
        if assistant_summary["text_preview"]:
            _emit_trace(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="reasoning",
                turn=turn_number,
                payload={"text": assistant_summary["text_preview"]},
            )
```

- [ ] **Step 4: Emit `tool_call` per tool use**

After `_log_agent_event("agent_tool_call", ...)` (~line 604-611), add:

```python
            _emit_trace(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="tool_call",
                turn=turn_number,
                payload={
                    "toolName": tool_name,
                    "summary": _build_tool_call_summary(tool_name, tool_input),
                },
                dev_payload={
                    "toolInput": tool_input,
                    "toolUseId": tool_use_id,
                },
            )
```

- [ ] **Step 5: Emit `tool_result` (skipping the `answer` tool)**

Replace the existing `_log_agent_event("agent_tool_result", ...)` block (~line 678-688) with:

```python
            tool_result_summary = _build_tool_result_summary(tool_name, result)
            _log_agent_event(
                "agent_tool_result",
                **trace_context,
                turn=turn_number,
                tool_use_id=tool_use_id,
                tool_latency_ms=tool_latency_ms,
                discovered_doc_count=len(all_doc_ids),
                chunk_count=len(all_chunks),
                discovery=_discovery_summary(discovery),
                tool_result_summary=tool_result_summary["raw"],
            )
            if tool_name != "answer":
                _emit_trace(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
                    kind="tool_result",
                    turn=turn_number,
                    payload={
                        "toolName": tool_name,
                        "status": tool_result_summary["status"],
                        "summary": tool_result_summary["summary_text"],
                        "docIds": tool_result_summary["doc_ids"],
                        "docTitles": tool_result_summary["doc_titles"],
                    },
                    dev_payload={
                        "raw": tool_result_summary["raw"],
                        "toolLatencyMs": tool_latency_ms,
                    },
                )
```

- [ ] **Step 6: Emit `loop_complete` on both exit paths**

After the `_log_agent_event("agent_loop_complete", terminal_reason="answer_tool", ...)` block (~line 714-725), and *before* `return answer, list(cited), rag_docs, None`:

```python
                _emit_trace(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
                    kind="loop_complete",
                    payload={
                        "terminalReason": "answer_tool",
                        "turnsUsed": turn_number,
                        "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                        "citedDocCount": len(cited),
                    },
                )
```

And after the fallback-branch `_log_agent_event("agent_loop_complete", terminal_reason="assistant_text_or_fallback", ...)` (~line 768-777), and *before* the final `return answer, list(all_doc_ids), rag_docs, None`:

```python
    _emit_trace(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_complete",
        payload={
            "terminalReason": "assistant_text_or_fallback",
            "turnsUsed": MAX_TURNS,
            "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
            "citedDocCount": len(all_doc_ids),
        },
    )
```

- [ ] **Step 7: Write an integration-style test for the emission sequence**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_run_agentic_loop_emits_trace_sequence(monkeypatch):
    """Drives run_agentic_loop through one vector_search + answer sequence
    and asserts the WebSocket received loop_start, tool_call, tool_result,
    loop_complete in that order.
    """
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    # Mock turn 0: FAQ returns a low-scoring hit so we fall through to the loop.
    def fake_faq_search(query):
        return {
            "faqs": [{"text": "Q: unrelated\nA: nope", "score": 0.2, "source_uri": "s3://f/faq_1.txt"}],
            "count": 1,
        }
    monkeypatch.setattr(main, "_faq_search_direct", fake_faq_search)

    # Drive Bedrock converse: turn 1 calls vector_search, turn 2 calls answer.
    responses = [
        {
            "output": {"message": {"content": [
                {"text": "I'll search the graph."},
                {"toolUse": {"toolUseId": "t1", "name": "vector_search", "input": {"query": "use value"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 100},
        },
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t2", "name": "answer", "input": {"response": "Use value is...", "cited_doc_ids": ["doc-a"]}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
            "metrics": {"latencyMs": 120},
        },
    ]
    main.bedrock.converse = MagicMock(side_effect=responses)

    def fake_execute(name, input_, neptune_client, chat_history=None):
        if name == "vector_search":
            return {
                "chunks": [{"doc_id": "doc-a", "text": "..."}],
                "graph_context": {},
            }
        if name == "answer":
            return {
                "response": input_.get("response", ""),
                "cited_doc_ids": input_.get("cited_doc_ids", []),
            }
        return {}
    monkeypatch.setattr(main, "execute_tool", fake_execute)

    # Return None from neptune.get_document so _build_rag_documents skips real lookups.
    main.neptune.get_document = MagicMock(return_value=None)

    mock_ws = MagicMock()
    # Make asyncio.run a no-op that records coroutines sent.
    sent_messages = []
    def fake_run(coro):
        # The coroutine is ws_server.send_json(msg); harvest the msg via send_json's
        # mock call args after we manually close the coro.
        coro.close()
    monkeypatch.setattr(main.asyncio, "run", fake_run)
    def capture_send(msg):
        sent_messages.append(msg)
        async def _noop(): return None
        return _noop()
    mock_ws.send_json = capture_send

    import itertools
    main.run_agentic_loop(
        "what is use value?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    kinds = [m.kind for m in sent_messages]
    assert kinds[0] == "loop_start"
    assert "reasoning" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "loop_complete"
    # seq is monotonically increasing and starts at 1.
    seqs = [m.seq for m in sent_messages]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1
    # The `answer` tool must NOT produce a tool_result event.
    answer_tool_results = [
        m for m in sent_messages
        if m.kind == "tool_result" and m.payload.get("toolName") == "answer"
    ]
    assert answer_tool_results == []
    # loop_complete carries terminalReason=answer_tool.
    complete = [m for m in sent_messages if m.kind == "loop_complete"][-1]
    assert complete.payload["terminalReason"] == "answer_tool"
    assert complete.payload["citedDocCount"] == 1
```

- [ ] **Step 8: Run the test to verify it passes**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_run_agentic_loop_emits_trace_sequence -v
```

Expected: PASS.

- [ ] **Step 9: Run all agentic tests to check nothing regressed**

```bash
uv run pytest packages/graphrag/lambdas/test -v
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): emit agent trace events during tool loop"
```

---

## Task 8: FAQ short-circuit emission

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:442-454`
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

The FAQ short-circuit path returns early without entering the tool loop. The UI still needs a matching `loop_start` / `loop_complete` pair so its fallback logic is predictable.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_run_agentic_loop_faq_short_circuit_emits_bracketed_pair(monkeypatch):
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    # High-scoring FAQ triggers short-circuit.
    monkeypatch.setattr(main, "_faq_search_direct", lambda q: {
        "faqs": [{"text": "Q: what is TID\nA: answer", "score": 0.90,
                  "source_uri": "s3://f/faq_1.txt"}],
        "count": 1,
    })

    sent = []
    def fake_run(coro):
        coro.close()
    monkeypatch.setattr(main.asyncio, "run", fake_run)
    mock_ws = MagicMock()
    def capture(msg):
        sent.append(msg)
        async def _noop(): return None
        return _noop()
    mock_ws.send_json = capture

    import itertools
    main.run_agentic_loop(
        "what is TID?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    kinds = [m.kind for m in sent]
    assert kinds == ["loop_start", "loop_complete"]
    assert sent[-1].payload["terminalReason"] == "faq_short_circuit"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_run_agentic_loop_faq_short_circuit_emits_bracketed_pair -v
```

Expected: FAIL (only `loop_start` is emitted; no `loop_complete` on the early return).

- [ ] **Step 3: Emit the short-circuit pair**

The `_log_agent_event("agent_loop_start", ...)` call is currently at line ~505 (**after** the FAQ turn-0 short-circuit block). Move it **before** the FAQ short-circuit, and add the matching `loop_complete` emission inside the short-circuit block.

Specifically, in `run_agentic_loop`, restructure as follows:

Find this block (~lines 433-454):

```python
    # Turn 0: deterministic FAQ search (using the refined query when we have one).
    faq_result = _faq_search_direct(search_query)
    faq_entries = faq_result.get("faqs", [])
    top_score = faq_entries[0].get("score", 0.0) if faq_entries else 0.0
    logger.info(...)

    if top_score >= FAQ_SCORE_THRESHOLD:
        faq_resource = _build_faq_resource(faq_entries)
        if faq_resource:
            logger.info(...)
            # Empty document list — answer is fully grounded in FAQs.
            return "", [], [], faq_resource
        logger.warning(...)
```

Replace with:

```python
    # Emit loop_start before the FAQ turn 0 so the UI sees a consistent
    # open-event regardless of whether we short-circuit or enter the loop.
    loop_started = time.perf_counter()
    _log_agent_event(
        "agent_loop_start",
        query_id=query_id,
        session_id=session_id,
        request_id=request_id,
        model_id=AGENTIC_MODEL_ID,
        max_turns=MAX_TURNS,
        **_query_log_fields(query),
    )
    _emit_trace(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_start",
        payload={"maxTurns": MAX_TURNS},
    )

    # Turn 0: deterministic FAQ search (using the refined query when we have one).
    faq_result = _faq_search_direct(search_query)
    faq_entries = faq_result.get("faqs", [])
    top_score = faq_entries[0].get("score", 0.0) if faq_entries else 0.0
    logger.info(
        f"FAQ turn-0: {len(faq_entries)} hits, top_score={top_score:.3f}, "
        f"threshold={FAQ_SCORE_THRESHOLD}"
    )

    if top_score >= FAQ_SCORE_THRESHOLD:
        faq_resource = _build_faq_resource(faq_entries)
        if faq_resource:
            logger.info(
                f"FAQ short-circuit: returning {len(faq_resource.faqs)} FAQ(s) "
                "without entering agentic loop"
            )
            _emit_trace(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="loop_complete",
                payload={
                    "terminalReason": "faq_short_circuit",
                    "turnsUsed": 0,
                    "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                    "citedDocCount": 0,
                },
            )
            # Empty document list — answer is fully grounded in FAQs.
            return "", [], [], faq_resource
        logger.warning(
            "FAQ score cleared threshold but no entries parsed; falling through to graph"
        )
```

Now **delete** the original `_log_agent_event("agent_loop_start", ...)` call that was at line ~505 (below the FAQ short-circuit in the pre-Task-7 file) and the now-duplicate `loop_started = time.perf_counter()` on the same line — both have been moved above the FAQ check. Search for the **second** occurrence of `agent_loop_start` and remove the whole `_log_agent_event("agent_loop_start", ...)` block plus the preceding `loop_started = ...` assignment.

- [ ] **Step 4: Run all agentic tests**

```bash
uv run pytest packages/graphrag/lambdas/test -v
```

Expected: all pass, including both `test_run_agentic_loop_emits_trace_sequence` and `test_run_agentic_loop_faq_short_circuit_emits_bracketed_pair`.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): emit synthetic loop pair for FAQ short-circuit"
```

---

## Task 9: Handler wiring — look up WebSocket server and pass into loop

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:1117-1183`
- Test: *covered by Task 7/8 (loop-level tests)*; add one handler-level smoke.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_handler_attaches_ws_server_when_session_lookup_succeeds(monkeypatch):
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
        "SESSIONS_TABLE_NAME": "t-sessions",
        "WEBSOCKET_CALLBACK_URL": "wss://example/stage",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import importlib, sys
            if "main" in sys.modules: del sys.modules["main"]
            import main

    mock_ws = MagicMock()
    monkeypatch.setattr(
        main, "get_ws_connection_from_session", MagicMock(return_value=mock_ws)
    )
    monkeypatch.setattr(
        main,
        "run_agentic_loop",
        MagicMock(return_value=("ans", [], [], None)),
    )
    monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
    # Avoid real process_event validation on MagicMock models; bypass.
    monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
        query="q", query_id="q-1", session_id="s-1"
    ))
    monkeypatch.setattr(main, "DocumentResource", MagicMock())

    ctx = SimpleNamespace(aws_request_id="r-1")
    main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

    kwargs = main.run_agentic_loop.call_args.kwargs
    assert kwargs["ws_server"] is mock_ws
    assert callable(kwargs["trace_seq"])


def test_handler_runs_with_ws_none_when_session_lookup_fails(monkeypatch):
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
        "SESSIONS_TABLE_NAME": "t-sessions",
        "WEBSOCKET_CALLBACK_URL": "wss://example/stage",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import importlib, sys
            if "main" in sys.modules: del sys.modules["main"]
            import main

    def raise_lookup(sid):
        # Use the real exception type from the mocked websocket_utils package.
        class _Err(Exception): pass
        raise _Err("no session")
    monkeypatch.setattr(main, "get_ws_connection_from_session", raise_lookup)
    monkeypatch.setattr(main, "run_agentic_loop",
                        MagicMock(return_value=("ans", [], [], None)))
    monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
    monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
        query="q", query_id="q-1", session_id="s-1"
    ))
    monkeypatch.setattr(main, "DocumentResource", MagicMock())

    ctx = SimpleNamespace(aws_request_id="r-1")
    main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

    kwargs = main.run_agentic_loop.call_args.kwargs
    assert kwargs["ws_server"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v -k "handler_attaches_ws"
```

Expected: FAIL — the handler doesn't pass `ws_server` into `run_agentic_loop` yet.

- [ ] **Step 3: Wire the handler**

At the top of `main.py`, add the import (alongside the existing `from step_function_types.errors import ...`):

```python
from websocket_utils.utils import get_ws_connection_from_session
```

Then edit the `handler` function (~line 1117). After `session_id = user_query.session_id` and before calling `run_agentic_loop`, insert:

```python
        ws_server = None
        if session_id:
            try:
                ws_server = get_ws_connection_from_session(session_id)
            except Exception:  # noqa: BLE001
                # Trace emission is best-effort; the loop must still run.
                logger.warning(
                    "Could not look up WebSocket connection; trace events will be skipped",
                    exc_info=True,
                )
                ws_server = None
        trace_seq = itertools.count(1).__next__
```

Change the existing call from:

```python
        answer, cited_doc_ids, rag_documents, faq_resource = run_agentic_loop(
            user_query.query,
            chat_history=chat_history,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            request_id=request_id,
        )
```

to:

```python
        answer, cited_doc_ids, rag_documents, faq_resource = run_agentic_loop(
            user_query.query,
            chat_history=chat_history,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            request_id=request_id,
            ws_server=ws_server,
            trace_seq=trace_seq,
        )
```

- [ ] **Step 4: Run all agentic tests**

```bash
uv run pytest packages/graphrag/lambdas/test -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): wire ws_server lookup into agentic handler"
```

---

## Task 10: Add `EMIT_AGENT_TRACE` env var to the CDK stack

**Files:**
- Modify: `packages/graphrag/infra/graphrag-messages-stack.ts:57-74`

- [ ] **Step 1: Edit the stack**

In `packages/graphrag/infra/graphrag-messages-stack.ts`, add `EMIT_AGENT_TRACE: 'true'` to the `environment` dict of `AgenticRetrievalFunction`:

```ts
environment: {
  WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
  NEPTUNE_GRAPH_ID: props.neptuneGraphId,
  AGENTIC_MODEL_ID: 'us.anthropic.claude-sonnet-4-6',
  RAW_BUCKET: props.rawBucketName,
  FAQ_KNOWLEDGE_BASE_ID: props.faqKnowledgeBaseId,
  CHAT_HISTORY_TABLE_NAME: props.chatHistoryTable.tableName,
  SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
  LOG_LEVEL: 'INFO',
  LOG_AGENT_TRACE: 'true',
  LOG_TOOL_TRACE: 'true',
  LOG_NEPTUNE_TRACE: 'true',
  LOG_QUERY_TEXT: 'true',
  LOG_NEPTUNE_QUERY_TEXT: 'true',
  LOG_MAX_TEXT_CHARS: '500',
  LOG_MAX_QUERY_CHARS: '1000',
  EMIT_AGENT_TRACE: 'true',
},
```

- [ ] **Step 2: Run `cdk synth` to verify the stack is valid**

```bash
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk synth -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --quiet
```

Expected: synth succeeds with no errors.

- [ ] **Step 3: Run `cdk diff` to verify the change is purely additive**

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
```

Expected: one env var added to `AgenticRetrievalFunction`. No IAM changes, no new resources.

- [ ] **Step 4: Commit**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
git add packages/graphrag/infra/graphrag-messages-stack.ts
git commit -m "feat(graphrag): add EMIT_AGENT_TRACE env var to agentic lambda"
```

---

## Task 11: Frontend — `AgentTraceEvent` type + store action

**Files:**
- Modify: `packages/webapp/src/stores/types.ts`
- Modify: `packages/webapp/src/stores/chat-store.ts`
- Test: `packages/webapp/src/stores/test/chat-store.test.ts` (create)

- [ ] **Step 1: Add the type**

Edit `packages/webapp/src/stores/types.ts`:

Add above the `Query` interface:

```ts
export interface AgentTraceEvent {
  kind: 'loop_start' | 'reasoning' | 'tool_call' | 'tool_result' | 'loop_complete';
  turn?: number | null;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
  devPayload?: Record<string, unknown>;
}
```

Add to the `Query` interface:

```ts
export interface Query {
  // ...existing fields...
  thinkingDuration?: number;
  agentTrace?: AgentTraceEvent[];
}
```

Add to the `ChatStore` interface, with the other action signatures:

```ts
appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) => void;
```

- [ ] **Step 2: Write a failing test for the store action**

Create `packages/webapp/src/stores/test/chat-store.test.ts`:

```ts
/** @bun */
import { describe, test, expect, beforeEach } from 'bun:test';
import { useChatStore } from '../chat-store';
import type { Query } from '../types';

describe('chat-store agentTrace', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  test('appendAgentTraceEvent creates array on first call', () => {
    const q: Query = {
      query: 'hello', queryId: 'q1', type: 'outbound',
      timestamp: new Date().toISOString(), status: 'pending',
      response: { type: 'stream', content: '' },
    };
    useChatStore.getState().addQuery(q);
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start', seq: 1, timestamp: 1, payload: { maxTurns: 10 },
    });
    expect(useChatStore.getState().queries.q1.agentTrace).toHaveLength(1);
    expect(useChatStore.getState().queries.q1.agentTrace?.[0].kind).toBe('loop_start');
  });

  test('appendAgentTraceEvent dedupes by seq', () => {
    const q: Query = {
      query: 'hello', queryId: 'q1', type: 'outbound',
      timestamp: new Date().toISOString(), status: 'pending',
      response: { type: 'stream', content: '' },
    };
    useChatStore.getState().addQuery(q);
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start', seq: 1, timestamp: 1, payload: {},
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start', seq: 1, timestamp: 1, payload: {},
    });
    expect(useChatStore.getState().queries.q1.agentTrace).toHaveLength(1);
  });

  test('appendAgentTraceEvent is a no-op for unknown queryId', () => {
    useChatStore.getState().appendAgentTraceEvent('nonexistent', {
      kind: 'loop_start', seq: 1, timestamp: 1, payload: {},
    });
    expect(useChatStore.getState().queries).toEqual({});
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd packages/webapp && bun test src/stores/test/chat-store.test.ts
```

Expected: FAIL with "appendAgentTraceEvent is not a function".

- [ ] **Step 4: Implement the action in `chat-store.ts`**

Edit `packages/webapp/src/stores/chat-store.ts`. Add the action alongside the other query actions:

```ts
    appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) =>
      set(state => {
        const query = state.queries[queryId];
        if (!query) return;
        if (!query.agentTrace) query.agentTrace = [];
        if (query.agentTrace.some(e => e.seq === event.seq)) return;
        query.agentTrace.push(event);
      }),
```

Add `AgentTraceEvent` to the type imports at the top:

```ts
import type {
  AgentTraceEvent,
  ChatError,
  // ...rest unchanged
} from './types';
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd packages/webapp && bun test src/stores/test/chat-store.test.ts
```

Expected: all 3 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
git add packages/webapp/src/stores/types.ts \
        packages/webapp/src/stores/chat-store.ts \
        packages/webapp/src/stores/test/chat-store.test.ts
git commit -m "feat(webapp): add AgentTraceEvent store action with seq dedupe"
```

---

## Task 12: Frontend — WebSocket `agent-event` routing

**Files:**
- Modify: `packages/webapp/src/hooks/use-websocket-chat.ts:51-125`
- Test: `packages/webapp/src/hooks/test/use-websocket-chat.test.tsx`

- [ ] **Step 1: Write the failing test**

Append a new test to `packages/webapp/src/hooks/test/use-websocket-chat.test.tsx`, inside the existing `describe('useWebSocketChat Hook Tests', ...)` block:

```tsx
  test('routes agent-event messages into agentTrace store', async () => {
    const options = { websocketUrl: 'wss://test-websocket.example.com' };
    const createWrapper = (queryClient: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };
    renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    // Seed a query in the store
    act(() => {
      useChatStore.getState().addQuery({
        query: 'hello', queryId: 'q1', type: 'outbound',
        timestamp: new Date().toISOString(), status: 'pending',
        response: { type: 'stream', content: '' },
      });
    });

    expect(mockMessageHandler).toBeDefined();

    // Dispatch an agent-event
    act(() => {
      mockMessageHandler!({
        responseType: 'agent-event',
        queryId: 'q1',
        kind: 'tool_call',
        turn: 1,
        seq: 1,
        timestamp: Date.now(),
        payload: { toolName: 'vector_search', summary: '"use value"' },
        devPayload: {},
      } as MessageUnion);
    });

    const trace = useChatStore.getState().queries.q1.agentTrace;
    expect(trace).toHaveLength(1);
    expect(trace?.[0].kind).toBe('tool_call');
    expect(trace?.[0].payload).toEqual({ toolName: 'vector_search', summary: '"use value"' });

    // Dispatch a duplicate seq; dedupe should apply
    act(() => {
      mockMessageHandler!({
        responseType: 'agent-event',
        queryId: 'q1',
        kind: 'tool_call',
        turn: 1,
        seq: 1,
        timestamp: Date.now(),
        payload: { toolName: 'vector_search', summary: '"different"' },
        devPayload: {},
      } as MessageUnion);
    });
    expect(useChatStore.getState().queries.q1.agentTrace).toHaveLength(1);
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd packages/webapp && bun test src/hooks/test/use-websocket-chat.test.tsx -t "routes agent-event"
```

Expected: FAIL (no trace appended; `agent-event` case not handled).

- [ ] **Step 3: Add the case to `messageHandler`**

Edit `packages/webapp/src/hooks/use-websocket-chat.ts`. Add an import:

```ts
const appendAgentTraceEvent = useChatStore(state => state.appendAgentTraceEvent);
```

Add a case inside the existing `switch (message.responseType)` block:

```ts
            case 'agent-event':
              appendAgentTraceEvent(message.queryId, {
                kind: message.kind,
                turn: message.turn ?? undefined,
                seq: message.seq,
                timestamp: message.timestamp,
                payload: message.payload,
                devPayload: message.devPayload,
              });
              break;
```

Add `appendAgentTraceEvent` to the `useCallback` dependency array:

```ts
    },
    [
      updateQueryResources,
      updateQueryStatus,
      appendQueryResponse,
      appendAgentTraceEvent,
      setChatState,
      setQueryError,
      handleError,
    ]
  );
```

- [ ] **Step 4: Run all webapp tests**

```bash
cd packages/webapp && bun test
```

Expected: all pass, including the new agent-event test.

- [ ] **Step 5: Commit**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
git add packages/webapp/src/hooks/use-websocket-chat.ts \
        packages/webapp/src/hooks/test/use-websocket-chat.test.tsx
git commit -m "feat(webapp): route agent-event messages into chat store"
```

---

## Task 13: Frontend — `useDevTrace` hook

**Files:**
- Create: `packages/webapp/src/hooks/use-dev-trace.ts`
- Create: `packages/webapp/src/hooks/test/use-dev-trace.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `packages/webapp/src/hooks/test/use-dev-trace.test.tsx`:

```tsx
/** @bun */
import { describe, test, expect, beforeEach } from 'bun:test';
import { renderHook } from '@testing-library/react';
import { JSDOM } from 'jsdom';
import { useDevTrace } from '../use-dev-trace';

describe('useDevTrace', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
      url: 'http://localhost/chat',
    });
    global.document = dom.window.document;
    (global as unknown as { window: typeof dom.window }).window = dom.window;
    global.navigator = dom.window.navigator;
    global.localStorage = dom.window.localStorage;
  });

  test('returns false by default', () => {
    const { result } = renderHook(() => useDevTrace());
    expect(result.current).toBe(false);
  });

  test('returns true when ?debug=1 is in URL', () => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
      url: 'http://localhost/chat?debug=1',
    });
    global.window = dom.window as unknown as Window & typeof globalThis;
    const { result } = renderHook(() => useDevTrace());
    expect(result.current).toBe(true);
  });

  test('returns true when localStorage flag is set', () => {
    global.localStorage.setItem('wisco:devTrace', '1');
    const { result } = renderHook(() => useDevTrace());
    expect(result.current).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd packages/webapp && bun test src/hooks/test/use-dev-trace.test.tsx
```

Expected: FAIL with "Cannot find module '../use-dev-trace'".

- [ ] **Step 3: Create the hook**

Create `packages/webapp/src/hooks/use-dev-trace.ts`:

```ts
'use client';
import { useMemo } from 'react';

const STORAGE_KEY = 'wisco:devTrace';

export function useDevTrace(): boolean {
  return useMemo(() => {
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    if (params.get('debug') === '1') return true;
    try {
      return window.localStorage.getItem(STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  }, []);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd packages/webapp && bun test src/hooks/test/use-dev-trace.test.tsx
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
git add packages/webapp/src/hooks/use-dev-trace.ts \
        packages/webapp/src/hooks/test/use-dev-trace.test.tsx
git commit -m "feat(webapp): add useDevTrace hook for URL-param + localStorage toggle"
```

---

## Task 14: Frontend — render live trace in `ChatMessage`

**Files:**
- Modify: `packages/webapp/src/components/messages/chat-message.tsx:270-382`

This is the user-facing change. We replace the static 3-step derivation with a trace-driven one, with a legacy fallback.

- [ ] **Step 1: Extract legacy steps into a helper**

At the top of `packages/webapp/src/components/messages/chat-message.tsx` (below imports), add:

```ts
import type { AgentTraceEvent } from '@/stores/types';
import { useDevTrace } from '@/hooks/use-dev-trace';

type TraceStep = { label: string; done: boolean; devJson?: string };

const TOOL_VERBS: Record<string, string> = {
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

function verbFor(toolName: string): string {
  return TOOL_VERBS[toolName] ?? toolName;
}

function buildLegacySteps({
  hasResources,
  items,
  isStreaming,
  streamingComplete,
}: {
  hasResources: boolean;
  items?: ResourceItem[];
  isStreaming: boolean;
  streamingComplete?: boolean;
}): TraceStep[] {
  const steps: TraceStep[] = [];
  const searchDone = hasResources || isStreaming || streamingComplete === true;
  steps.push({
    label: searchDone ? 'Searched knowledge base' : 'Searching knowledge base...',
    done: searchDone,
  });
  if (hasResources && items) {
    steps.push({
      label: `Found ${items.length} source${items.length === 1 ? '' : 's'}`,
      done: true,
    });
  }
  const genDone = isStreaming || streamingComplete === true;
  steps.push({
    label: genDone ? 'Generated response' : 'Generating response...',
    done: genDone,
  });
  return steps;
}

function renderTraceStep(event: AgentTraceEvent, devMode: boolean): TraceStep | null {
  const devJson = devMode && event.devPayload && Object.keys(event.devPayload).length > 0
    ? JSON.stringify(event.devPayload, null, 2)
    : undefined;

  if (event.kind === 'reasoning') {
    const text = String(event.payload.text ?? '');
    return text ? { label: text, done: true, devJson } : null;
  }
  if (event.kind === 'tool_call') {
    const name = String(event.payload.toolName ?? '');
    const summary = String(event.payload.summary ?? '');
    const label = summary ? `${verbFor(name)} ${summary}` : verbFor(name);
    // tool_call is "in progress" until a matching tool_result arrives; the
    // caller (buildTraceSteps) marks it done post-hoc based on pairing.
    return { label, done: false, devJson };
  }
  if (event.kind === 'tool_result') {
    const summary = String(event.payload.summary ?? '');
    const status = String(event.payload.status ?? 'ok');
    const done = status === 'ok' || status === 'terminal';
    return { label: summary, done, devJson };
  }
  return null;
}

function buildTraceSteps(
  trace: AgentTraceEvent[],
  devMode: boolean
): TraceStep[] {
  // Filter out loop_start and loop_complete — the outer "Thinking for Xs"
  // label already communicates start/end of the loop.
  const visible = trace.filter(
    e => e.kind !== 'loop_start' && e.kind !== 'loop_complete'
  );
  const steps: TraceStep[] = [];
  for (const event of visible) {
    const step = renderTraceStep(event, devMode);
    if (!step) continue;
    steps.push(step);
  }
  // If the last visible event is a tool_call with no matching tool_result,
  // leave it as "in progress" (done=false). That's already the case because
  // tool_result flips the matching step, but here we use visibility to gate
  // the current step's done-ness.
  return steps;
}
```

- [ ] **Step 2: Replace the existing `steps` useMemo**

Find the current block in `ChatMessage` (~line 267-280):

```ts
  const [stepsOpen, setStepsOpen] = useState(true);

  // Derive current step
  const steps = useMemo(() => {
    const s: { label: string; done: boolean }[] = [];
    const searchDone = hasResources || isStreaming || streamingComplete === true;
    s.push({ label: searchDone ? 'Searched knowledge base' : 'Searching knowledge base...', done: searchDone });
    if (hasResources) {
      s.push({ label: `Found ${items!.length} source${items!.length === 1 ? '' : 's'}`, done: true });
    }
    const genDone = isStreaming || streamingComplete === true;
    s.push({ label: genDone ? 'Generated response' : 'Generating response...', done: genDone });
    return s;
  }, [hasResources, items, isStreaming, streamingComplete]);
```

Replace with:

```ts
  const [stepsOpen, setStepsOpen] = useState(true);
  const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
  const devTrace = useDevTrace();

  const steps = useMemo<TraceStep[]>(() => {
    if (!agentTrace || agentTrace.length === 0) {
      return buildLegacySteps({
        hasResources,
        items,
        isStreaming,
        streamingComplete,
      });
    }
    return buildTraceSteps(agentTrace, devTrace);
  }, [agentTrace, devTrace, hasResources, items, isStreaming, streamingComplete]);
```

- [ ] **Step 3: Render the optional `devJson` disclosure**

In the existing `steps.map((step, i) => ...)` block (~line 365-376), extend it so each bullet renders an optional disclosure when `step.devJson` is present:

```tsx
                      {steps.map((step, i) => (
                        <div key={i} className="flex flex-col gap-1 -ml-[4px]">
                          <div className="flex items-center gap-2.5">
                            <div
                              className={`h-[7px] w-[7px] shrink-0 rounded-full transition-colors duration-500 ${
                                step.done
                                  ? 'bg-muted-foreground'
                                  : 'border border-muted-foreground/50 bg-background'
                              }`}
                            />
                            <span>{step.label}</span>
                          </div>
                          {step.devJson && (
                            <details className="ml-5 text-[0.75em] text-muted-foreground/80">
                              <summary className="cursor-pointer select-none">devPayload</summary>
                              <pre className="overflow-auto bg-muted/40 rounded p-2 mt-1 text-xs">
                                {step.devJson}
                              </pre>
                            </details>
                          )}
                        </div>
                      ))}
```

- [ ] **Step 4: Typecheck**

```bash
cd packages/webapp && bunx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Run all webapp tests**

```bash
cd packages/webapp && bun test
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
git add packages/webapp/src/components/messages/chat-message.tsx
git commit -m "feat(webapp): render live agent trace in thinking panel"
```

---

## Task 15: Deploy to us-east-1 and smoke-test

**Files:**
- None (deploy + verify)

This is the "does it actually work" task. Do **not** deploy to us-west-2 from this feature branch (CLAUDE.md project rule).

- [ ] **Step 1: Full local checks**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
bun run test
uv run pytest packages/graphrag/lambdas/test -v
uv run pytest packages/shared/lambda_layers/test -v
bunx eslint packages/webapp packages/messages
uv run ruff check packages/graphrag
```

Expected: all green.

- [ ] **Step 2: Bundle and diff**

```bash
bun run bundle
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
```

Expected: changes limited to:
- `AgenticRetrievalFunction`: new env var `EMIT_AGENT_TRACE=true`, new Lambda code hash (because source changed).
- Webapp assets: new bundle.
- No IAM changes. No new resources.

- [ ] **Step 3: Deploy**

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```

Expected: stack updates cleanly.

- [ ] **Step 4: Smoke-test the UI**

Open the GraphRAG test site in a browser. Ask a question that triggers the tool loop (anything that will not FAQ-short-circuit — for example, *"Can the assessor value an agricultural parcel under a different rule than §70.32?"*).

Verify:
- Live trace steps appear under "Thinking for Xs..." during the loop — new bullets arrive every few seconds.
- Steps include reasoning text (italicized), tool-call labels (e.g. *"Searching for ..."*), and tool-result summaries (e.g. *"Found 6 chunks across 3 docs"*).
- On completion, the trace auto-collapses into "Thought for Xs".
- Clicking the collapsed label re-expands the full trace.

Append `?debug=1` to the URL and repeat. Verify:
- Each step now shows a `devPayload` disclosure.
- Clicking the disclosure reveals pretty-printed JSON with `toolInput`, `raw`, `toolLatencyMs`.

- [ ] **Step 5: Smoke-test the FAQ short-circuit**

Ask a question known to clear `FAQ_SCORE_THRESHOLD` (e.g. a direct FAQ question from the corpus).

Verify:
- No mid-loop trace bullets appear.
- The thinking panel shows the legacy 3-step placeholder ("Searching knowledge base..." → "Found N sources" → "Generating response...") — this is expected because the FAQ short-circuit emits only `loop_start` + `loop_complete`, both filtered out by `buildTraceSteps`, leaving an empty visible trace and falling through to the legacy steps via the length check.

  If you prefer FAQ short-circuits to show *something* richer, that's a Task-14 followup; the current behavior is intentional per spec.

- [ ] **Step 6: Smoke-test the kill switch**

Via AWS Console, set `EMIT_AGENT_TRACE=false` on `AgenticRetrievalFunction` and save. Submit a new query.

Verify:
- No trace bullets arrive.
- UI falls back to the legacy 3-step placeholder.
- Document cards and streaming response still work normally.

Revert `EMIT_AGENT_TRACE=true`.

- [ ] **Step 7: Final commit (if any env/code adjustments were needed during smoke)**

If none, skip. Otherwise:

```bash
git add -p   # carefully stage fixes
git commit -m "fix(graphrag|webapp): <specific smoke-test fix>"
```

---

## Followups (explicitly out of scope — do not implement here)

- Persist `agentTrace` in the chat-history DynamoDB table so reloading a past conversation replays the trace.
- "Export trace" action in `MessageOptionsBar`.
- Richer per-turn card view (rejected Option B from brainstorming).
- FAQ short-circuit: show *something* (e.g. "Matched FAQ 'X' at 0.92 score") instead of falling through to the legacy 3-step placeholder.
