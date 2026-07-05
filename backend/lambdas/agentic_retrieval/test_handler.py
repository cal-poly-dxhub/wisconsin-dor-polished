"""Integration tests for handler and run_agentic_loop."""

import json
import sys
import os
import itertools
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

pydantic = pytest.importorskip("pydantic")


class MockUserQuery(pydantic.BaseModel):
    query: str
    query_id: str
    session_id: str


class MockRAGDocument(pydantic.BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"
    authority_level: int | None = None
    s3_key: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    edition_year: int | None = None

    def model_copy(self, update=None):
        data = self.model_dump()
        if update:
            data.update(update)
        return MockRAGDocument(**data)


class FakeFAQ(pydantic.BaseModel):
    faq_id: str
    question: str
    answer: str
    source_url: str | None = None


class FakeFAQResource(pydantic.BaseModel):
    faqs: list[FakeFAQ]

    def model_dump(self):
        return {"faqs": [f.model_dump() for f in self.faqs]}


# NOTE: these fakes are injected into the freshly-imported `main` module's
# namespace by _import_main() below — NOT into the shared
# sys.modules["step_function_types.*"] entries. Mutating the shared modules
# corrupts the real step_function_types package for every other test that
# runs in the same process (e.g. test_step_function_types.py, whose
# report_error would become a non-awaitable MagicMock). main.py looks these
# names up in its own globals at call time, so rebinding them on `main`
# after import gives us the mocks we want with zero cross-file pollution.
def _inject_fakes(main) -> None:
    main.UserQuery = MockUserQuery
    main.RAGDocument = MockRAGDocument
    main.FAQ = FakeFAQ
    main.FAQResource = FakeFAQResource
    main.ValidationError = Exception
    main.report_error = MagicMock()


def _converse_response_to_stream(response):
    """Convert a converse() response dict into a fake converse_stream() response.

    This allows existing test fixtures (written for converse()) to work
    with the new converse_stream() code path.
    """
    events = []
    message = response["output"]["message"]
    events.append({"messageStart": {"role": message.get("role", "assistant")}})

    for idx, block in enumerate(message.get("content", [])):
        if "text" in block:
            events.append({"contentBlockStart": {"contentBlockIndex": idx, "start": {}}})
            events.append({"contentBlockDelta": {"delta": {"text": block["text"]}}})
            events.append({"contentBlockStop": {"contentBlockIndex": idx}})
        elif "toolUse" in block:
            tool = block["toolUse"]
            events.append({
                "contentBlockStart": {
                    "contentBlockIndex": idx,
                    "start": {"toolUse": {"toolUseId": tool["toolUseId"], "name": tool["name"]}},
                }
            })
            input_json = json.dumps(tool["input"])
            events.append({"contentBlockDelta": {"delta": {"toolUse": {"input": input_json}}}})
            events.append({"contentBlockStop": {"contentBlockIndex": idx}})

    events.append({"messageStop": {"stopReason": response.get("stopReason", "")}})
    events.append({"metadata": {"usage": response.get("usage", {})}})
    return {"stream": iter(events)}


def _import_main():
    """Import main with all AWS deps mocked."""
    import importlib.util

    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            spec = importlib.util.spec_from_file_location(
                "main", os.path.join(os.path.dirname(__file__), "main.py")
            )
            main = importlib.util.module_from_spec(spec)
            sys.modules["main"] = main
            spec.loader.exec_module(main)
    _inject_fakes(main)
    return main


class TestProcessEvent:
    def test_flat_input(self):
        main = _import_main()
        event = {"query": "What is property tax?", "query_id": "q-1", "session_id": "s-1"}
        result = main.process_event(event)
        assert result.query == "What is property tax?"

    def test_rejects_malformed(self):
        main = _import_main()
        with pytest.raises(Exception):
            main.process_event({"bad": "data"})


class TestRunAgenticLoop:
    def _setup_main(self, monkeypatch):
        main = _import_main()

        # FAQ returns a low-scoring hit so we fall through to the loop.
        monkeypatch.setattr(main, "faq_search_direct", lambda q, n, e: {
            "faqs": [{"text": "Q: unrelated\nA: nope", "score": 0.2, "source_uri": "s3://f/faq_1.txt"}],
            "count": 1,
        })
        monkeypatch.setattr(main, "build_faq_resource", lambda results: None)
        monkeypatch.setattr(main, "build_cited_faq_resource", lambda results, cited: None)

        def fake_run(coro):
            coro.close()
        monkeypatch.setattr(main.asyncio, "run", fake_run)
        main.neptune.get_document = MagicMock(return_value=None)

        return main

    def test_emits_trace_sequence(self, monkeypatch):
        main = self._setup_main(monkeypatch)

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
                    {"toolUse": {"toolUseId": "t2", "name": "prepare_answer",
                                 "input": {"cited_doc_ids": ["doc-a"], "answer_plan": "Explain use value"}}},
                ]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        main.converse_with_cache = MagicMock(side_effect=responses)

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "vector_search":
                return {"chunks": [{"doc_id": "doc-a", "text": "..."}], "graph_context": {}}
            if name == "prepare_answer":
                return {"cited_doc_ids": input_.get("cited_doc_ids", []), "answer_plan": input_.get("answer_plan", "")}
            return {}
        monkeypatch.setattr(main, "execute_tool", fake_execute)

        sent = []
        mock_ws = MagicMock()
        def capture(msg):
            sent.append(msg)
            async def _noop():
                return None
            return _noop()
        mock_ws.send_json = capture

        result = main.run_agentic_loop(
            "what is use value?",
            query_id="q-1", session_id="s-1",
            ws_server=mock_ws, trace_seq=itertools.count(1).__next__,
        )

        kinds = [m.kind for m in sent]
        assert "reasoning" in kinds
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        assert kinds[-1] == "loop_complete"
        seqs = [m.seq for m in sent]
        assert seqs == sorted(seqs)
        assert seqs[0] == 1
        # The prepare_answer tool must NOT emit a tool_result trace.
        prepare_results = [m for m in sent if m.kind == "tool_result" and m.payload.get("toolName") == "prepare_answer"]
        assert prepare_results == []
        # Verify result is an AgentLoopResult with no fallback
        assert result.fallback_answer is None
        assert result.cited_doc_ids == ["doc-a"]

    def test_recovers_from_tool_exception(self, monkeypatch):
        main = self._setup_main(monkeypatch)

        responses = [
            {
                "output": {"message": {"content": [
                    {"toolUse": {"toolUseId": "t1", "name": "get_document", "input": {"node_id": "doc-a"}}},
                ]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
                "metrics": {"latencyMs": 100},
            },
            {
                "output": {"message": {"content": [
                    {"toolUse": {"toolUseId": "t2", "name": "prepare_answer",
                                 "input": {"cited_doc_ids": []}}},
                ]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        main.converse_with_cache = MagicMock(side_effect=responses)

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "get_document":
                raise KeyError("doc_id")
            if name == "prepare_answer":
                return {"cited_doc_ids": input_.get("cited_doc_ids", []), "answer_plan": ""}
            return {}
        monkeypatch.setattr(main, "execute_tool", fake_execute)

        mock_ws = MagicMock()
        def capture(msg):
            async def _noop():
                return None
            return _noop()
        mock_ws.send_json = capture

        result = main.run_agentic_loop(
            "what is use value?",
            query_id="q-1", session_id="s-1",
            ws_server=mock_ws, trace_seq=itertools.count(1).__next__,
        )
        assert result.fallback_answer is None
        assert result.cited_doc_ids == []
        assert main.converse_with_cache.call_count == 2

    def test_high_confidence_faq_continues_into_graph(self, monkeypatch):
        main = _import_main()

        monkeypatch.setattr(main, "faq_search_direct", lambda q, n, e: {
            "faqs": [{"text": "Q: what is TID\nA: tax incremental district", "score": 0.90, "source_uri": "s3://f/faq_1.txt"}],
            "count": 1,
        })
        monkeypatch.setattr(main, "build_faq_resource", lambda results: FakeFAQResource(
            faqs=[FakeFAQ(faq_id="faq_1", question="what is TID", answer="tax incremental district")]
        ))
        monkeypatch.setattr(main, "build_cited_faq_resource", lambda results, cited: None)

        def fake_run(coro):
            coro.close()
        monkeypatch.setattr(main.asyncio, "run", fake_run)
        main.neptune.get_document = MagicMock(return_value=None)

        responses = [
            {
                "output": {"message": {"content": [
                    {"toolUse": {"toolUseId": "t1", "name": "vector_search", "input": {"query": "TID"}}},
                ]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
                "metrics": {"latencyMs": 100},
            },
            {
                "output": {"message": {"content": [
                    {"toolUse": {"toolUseId": "t2", "name": "prepare_answer",
                                 "input": {"cited_doc_ids": ["doc-stat"], "answer_plan": "Explain TID"}}},
                ]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        main.converse_with_cache = MagicMock(side_effect=responses)

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "vector_search":
                return {"chunks": [{"doc_id": "doc-stat", "text": "..."}], "graph_context": {}}
            if name == "prepare_answer":
                return {"cited_doc_ids": input_.get("cited_doc_ids", []), "answer_plan": input_.get("answer_plan", "")}
            return {}
        monkeypatch.setattr(main, "execute_tool", fake_execute)

        sent = []
        mock_ws = MagicMock()
        def capture(msg):
            sent.append(msg)
            async def _noop():
                return None
            return _noop()
        mock_ws.send_json = capture

        result = main.run_agentic_loop(
            "what is TID?",
            query_id="q-1", session_id="s-1",
            ws_server=mock_ws, trace_seq=itertools.count(1).__next__,
        )

        assert main.converse_with_cache.call_count == 2
        # high_confidence_faq is stored on the result for the handler to use
        assert result.high_confidence_faq is not None
        assert result.high_confidence_faq.faqs[0].faq_id == "faq_1"
        kinds = [m.kind for m in sent]
        assert kinds[-1] == "loop_complete"
        assert sent[-1].payload["terminalReason"] == "prepare_answer"


class TestHandler:
    def _make_fallback_result(self):
        """Create a mock AgentLoopResult with a fallback answer."""
        from main import AgentLoopResult
        return AgentLoopResult(
            cited_doc_ids=[],
            all_chunks=[],
            all_doc_ids=set(),
            discovery={},
            fetched_opinions={},
            faq_resource=None,
            answer_plan="",
            trace_log=[],
            connection_alive=True,
            fallback_answer="ans",
            high_confidence_faq=None,
            faq_entries=[],
        )

    def test_attaches_ws_server(self, monkeypatch):
        main = _import_main()
        mock_ws = MagicMock()
        monkeypatch.setattr(main, "get_ws_connection_from_session", MagicMock(return_value=mock_ws))
        monkeypatch.setattr(main, "run_agentic_loop", MagicMock(return_value=self._make_fallback_result()))
        monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
        monkeypatch.setattr(main, "save_chat_history", lambda *a, **kw: None)
        monkeypatch.setattr(main, "build_rag_documents", lambda *a, **kw: [])
        monkeypatch.setattr(main, "build_cited_faq_resource", lambda *a, **kw: None)
        monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
            query="q", query_id="q-1", session_id="s-1"
        ))
        monkeypatch.setattr(main.asyncio, "run", lambda coro: coro.close())

        ctx = SimpleNamespace(aws_request_id="r-1")
        main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

        kwargs = main.run_agentic_loop.call_args.kwargs
        assert kwargs["ws_server"] is mock_ws
        assert callable(kwargs["trace_seq"])

    def test_runs_with_ws_none_on_session_lookup_failure(self, monkeypatch):
        main = _import_main()
        monkeypatch.setattr(main, "get_ws_connection_from_session", MagicMock(side_effect=RuntimeError("no session")))
        monkeypatch.setattr(main, "run_agentic_loop", MagicMock(return_value=self._make_fallback_result()))
        monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
        monkeypatch.setattr(main, "save_chat_history", lambda *a, **kw: None)
        monkeypatch.setattr(main, "build_rag_documents", lambda *a, **kw: [])
        monkeypatch.setattr(main, "build_cited_faq_resource", lambda *a, **kw: None)
        monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
            query="q", query_id="q-1", session_id="s-1"
        ))
        monkeypatch.setattr(main.asyncio, "run", lambda coro: coro.close())

        ctx = SimpleNamespace(aws_request_id="r-1")
        main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

        kwargs = main.run_agentic_loop.call_args.kwargs
        assert kwargs["ws_server"] is None
