# Agentic Retrieval Query Tracing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON tracing to the GraphRAG agentic retrieval Lambda so every query produces a consolidated log line with the full decision trail (FAQ evaluation, Claude reasoning, tool calls, sources, answer generation).

**Architecture:** Dataclass-based `TraceContext` created at handler entry, threaded into `run_agentic_loop()`, accumulates `TraceStep` entries per tool call, derives `FaqDecision` and `AnswerSummary`, emits one structured JSON log line per query in a `finally` block.

**Tech Stack:** Python stdlib (`dataclasses`, `json`, `time`, `logging`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-16-agentic-retrieval-tracing-design.md`

---

### Task 1: Add trace dataclasses

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:1-18` (imports + new dataclass block)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write tests for trace dataclasses**

Add a new test file section in the existing test file. These tests verify the dataclasses can be constructed and serialized to JSON (the core contract of the trace system).

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
import json
from dataclasses import asdict


def test_trace_step_to_dict():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import TraceStep

    step = TraceStep(
        turn=1,
        tool_name="faq_search",
        tool_input={"query": "test", "top_k": 5},
        tool_result={"faqs": [{"text": "answer", "score": 0.9}], "count": 1},
        reasoning_text=None,
        duration_ms=123.4,
    )
    d = asdict(step)
    assert d["tool_name"] == "faq_search"
    assert d["reasoning_text"] is None
    assert d["duration_ms"] == 123.4
    # Must be JSON-serializable
    json.dumps(d)


def test_faq_decision_to_dict():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import FaqDecision

    decision = FaqDecision(
        query_used="property tax",
        num_results=3,
        top_scores=[0.82, 0.65, 0.41],
        accepted=False,
        reasoning="FAQs are partially relevant but lack specifics",
    )
    d = asdict(decision)
    assert d["accepted"] is False
    assert d["top_scores"] == [0.82, 0.65, 0.41]
    json.dumps(d)


def test_answer_summary_to_dict():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import AnswerSummary

    summary = AnswerSummary(
        cited_doc_ids=["DOC-1", "DOC-2"],
        num_sources=2,
        answer_length=500,
        tools_used=["faq_search", "vector_search", "answer"],
        total_turns=3,
        total_duration_ms=2500.0,
    )
    d = asdict(summary)
    assert d["num_sources"] == 2
    assert len(d["tools_used"]) == 3
    json.dumps(d)


def test_trace_context_emit(caplog):
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import TraceContext, TraceStep, AnswerSummary
    import time

    trace = TraceContext(
        query_id="q-test",
        session_id="s-test",
        query="What is property tax?",
        started_at=time.time(),
        steps=[
            TraceStep(
                turn=1,
                tool_name="faq_search",
                tool_input={"query": "property tax"},
                tool_result={"faqs": [], "count": 0},
                reasoning_text=None,
                duration_ms=100.0,
            ),
        ],
    )
    trace.answer_summary = AnswerSummary(
        cited_doc_ids=[],
        num_sources=0,
        answer_length=42,
        tools_used=["faq_search", "answer"],
        total_turns=1,
        total_duration_ms=200.0,
    )

    import logging
    with caplog.at_level(logging.INFO):
        trace.emit()

    # Find the trace log line
    trace_lines = [r for r in caplog.records if "query_trace" in r.getMessage()]
    assert len(trace_lines) == 1
    log_data = json.loads(trace_lines[0].getMessage())
    assert log_data["trace_type"] == "query_trace"
    assert log_data["query_id"] == "q-test"
    assert log_data["query"] == "What is property tax?"
    assert len(log_data["steps"]) == 1
    assert log_data["answer_summary"]["num_sources"] == 0
    assert log_data["error"] is None
    assert log_data["max_turns_exhausted"] is False


def test_trace_context_emit_with_error(caplog):
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import TraceContext
    import time
    import logging

    trace = TraceContext(
        query_id="q-err",
        session_id="s-err",
        query="broken query",
        started_at=time.time(),
        steps=[],
    )
    trace.error = "Neptune timeout"

    with caplog.at_level(logging.INFO):
        trace.emit()

    trace_lines = [r for r in caplog.records if "query_trace" in r.getMessage()]
    assert len(trace_lines) == 1
    log_data = json.loads(trace_lines[0].getMessage())
    assert log_data["error"] == "Neptune timeout"
    assert log_data["steps"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v -k "test_trace or test_faq_decision or test_answer_summary" 2>&1 | tail -20`

Expected: FAIL — `ImportError: cannot import name 'TraceStep' from 'main'` (dataclasses don't exist yet)

- [ ] **Step 3: Add dataclass definitions and imports**

Add to `packages/graphrag/lambdas/agentic_retrieval/main.py`. Insert after the existing imports (after line 17 `from typing import Any`), before `MAX_TURNS`:

```python
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class TraceStep:
    turn: int
    tool_name: str
    tool_input: dict
    tool_result: dict
    reasoning_text: str | None
    duration_ms: float


@dataclass
class FaqDecision:
    query_used: str
    num_results: int
    top_scores: list[float]
    accepted: bool
    reasoning: str


@dataclass
class AnswerSummary:
    cited_doc_ids: list[str]
    num_sources: int
    answer_length: int
    tools_used: list[str]
    total_turns: int
    total_duration_ms: float


@dataclass
class TraceContext:
    query_id: str
    session_id: str
    query: str
    started_at: float
    steps: list[TraceStep] = field(default_factory=list)
    faq_decision: FaqDecision | None = None
    answer_summary: AnswerSummary | None = None
    error: str | None = None
    max_turns_exhausted: bool = False

    def finalize(self, cited_doc_ids: list[str], answer: str, total_turns: int):
        self.answer_summary = AnswerSummary(
            cited_doc_ids=cited_doc_ids,
            num_sources=len(cited_doc_ids),
            answer_length=len(answer),
            tools_used=[s.tool_name for s in self.steps],
            total_turns=total_turns,
            total_duration_ms=(time.time() - self.started_at) * 1000,
        )

    def emit(self):
        log_entry = {
            "trace_type": "query_trace",
            "query_id": self.query_id,
            "session_id": self.session_id,
            "query": self.query,
            "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(),
            "duration_ms": (time.time() - self.started_at) * 1000,
            "faq_decision": asdict(self.faq_decision) if self.faq_decision else None,
            "steps": [asdict(s) for s in self.steps],
            "answer_summary": asdict(self.answer_summary) if self.answer_summary else None,
            "error": self.error,
            "max_turns_exhausted": self.max_turns_exhausted,
        }
        logger.info(json.dumps(log_entry))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v -k "test_trace or test_faq_decision or test_answer_summary" 2>&1 | tail -20`

Expected: All 6 new tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat: add trace dataclasses for agentic retrieval logging"
```

---

### Task 2: Thread TraceContext through `run_agentic_loop()` — capture reasoning and tool calls

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:85-162` (`run_agentic_loop`)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write test for reasoning text extraction and step capture**

This test mocks `bedrock.converse` to return a response with text blocks (reasoning) and tool_use blocks, verifying the trace captures both.

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_run_agentic_loop_captures_trace_steps():
    """Verify the agentic loop populates TraceContext with steps, reasoning, and timing."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.vector_search.return_value = []
        mock_neptune.get_document.return_value = {"title": "Test", "id": "doc-1"}
        MockNeptune.return_value = mock_neptune

        # Force re-import with mocks
        if "main" in sys.modules:
            del sys.modules["main"]
        if "tools" in sys.modules:
            del sys.modules["tools"]

        with patch.dict(os.environ, {"FAQ_KNOWLEDGE_BASE_ID": "kb-123"}):
            import main
            main.neptune = mock_neptune

            # Simulate 2-turn conversation:
            # Turn 1: Claude reasons, calls faq_search
            # Turn 2: Claude reasons about FAQ results, calls answer
            main.bedrock.converse = MagicMock(side_effect=[
                # Turn 1: reasoning + faq_search tool call
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"text": "Let me search the FAQs first."},
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-1",
                                        "name": "faq_search",
                                        "input": {"query": "test question", "top_k": 5},
                                    }
                                },
                            ],
                        }
                    }
                },
                # Turn 2: reasoning about FAQ results + answer
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"text": "The FAQs don't cover this. Let me provide a direct answer."},
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-2",
                                        "name": "answer",
                                        "input": {
                                            "response": "Here is the answer.",
                                            "cited_doc_ids": [],
                                        },
                                    }
                                },
                            ],
                        }
                    }
                },
            ])

            # Mock faq_search via bedrock_agent_runtime
            import tools
            tools.bedrock_agent_runtime = MagicMock()
            tools.bedrock_agent_runtime.retrieve.return_value = {
                "retrievalResults": [
                    {"content": {"text": "Q: What?\nA: Something."}, "score": 0.7}
                ]
            }

            import time
            trace = main.TraceContext(
                query_id="q-1",
                session_id="s-1",
                query="test question",
                started_at=time.time(),
            )

            answer, doc_ids, rag_docs = main.run_agentic_loop("test question", trace)

            assert answer == "Here is the answer."
            assert len(trace.steps) == 2

            # Step 1: faq_search with reasoning
            assert trace.steps[0].tool_name == "faq_search"
            assert trace.steps[0].reasoning_text == "Let me search the FAQs first."
            assert trace.steps[0].turn == 1
            assert trace.steps[0].duration_ms >= 0

            # Step 2: answer with reasoning about FAQ results
            assert trace.steps[1].tool_name == "answer"
            assert "FAQs don't cover this" in trace.steps[1].reasoning_text
            assert trace.steps[1].turn == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py::test_run_agentic_loop_captures_trace_steps -v 2>&1 | tail -20`

Expected: FAIL — `run_agentic_loop()` doesn't accept a `trace` parameter yet

- [ ] **Step 3: Modify `run_agentic_loop()` to accept and populate TraceContext**

Replace the `run_agentic_loop` function in `packages/graphrag/lambdas/agentic_retrieval/main.py`. The current function signature is at line 85. The full replacement:

```python
def run_agentic_loop(query: str, trace: TraceContext) -> tuple[str, list[str], list[RAGDocument]]:
    """
    Run Claude's agentic loop against Neptune.

    Returns:
        (answer_text, cited_doc_ids, rag_documents)
    """
    messages = [{"role": "user", "content": [{"text": query}]}]
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        logger.info(f"Agentic loop turn {turn + 1}/{MAX_TURNS}")

        response = bedrock.converse(
            modelId=AGENTIC_MODEL_ID,
            messages=messages,
            system=[{"text": SYSTEM_PROMPT}],
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        # Extract reasoning text from text blocks
        reasoning_parts = [
            block["text"] for block in assistant_message["content"]
            if "text" in block
        ]
        reasoning_text = "\n".join(reasoning_parts) if reasoning_parts else None

        tool_uses = [
            block for block in assistant_message["content"]
            if "toolUse" in block
        ]

        if not tool_uses:
            text_blocks = [
                block["text"] for block in assistant_message["content"]
                if "text" in block
            ]
            answer = "\n".join(text_blocks)
            break

        tool_results = []
        tool_names_this_turn = []
        for tool_use in tool_uses:
            tool = tool_use["toolUse"]
            tool_name = tool["name"]
            tool_input = tool["input"]
            tool_use_id = tool["toolUseId"]

            tool_names_this_turn.append(tool_name)

            t0 = time.time()
            result = execute_tool(tool_name, tool_input, neptune)
            duration_ms = (time.time() - t0) * 1000

            # Per-step log for real-time tailing
            result_summary = _summarize_tool_result(tool_name, result)
            logger.info(json.dumps({
                "query_id": trace.query_id,
                "turn": turn + 1,
                "tool": tool_name,
                "duration_ms": round(duration_ms, 1),
                "result_summary": result_summary,
            }))

            trace.steps.append(TraceStep(
                turn=turn + 1,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_result=result,
                reasoning_text=reasoning_text,
                duration_ms=round(duration_ms, 1),
            ))
            # Only attach reasoning to the first tool call in a turn
            reasoning_text = None

            # Derive FaqDecision after faq_search
            if tool_name == "faq_search" and trace.faq_decision is None:
                faqs = result.get("faqs", [])
                scores = [f.get("score", 0.0) for f in faqs]
                has_answer_in_turn = "answer" in tool_names_this_turn
                trace.faq_decision = FaqDecision(
                    query_used=tool_input.get("query", ""),
                    num_results=result.get("count", len(faqs)),
                    top_scores=sorted(scores, reverse=True),
                    accepted=has_answer_in_turn,
                    reasoning="",  # filled in next turn when we get reasoning text
                )

            # If we had a pending faq_decision with empty reasoning, fill it
            if (
                trace.faq_decision
                and not trace.faq_decision.reasoning
                and trace.steps[-1].reasoning_text
                and tool_name != "faq_search"
            ):
                trace.faq_decision.reasoning = trace.steps[-1].reasoning_text

            if tool_name == "vector_search" and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                    all_chunks.append(chunk)

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = result.get("cited_doc_ids", [])
                all_doc_ids.update(cited)

                # Check if FAQ was accepted (answer in same turn as faq_search)
                if trace.faq_decision and "faq_search" in tool_names_this_turn:
                    trace.faq_decision.accepted = True

                rag_docs = _build_rag_documents(all_chunks, all_doc_ids)
                return answer, list(all_doc_ids), rag_docs

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                }
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        trace.max_turns_exhausted = True
        answer = "I was unable to find a complete answer within the allowed number of search steps. Please try rephrasing your question."

    rag_docs = _build_rag_documents(all_chunks, all_doc_ids)
    return answer, list(all_doc_ids), rag_docs


def _summarize_tool_result(tool_name: str, result: dict) -> str:
    """One-line summary of a tool result for per-step logs."""
    if tool_name == "faq_search":
        faqs = result.get("faqs", [])
        top = faqs[0].get("score", 0) if faqs else 0
        return f"{len(faqs)} FAQs, top score {top:.2f}"
    elif tool_name == "vector_search":
        chunks = result.get("chunks", [])
        return f"{len(chunks)} chunks"
    elif tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        return f"{len(neighbors)} neighbors"
    elif tool_name == "get_authority_chain":
        chain = result.get("authority_chain", [])
        return f"{len(chain)} nodes in chain"
    elif tool_name == "get_document":
        return result.get("document", {}).get("title", "not found")
    elif tool_name == "list_framework_docs":
        docs = result.get("documents", [])
        return f"{len(docs)} docs"
    elif tool_name == "answer":
        return f"{len(result.get('response', ''))} chars, {len(result.get('cited_doc_ids', []))} citations"
    return str(result)[:100]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py::test_run_agentic_loop_captures_trace_steps -v 2>&1 | tail -20`

Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v 2>&1 | tail -30`

Expected: All tests pass. Note: `test_build_rag_documents` doesn't call `run_agentic_loop`, so it's unaffected. `test_process_event_*` tests are also unaffected. Existing tests that call `run_agentic_loop` directly will need the new `trace` parameter — if any fail, add a `TraceContext` fixture to them.

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat: capture reasoning text and tool calls in TraceContext"
```

---

### Task 3: Wire TraceContext into `handler()` with `finally` emit

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:226-271` (`handler`)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write test for handler trace emission**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_handler_emits_trace_on_success(caplog):
    """Verify handler creates TraceContext, calls run_agentic_loop, and emits trace."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main

    mock_result = MagicMock()
    mock_result.model_dump.return_value = {"successful": True}
    models_mock.RetrieveResult.return_value = mock_result
    models_mock.DocumentResource.return_value = MagicMock()
    models_mock.GenerateResponseJob.return_value = MagicMock()
    models_mock.StreamResourcesJob.return_value = MagicMock()

    with patch.object(main, "run_agentic_loop") as mock_loop, \
         patch.object(main, "process_event") as mock_process:

        mock_process.return_value = MockUserQuery(
            query="test query", query_id="q-handler", session_id="s-handler"
        )
        mock_loop.return_value = ("answer text", ["doc-1"], [])

        import logging
        with caplog.at_level(logging.INFO):
            main.handler({"query": "test", "query_id": "q-handler", "session_id": "s-handler"}, None)

        # Verify run_agentic_loop was called with a TraceContext
        args = mock_loop.call_args
        assert args[0][0] == "test query"  # query string
        trace_arg = args[0][1]
        assert trace_arg.query_id == "q-handler"
        assert trace_arg.session_id == "s-handler"

        # Verify trace was emitted
        trace_lines = [r for r in caplog.records if "query_trace" in r.getMessage()]
        assert len(trace_lines) == 1
        log_data = json.loads(trace_lines[0].getMessage())
        assert log_data["query_id"] == "q-handler"
        assert log_data["answer_summary"] is not None


def test_handler_emits_trace_on_error(caplog):
    """Verify handler emits trace even when run_agentic_loop raises."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main

    mock_result = MagicMock()
    mock_result.model_dump.return_value = {"successful": False}
    models_mock.RetrieveResult.return_value = mock_result

    with patch.object(main, "run_agentic_loop", side_effect=RuntimeError("Neptune down")), \
         patch.object(main, "process_event") as mock_process:

        mock_process.return_value = MockUserQuery(
            query="test query", query_id="q-err", session_id="s-err"
        )
        errors_mock.report_error = MagicMock()

        import logging
        with caplog.at_level(logging.INFO):
            result = main.handler({"query": "test", "query_id": "q-err", "session_id": "s-err"}, None)

        # Verify trace was still emitted despite error
        trace_lines = [r for r in caplog.records if "query_trace" in r.getMessage()]
        assert len(trace_lines) == 1
        log_data = json.loads(trace_lines[0].getMessage())
        assert log_data["query_id"] == "q-err"
        assert log_data["error"] == "Neptune down"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v -k "test_handler_emits" 2>&1 | tail -20`

Expected: FAIL — handler doesn't create TraceContext yet

- [ ] **Step 3: Modify `handler()` to create, finalize, and emit TraceContext**

Replace the `handler` function in `packages/graphrag/lambdas/agentic_retrieval/main.py`:

```python
def handler(event: dict, context) -> dict[str, Any]:
    """
    Lambda handler. Processes a UserQuery via agentic retrieval,
    returns a RetrieveResult compatible with the existing Step Functions flow.
    """
    session_id: str | None = None
    trace: TraceContext | None = None

    try:
        user_query = process_event(event)
        session_id = user_query.session_id
        logger.info(f"Agentic retrieval for query: {user_query.query[:200]}")

        trace = TraceContext(
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            query=user_query.query,
            started_at=time.time(),
        )

        answer, cited_doc_ids, rag_documents = run_agentic_loop(user_query.query, trace)

        trace.finalize(cited_doc_ids, answer, len({s.turn for s in trace.steps}))

        documents = DocumentResource(documents=rag_documents)

        result = RetrieveResult(
            successful=True,
            generate_response_job=GenerateResponseJob(
                query=user_query.query,
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                documents=documents,
                faqs=None,
            ),
            stream_documents_job=StreamResourcesJob(
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                faqs=None,
                documents=documents,
            ),
        )

        return result.model_dump()

    except Exception as e:
        logger.error(f"Agentic retrieval failed: {e}", exc_info=True)
        if trace:
            trace.error = str(e)
        if session_id:
            asyncio.run(report_error(e, session_id=session_id))

        return RetrieveResult(
            successful=False,
            generate_response_job=None,
            stream_documents_job=None,
        ).model_dump()

    finally:
        if trace:
            trace.emit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v -k "test_handler_emits" 2>&1 | tail -20`

Expected: Both `test_handler_emits_trace_on_success` and `test_handler_emits_trace_on_error` PASS

- [ ] **Step 5: Run full test suite**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v 2>&1 | tail -30`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat: wire TraceContext into handler with finally-block emit"
```

---

### Task 4: Test FaqDecision derivation — accepted vs rejected

**Files:**
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 1: Write test for FAQ accepted (answer in same turn)**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_faq_decision_accepted_when_answer_in_same_turn():
    """When Claude calls faq_search and answer in the same Bedrock response, FAQ is accepted."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        MockNeptune.return_value = mock_neptune

        if "main" in sys.modules:
            del sys.modules["main"]
        if "tools" in sys.modules:
            del sys.modules["tools"]

        with patch.dict(os.environ, {"FAQ_KNOWLEDGE_BASE_ID": "kb-123"}):
            import main
            import tools
            main.neptune = mock_neptune

            # Single turn: faq_search + answer in same response (parallel tool use)
            main.bedrock.converse = MagicMock(return_value={
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"text": "The FAQ directly answers this question."},
                            {
                                "toolUse": {
                                    "toolUseId": "tu-1",
                                    "name": "faq_search",
                                    "input": {"query": "simple question"},
                                }
                            },
                            {
                                "toolUse": {
                                    "toolUseId": "tu-2",
                                    "name": "answer",
                                    "input": {
                                        "response": "FAQ answer here.",
                                        "cited_doc_ids": [],
                                    },
                                }
                            },
                        ],
                    }
                }
            })

            tools.bedrock_agent_runtime = MagicMock()
            tools.bedrock_agent_runtime.retrieve.return_value = {
                "retrievalResults": [
                    {"content": {"text": "Q: Simple?\nA: Yes."}, "score": 0.95}
                ]
            }

            import time
            trace = main.TraceContext(
                query_id="q-faq-accept",
                session_id="s-1",
                query="simple question",
                started_at=time.time(),
            )

            answer, _, _ = main.run_agentic_loop("simple question", trace)

            assert trace.faq_decision is not None
            assert trace.faq_decision.accepted is True
            assert trace.faq_decision.top_scores == [0.95]
            assert trace.faq_decision.num_results == 1


def test_faq_decision_rejected_when_loop_continues():
    """When Claude calls faq_search then continues to vector_search, FAQ is rejected."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.vector_search.return_value = [
            {"doc_id": "doc-1", "text": "chunk text", "source_url": "", "s3_key": "", "start_page": 1, "end_page": 1, "heading": "", "subheading": "", "score": 0.8}
        ]
        mock_neptune.get_document.return_value = {"title": "Doc", "id": "doc-1"}
        MockNeptune.return_value = mock_neptune

        if "main" in sys.modules:
            del sys.modules["main"]
        if "tools" in sys.modules:
            del sys.modules["tools"]

        with patch.dict(os.environ, {"FAQ_KNOWLEDGE_BASE_ID": "kb-123"}):
            import main
            import tools
            main.neptune = mock_neptune

            main.bedrock.converse = MagicMock(side_effect=[
                # Turn 1: faq_search only
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-1",
                                        "name": "faq_search",
                                        "input": {"query": "complex question"},
                                    }
                                },
                            ],
                        }
                    }
                },
                # Turn 2: reasoning about FAQ + vector_search
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"text": "The FAQs are not specific enough. Searching documents."},
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-2",
                                        "name": "vector_search",
                                        "input": {"query": "complex question details"},
                                    }
                                },
                            ],
                        }
                    }
                },
                # Turn 3: answer
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-3",
                                        "name": "answer",
                                        "input": {
                                            "response": "Detailed answer.",
                                            "cited_doc_ids": ["doc-1"],
                                        },
                                    }
                                },
                            ],
                        }
                    }
                },
            ])

            tools.bedrock_agent_runtime = MagicMock()
            tools.bedrock_agent_runtime.retrieve.return_value = {
                "retrievalResults": [
                    {"content": {"text": "Q: General?\nA: General answer."}, "score": 0.6}
                ]
            }
            tools.bedrock = MagicMock()
            tools.bedrock.invoke_model.return_value = {
                "body": MagicMock(read=MagicMock(return_value=b'{"embedding": [0.1] * 1024}'))
            }

            import time
            trace = main.TraceContext(
                query_id="q-faq-reject",
                session_id="s-1",
                query="complex question",
                started_at=time.time(),
            )

            answer, _, _ = main.run_agentic_loop("complex question", trace)

            assert trace.faq_decision is not None
            assert trace.faq_decision.accepted is False
            assert trace.faq_decision.reasoning == "The FAQs are not specific enough. Searching documents."
```

- [ ] **Step 2: Run tests**

Run: `cd packages/graphrag/lambdas && uv run pytest test/test_agentic_retrieval.py -v -k "test_faq_decision" 2>&1 | tail -20`

Expected: Both PASS (the implementation from Task 2 already handles this)

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "test: verify FaqDecision accepted/rejected derivation"
```

---

### Task 5: Final verification — full suite + lint

**Files:**
- All modified files

- [ ] **Step 1: Run full Python test suite**

Run: `cd /Users/jonahchan/dev/dxhub/wisco && uv run pytest packages/graphrag/lambdas/test/ -v 2>&1 | tail -30`

Expected: All tests pass (including `test_tools.py` and `test_neptune_client.py`)

- [ ] **Step 2: Run linter**

Run: `cd /Users/jonahchan/dev/dxhub/wisco && uv run ruff check packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py`

Expected: No errors. If any, fix them.

- [ ] **Step 3: Run formatter**

Run: `cd /Users/jonahchan/dev/dxhub/wisco && uv run ruff format packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py`

- [ ] **Step 4: Verify no regressions in other packages**

Run: `cd /Users/jonahchan/dev/dxhub/wisco && uv run pytest packages/messages/lambdas/test/ -v 2>&1 | tail -20`

Expected: All pass — messages Lambdas are untouched.

- [ ] **Step 5: Final commit if lint/format changed anything**

```bash
git add -u
git commit -m "style: lint and format trace logging code"
```
