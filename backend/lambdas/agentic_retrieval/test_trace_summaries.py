"""Tests for the trace_summaries module."""

from unittest.mock import MagicMock

import pytest

from trace_summaries import (
    build_tool_call_summary,
    build_tool_result_summary,
    discovery_summary,
    summarize_assistant_message,
    summarize_bedrock_response,
    summarize_tool_result,
)


class TestBuildToolCallSummary:
    def test_vector_search(self):
        assert build_tool_call_summary("vector_search", {"query": "ag use value"}) == '"ag use value"'

    def test_faq_search(self):
        assert build_tool_call_summary("faq_search", {"query": "what is TID"}) == '"what is TID"'

    def test_get_neighbors(self):
        assert build_tool_call_summary("get_neighbors", {"doc_id": "stat-70-32"}, None) == "stat-70-32"

    def test_get_document(self):
        assert build_tool_call_summary("get_document", {"doc_id": "doc-1"}) == "doc-1"

    def test_prepare_answer(self):
        assert build_tool_call_summary("prepare_answer", {"cited_doc_ids": ["a", "b", "c"]}, None) == "with 3 cited sources"

    def test_unknown_tool(self):
        assert build_tool_call_summary("mystery_tool", {"foo": "bar"}) == ""

    def test_empty_inputs(self):
        assert build_tool_call_summary("vector_search", {}, None) == ""
        assert build_tool_call_summary("faq_search", {"query": ""}, None) == ""
        assert build_tool_call_summary("get_neighbors", {"doc_id": ""}, None) == ""
        assert build_tool_call_summary("get_authority_chain", {}, None) == ""
        assert build_tool_call_summary("prepare_answer", {}, None) == "with 0 cited sources"
        assert build_tool_call_summary("prepare_answer", {"cited_doc_ids": None}, None) == "with 0 cited sources"


class TestBuildToolResultSummary:
    def _mock_neptune(self):
        mock = MagicMock()
        mock.get_document.return_value = None
        return mock

    def test_vector_search_ok(self):
        result = {
            "chunks": [
                {"doc_id": "doc-a", "text": "x", "score": 0.91},
                {"doc_id": "doc-a", "text": "y", "score": 0.85},
                {"doc_id": "doc-b", "text": "z", "score": 0.80},
            ],
            "graph_context": {"doc-a": [{"id": "doc-c"}]},
        }
        s = build_tool_result_summary("vector_search", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "3 chunks" in s["summary_text"]
        assert set(s["doc_ids"]) == {"doc-a", "doc-b"}
        assert s["metadata"]["chunkCount"] == 3
        assert s["metadata"]["docCount"] == 2
        assert s["metadata"]["autoEnrichedCount"] == 1
        assert s["metadata"]["topScore"] == pytest.approx(0.91)

    def test_get_neighbors(self):
        result = {"neighbors": [{"id": "d1", "relationship": "CITES"}, {"id": "d2", "relationship": "IMPLEMENTS"}]}
        s = build_tool_result_summary("get_neighbors", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "2 related" in s["summary_text"]
        assert set(s["doc_ids"]) == {"d1", "d2"}

    def test_faq_search_with_scores(self):
        result = {"faqs": [{"text": "Q: x\nA: y", "score": 0.84}, {"text": "Q: p\nA: q", "score": 0.71}], "count": 2}
        s = build_tool_result_summary("faq_search", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "0.84" in s["summary_text"]
        assert s["metadata"]["faqCount"] == 2

    def test_error_result(self):
        s = build_tool_result_summary("get_document", {"error": "not found", "fallback_matches": []}, self._mock_neptune())
        assert s["status"] == "error"
        assert "not found" in s["summary_text"]

    def test_prepare_answer_terminal(self):
        s = build_tool_result_summary("prepare_answer", {"cited_doc_ids": ["a", "b"], "answer_plan": "plan"}, self._mock_neptune())
        assert s["status"] == "terminal"
        assert "2 sources" in s["summary_text"]
        assert s["doc_ids"] == ["a", "b"]

    def test_fetch_opinion_miss(self):
        s = build_tool_result_summary("fetch_case_opinion", {"found": False, "citation": "123 Wis. 2d 45"}, self._mock_neptune())
        assert s["status"] == "miss"
        assert "123 Wis. 2d 45" in s["summary_text"]


class TestDiscoverySummary:
    def test_counts_tags(self):
        discovery = {"a": "vector-search", "b": "vector-search", "c": "fetched"}
        assert discovery_summary(discovery) == {"vector-search": 2, "fetched": 1}

    def test_empty(self):
        assert discovery_summary({}) == {}


class TestSummarizeAssistantMessage:
    def test_extracts_text_and_tools(self):
        message = {
            "content": [
                {"text": "thinking..."},
                {"toolUse": {"name": "vector_search", "toolUseId": "t1", "input": {}}},
            ]
        }
        s = summarize_assistant_message(message, 500)
        assert s["text_block_count"] == 1
        assert s["tool_use_count"] == 1
        assert s["tool_names"] == ["vector_search"]
        assert "thinking" in s["text_preview"]


class TestSummarizBedrockResponse:
    def test_extracts_usage(self):
        response = {
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 150},
        }
        s = summarize_bedrock_response(response)
        assert s["stop_reason"] == "tool_use"
        assert s["input_tokens"] == 10
        assert s["model_latency_ms"] == 150
