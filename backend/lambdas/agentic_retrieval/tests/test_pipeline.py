"""Tests for agent_tools/pipeline.py: stage composition and trace shape.

execute_tool's vector_search branch delegates to run_vector_search — the
behavioral tests for the actual retrieval logic live in test_tools.py,
test_statute_backfill.py, and test_auto_enrichment.py. These tests instead
verify pipeline.py's own contract: stage ordering, that the runner emits a
standard-shaped trace event for every logged stage, and that the final
result dict matches the documented shape.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _passthrough_auto_refine(monkeypatch):
    monkeypatch.setattr(
        "agent_tools.executor._auto_refine",
        lambda query, history: (query, None),
    )


def test_vector_search_stages_order_matches_original_monolith():
    from agent_tools import pipeline

    names = [stage.__name__.rsplit(".", 1)[-1] for stage in pipeline.VECTOR_SEARCH_STAGES]

    assert names == [
        "auto_refine",
        "neptune_search",
        "wpam_dedup",
        "diversity_cap",
        "authority_tiebreak",
        "auto_enrichment",
        "citation_extraction",
        "statute_backfill",
        "caselaw_backfill",
        "broad_discovery",
    ]


def test_each_stage_module_exposes_a_run_callable():
    from agent_tools import pipeline

    for stage in pipeline.VECTOR_SEARCH_STAGES:
        assert callable(stage.run)


def test_run_vector_search_returns_documented_result_shape():
    from agent_tools import pipeline

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test chunk", "score": 0.9, "doc_id": "doc-1"},
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = pipeline.run_vector_search(
            query="test query",
            neptune=mock_neptune,
            chat_history=None,
            original_user_query=None,
            top_k=15,
        )

    # Documented always-present keys (see run_vector_search's result dict).
    assert {
        "chunks",
        "pre_dedup_count",
        "refined_query",
        "top_k",
        "diversity_cap_per_doc",
    }.issubset(result.keys())
    assert result["top_k"] == 15
    assert len(result["chunks"]) == 1
    # original_user_query was None -> broad_query/broad_skipped absent.
    assert "broad_query" not in result
    assert "broad_skipped" not in result


def test_run_vector_search_emits_stage_complete_trace_events(caplog):
    import logging

    from agent_tools import pipeline

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test chunk", "score": 0.9, "doc_id": "doc-1"},
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    caplog.set_level(logging.INFO, logger="agent_tools.executor")
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        pipeline.run_vector_search(
            query="test query",
            neptune=mock_neptune,
            chat_history=None,
            original_user_query=None,
            top_k=15,
        )

    events = [rec.message for rec in caplog.records]
    # authority_tiebreak's trace and the final vector_search_complete summary
    # must both be present as standard-shaped log lines.
    assert any('"event":"vector_search_neptune_complete"' in e for e in events)
    assert any('"event":"vector_search_complete"' in e for e in events)
    # All emitted trace events share the same component tag.
    assert all(
        '"component":"graphrag.agentic_retrieval.tools"' in e for e in events if '"event":' in e
    )


def test_run_vector_search_top_k_respected_in_chunk_count():
    from agent_tools import pipeline

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": f"c{i}", "text": "t", "score": 1.0 - i * 0.01, "doc_id": f"doc-{i}"}
        for i in range(30)
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = pipeline.run_vector_search(
            query="test query",
            neptune=mock_neptune,
            chat_history=None,
            original_user_query=None,
            top_k=5,
        )

    assert len(result["chunks"]) <= 5


def test_run_vector_search_original_user_query_adds_broad_fields():
    from agent_tools import pipeline

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "t", "score": 0.9, "doc_id": "doc-1"},
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = pipeline.run_vector_search(
            query="refined query text",
            neptune=mock_neptune,
            chat_history=None,
            original_user_query="original user question?",
            top_k=15,
        )

    assert "broad_query" in result
    assert "broad_skipped" in result
