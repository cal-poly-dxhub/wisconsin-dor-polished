"""Tests for the trace_summaries module."""

from unittest.mock import MagicMock

import pytest
from tracing.summaries import (
    build_tool_call_summary,
    build_tool_result_summary,
    discovery_summary,
    summarize_assistant_message,
    summarize_bedrock_response,
)


class TestBuildToolCallSummary:
    def test_vector_search(self):
        assert (
            build_tool_call_summary("vector_search", {"query": "ag use value"}) == '"ag use value"'
        )

    def test_faq_search(self):
        assert build_tool_call_summary("faq_search", {"query": "what is TID"}) == '"what is TID"'

    def test_get_neighbors(self):
        assert (
            build_tool_call_summary("get_neighbors", {"doc_id": "stat-70-32"}, None) == "stat-70-32"
        )

    def test_get_document(self):
        assert build_tool_call_summary("get_document", {"doc_id": "doc-1"}) == "doc-1"

    def test_prepare_answer(self):
        assert (
            build_tool_call_summary("prepare_answer", {"cited_doc_ids": ["a", "b", "c"]}, None)
            == "with 3 cited sources"
        )

    def test_unknown_tool(self):
        assert build_tool_call_summary("mystery_tool", {"foo": "bar"}) == ""

    def test_empty_inputs(self):
        assert build_tool_call_summary("vector_search", {}, None) == ""
        assert build_tool_call_summary("faq_search", {"query": ""}, None) == ""
        assert build_tool_call_summary("get_neighbors", {"doc_id": ""}, None) == ""
        assert build_tool_call_summary("get_authority_chain", {}, None) == ""
        assert build_tool_call_summary("prepare_answer", {}, None) == "with 0 cited sources"
        assert (
            build_tool_call_summary("prepare_answer", {"cited_doc_ids": None}, None)
            == "with 0 cited sources"
        )


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
        result = {
            "neighbors": [
                {"id": "d1", "relationship": "CITES"},
                {"id": "d2", "relationship": "IMPLEMENTS"},
            ]
        }
        s = build_tool_result_summary("get_neighbors", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "2 related" in s["summary_text"]
        assert set(s["doc_ids"]) == {"d1", "d2"}

    def test_get_neighbors_ranked_emits_ten_results_and_ranking_data(self):
        neighbors = [
            {"id": f"case-{i}", "heading": f"Case {i}"}
            for i in range(1, 11)
        ]
        result = {
            "neighbors": neighbors,
            "query": "uniformity clause agricultural assessment",
            "top_k": 10,
            "total_cases": 27,
            "ranking_stats": {
                "chunkScores": [
                    {"chunkId": f"case-{i}", "cosine": 1 - i / 100}
                    for i in range(1, 11)
                ]
            },
        }

        s = build_tool_result_summary("get_neighbors", result, self._mock_neptune())
        metadata = s["metadata"]

        assert len(s["doc_ids"]) == 10
        assert len(metadata["neighborEdges"]) == 10
        assert metadata["neighborEdges"][0] == {
            "id": "case-1",
            "title": "Case 1",
            "relationship": "SEMANTIC_MATCH",
            "rank": 1,
            "score": 0.99,
        }
        assert metadata["ranked"] is True
        assert metadata["query"] == "uniformity clause agricultural assessment"
        assert metadata["topK"] == 10
        assert metadata["totalCandidates"] == 27

    def test_faq_search_with_scores(self):
        result = {
            "faqs": [{"text": "Q: x\nA: y", "score": 0.84}, {"text": "Q: p\nA: q", "score": 0.71}],
            "count": 2,
        }
        s = build_tool_result_summary("faq_search", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "0.84" in s["summary_text"]
        assert s["metadata"]["faqCount"] == 2

    def test_error_result(self):
        s = build_tool_result_summary(
            "get_document", {"error": "not found", "fallback_matches": []}, self._mock_neptune()
        )
        assert s["status"] == "error"
        assert "not found" in s["summary_text"]

    def test_prepare_answer_terminal(self):
        s = build_tool_result_summary(
            "prepare_answer",
            {"cited_doc_ids": ["a", "b"], "answer_plan": "plan"},
            self._mock_neptune(),
        )
        assert s["status"] == "terminal"
        assert "2 sources" in s["summary_text"]
        assert s["doc_ids"] == ["a", "b"]

    def test_get_section_unranked(self):
        """No-query get_section: counts always present, no ranking fields."""
        result = {
            "chunks": [
                {"chunk_id": "c1", "doc_id": "statutes-79"},
                {"chunk_id": "c2", "doc_id": "statutes-79"},
            ],
            "doc_id": "statutes-79",
            "heading": "79.036 County and municipal aid; beginning in 2024.",
        }
        s = build_tool_result_summary("get_section", result, self._mock_neptune())
        assert s["status"] == "ok"
        assert "2 chunks" in s["summary_text"]
        m = s["metadata"]
        assert m["chunkCount"] == 2
        assert m["filtered"] is False
        assert m["sectionChunkCount"] == 2
        assert m["returnedChunkCount"] == 2
        assert m["chunkIds"] == ["c1", "c2"]
        assert "query" not in m
        assert "chunkScores" not in m

    def test_get_section_ranked(self):
        """Query-ranked get_section: ranking stats and query pass through."""
        result = {
            "chunks": [{"chunk_id": "c1", "doc_id": "statutes-79"}],
            "doc_id": "statutes-79",
            "heading": "79.05 Expenditure restraint incentive program.",
            "query": "expenditure restraint program sales tax revenues calculation",
            "ranking_stats": {
                "sectionChunkCount": 7,
                "returnedChunkCount": 1,
                "mean": 0.7823,
                "std": 0.0941,
                "zThreshold": 0.5,
                "flatDistribution": False,
                "chunkScores": [
                    {
                        "chunkId": "c1",
                        "cosine": 0.8912,
                        "zScore": 1.16,
                        "heading": "79.05",
                        "included": True,
                    },
                    {
                        "chunkId": "c2",
                        "cosine": 0.6990,
                        "zScore": -0.88,
                        "heading": "79.05",
                        "included": False,
                    },
                ],
            },
        }
        s = build_tool_result_summary("get_section", result, self._mock_neptune())
        assert s["status"] == "ok"
        m = s["metadata"]
        assert m["filtered"] is True
        assert m["query"] == "expenditure restraint program sales tax revenues calculation"
        assert m["chunkCount"] == 1
        assert m["sectionChunkCount"] == 7
        assert m["returnedChunkCount"] == 1
        assert len(m["chunkScores"]) == 2
        assert m["zThreshold"] == 0.5

    def test_fetch_opinion_miss(self):
        s = build_tool_result_summary(
            "fetch_case_opinion",
            {"found": False, "citation": "123 Wis. 2d 45"},
            self._mock_neptune(),
        )
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
