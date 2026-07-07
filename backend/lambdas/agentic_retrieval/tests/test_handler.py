"""Integration tests for handler.py and loop.phase_a.run_agentic_loop."""

import itertools
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pydantic = pytest.importorskip("pydantic")


class FakeFAQ(pydantic.BaseModel):
    faq_id: str
    question: str
    answer: str
    source_url: str | None = None


class FakeFAQResource(pydantic.BaseModel):
    faqs: list[FakeFAQ]


class TestProcessEvent:
    def test_flat_input(self, fresh_modules):
        (handler,) = fresh_modules("handler")
        event = {"query": "What is property tax?", "query_id": "q-1", "session_id": "s-1"}
        result = handler.process_event(event)
        assert result.query == "What is property tax?"

    def test_rejects_malformed(self, fresh_modules):
        (handler,) = fresh_modules("handler")
        with pytest.raises(Exception):  # noqa: B017
            handler.process_event({"bad": "data"})


class TestRunAgenticLoop:
    def _setup_phase_a(self, fresh_modules, monkeypatch):
        (phase_a,) = fresh_modules("loop.phase_a")

        # FAQ returns a low-scoring hit so we fall through to the loop.
        monkeypatch.setattr(
            phase_a,
            "faq_search_direct",
            lambda q, n, e: {
                "faqs": [
                    {
                        "text": "Q: unrelated\nA: nope",
                        "score": 0.2,
                        "source_uri": "s3://f/faq_1.txt",
                    }
                ],
                "count": 1,
            },
        )
        monkeypatch.setattr(phase_a, "build_faq_resource", lambda results: None)
        phase_a.neptune.get_document = MagicMock(return_value=None)

        return phase_a

    def test_emits_trace_sequence(self, fresh_modules, monkeypatch):
        phase_a = self._setup_phase_a(fresh_modules, monkeypatch)

        responses = [
            {
                "output": {
                    "message": {
                        "content": [
                            {"text": "I'll search the graph."},
                            {
                                "toolUse": {
                                    "toolUseId": "t1",
                                    "name": "vector_search",
                                    "input": {"query": "use value"},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
                "metrics": {"latencyMs": 100},
            },
            {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "t2",
                                    "name": "prepare_answer",
                                    "input": {
                                        "cited_doc_ids": ["doc-a"],
                                        "answer_plan": "Explain use value",
                                    },
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        monkeypatch.setattr(phase_a, "converse_with_cache", MagicMock(side_effect=responses))

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "vector_search":
                return {"chunks": [{"doc_id": "doc-a", "text": "..."}], "graph_context": {}}
            if name == "prepare_answer":
                return {
                    "cited_doc_ids": input_.get("cited_doc_ids", []),
                    "answer_plan": input_.get("answer_plan", ""),
                }
            return {}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)

        sent = []
        mock_ws = MagicMock()

        def capture(msg):
            sent.append(msg)

            async def _noop():
                return None

            return _noop()

        mock_ws.send_json = capture

        result = phase_a.run_agentic_loop(
            "what is use value?",
            query_id="q-1",
            session_id="s-1",
            ws_server=mock_ws,
            trace_seq=itertools.count(1).__next__,
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
        prepare_results = [
            m
            for m in sent
            if m.kind == "tool_result" and m.payload.get("toolName") == "prepare_answer"
        ]
        assert prepare_results == []
        # Verify result is an AgentLoopResult with no fallback
        assert result.fallback_answer is None
        assert result.cited_doc_ids == ["doc-a"]

    def test_recovers_from_tool_exception(self, fresh_modules, monkeypatch):
        phase_a = self._setup_phase_a(fresh_modules, monkeypatch)

        responses = [
            {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "t1",
                                    "name": "get_document",
                                    "input": {"node_id": "doc-a"},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
                "metrics": {"latencyMs": 100},
            },
            {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "t2",
                                    "name": "prepare_answer",
                                    "input": {"cited_doc_ids": []},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        mock_converse = MagicMock(side_effect=responses)
        monkeypatch.setattr(phase_a, "converse_with_cache", mock_converse)

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "get_document":
                raise KeyError("doc_id")
            if name == "prepare_answer":
                return {"cited_doc_ids": input_.get("cited_doc_ids", []), "answer_plan": ""}
            return {}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)

        mock_ws = MagicMock()

        def capture(msg):
            async def _noop():
                return None

            return _noop()

        mock_ws.send_json = capture

        result = phase_a.run_agentic_loop(
            "what is use value?",
            query_id="q-1",
            session_id="s-1",
            ws_server=mock_ws,
            trace_seq=itertools.count(1).__next__,
        )
        assert result.fallback_answer is None
        assert result.cited_doc_ids == []
        assert mock_converse.call_count == 2

    def test_high_confidence_faq_continues_into_graph(self, fresh_modules, monkeypatch):
        (phase_a,) = fresh_modules("loop.phase_a")

        monkeypatch.setattr(
            phase_a,
            "faq_search_direct",
            lambda q, n, e: {
                "faqs": [
                    {
                        "text": "Q: what is TID\nA: tax incremental district",
                        "score": 0.90,
                        "source_uri": "s3://f/faq_1.txt",
                    }
                ],
                "count": 1,
            },
        )
        monkeypatch.setattr(
            phase_a,
            "build_faq_resource",
            lambda results: FakeFAQResource(
                faqs=[
                    FakeFAQ(
                        faq_id="faq_1", question="what is TID", answer="tax incremental district"
                    )
                ]
            ),
        )
        phase_a.neptune.get_document = MagicMock(return_value=None)

        responses = [
            {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "t1",
                                    "name": "vector_search",
                                    "input": {"query": "TID"},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
                "metrics": {"latencyMs": 100},
            },
            {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "t2",
                                    "name": "prepare_answer",
                                    "input": {
                                        "cited_doc_ids": ["doc-stat"],
                                        "answer_plan": "Explain TID",
                                    },
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
                "metrics": {"latencyMs": 120},
            },
        ]
        mock_converse = MagicMock(side_effect=responses)
        monkeypatch.setattr(phase_a, "converse_with_cache", mock_converse)

        def fake_execute(name, input_, neptune_client, chat_history=None):
            if name == "vector_search":
                return {"chunks": [{"doc_id": "doc-stat", "text": "..."}], "graph_context": {}}
            if name == "prepare_answer":
                return {
                    "cited_doc_ids": input_.get("cited_doc_ids", []),
                    "answer_plan": input_.get("answer_plan", ""),
                }
            return {}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)

        sent = []
        mock_ws = MagicMock()

        def capture(msg):
            sent.append(msg)

            async def _noop():
                return None

            return _noop()

        mock_ws.send_json = capture

        result = phase_a.run_agentic_loop(
            "what is TID?",
            query_id="q-1",
            session_id="s-1",
            ws_server=mock_ws,
            trace_seq=itertools.count(1).__next__,
        )

        assert mock_converse.call_count == 2
        # high_confidence_faq is stored on the result for the handler to use
        assert result.high_confidence_faq is not None
        assert result.high_confidence_faq.faqs[0].faq_id == "faq_1"
        kinds = [m.kind for m in sent]
        assert kinds[-1] == "loop_complete"
        assert sent[-1].payload["terminalReason"] == "prepare_answer"


class TestHandler:
    def _make_fallback_result(self, phase_a):
        """Create a mock AgentLoopResult with a fallback answer."""
        return phase_a.AgentLoopResult(
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

    def _setup_handler(self, fresh_modules, monkeypatch):
        handler, phase_a = fresh_modules("handler", "loop.phase_a")
        monkeypatch.setattr(
            handler, "run_agentic_loop", MagicMock(return_value=self._make_fallback_result(phase_a))
        )
        monkeypatch.setattr(handler, "get_chat_history", lambda sid: [])
        monkeypatch.setattr(handler, "save_chat_history", lambda *a, **kw: None)
        monkeypatch.setattr(handler, "build_rag_documents", lambda *a, **kw: [])
        monkeypatch.setattr(handler, "build_cited_faq_resource", lambda *a, **kw: None)
        monkeypatch.setattr(
            handler,
            "process_event",
            lambda e: SimpleNamespace(query="q", query_id="q-1", session_id="s-1", persona=None),
        )
        monkeypatch.setattr(handler.asyncio, "run", lambda coro: coro.close())
        return handler

    def test_attaches_ws_server(self, fresh_modules, monkeypatch):
        handler = self._setup_handler(fresh_modules, monkeypatch)
        mock_ws = MagicMock()
        monkeypatch.setattr(
            handler, "get_ws_connection_from_session", MagicMock(return_value=mock_ws)
        )

        ctx = SimpleNamespace(aws_request_id="r-1")
        handler.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

        kwargs = handler.run_agentic_loop.call_args.kwargs
        assert kwargs["ws_server"] is mock_ws
        assert callable(kwargs["trace_seq"])

    def test_runs_with_ws_none_on_session_lookup_failure(self, fresh_modules, monkeypatch):
        handler = self._setup_handler(fresh_modules, monkeypatch)
        monkeypatch.setattr(
            handler,
            "get_ws_connection_from_session",
            MagicMock(side_effect=RuntimeError("no session")),
        )

        ctx = SimpleNamespace(aws_request_id="r-1")
        handler.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

        kwargs = handler.run_agentic_loop.call_args.kwargs
        assert kwargs["ws_server"] is None
