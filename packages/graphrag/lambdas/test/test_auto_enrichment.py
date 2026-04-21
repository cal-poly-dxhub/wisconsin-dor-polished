"""Tests for vector_search auto-enrichment.

After vector_search returns chunks, execute_tool auto-fetches neighbors
for the top-3 distinct parent doc_ids and folds them into the result.
This gives the agent graph context for free without an extra turn.
"""

from unittest.mock import MagicMock, patch


def test_vector_search_auto_enriches_top_parents():
    from tools import execute_tool

    mock_neptune = MagicMock()
    # 5 chunks from 4 distinct docs
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "text1", "doc_id": "doc-A", "score": 0.9},
        {"chunk_id": "c2", "text": "text2", "doc_id": "doc-A", "score": 0.85},
        {"chunk_id": "c3", "text": "text3", "doc_id": "doc-B", "score": 0.80},
        {"chunk_id": "c4", "text": "text4", "doc_id": "doc-C", "score": 0.75},
        {"chunk_id": "c5", "text": "text5", "doc_id": "doc-D", "score": 0.70},
    ]
    mock_neptune.get_neighbors.side_effect = lambda node_id, **kw: [
        {"relationship": "CITES", "id": f"{node_id}-cited", "title": "cited title"}
    ]

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    # Chunks present
    assert "chunks" in result
    assert len(result["chunks"]) == 5

    # Enrichment: top-3 distinct parents (A, B, C) got get_neighbors called
    called_ids = [call.args[0] for call in mock_neptune.get_neighbors.call_args_list]
    assert called_ids == ["doc-A", "doc-B", "doc-C"]

    # Enrichment payload present on the result
    assert "graph_context" in result
    assert "doc-A" in result["graph_context"]
    assert "doc-B" in result["graph_context"]
    assert "doc-C" in result["graph_context"]


def test_vector_search_no_enrichment_when_no_chunks():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    assert result["chunks"] == []
    assert result.get("graph_context", {}) == {}
    mock_neptune.get_neighbors.assert_not_called()


def test_vector_search_enrichment_swallows_neighbor_errors():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "text1", "doc_id": "doc-A", "score": 0.9},
    ]
    mock_neptune.get_neighbors.side_effect = RuntimeError("neptune down")

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    # Chunks still returned, enrichment absent but no crash
    assert len(result["chunks"]) == 1
    assert result.get("graph_context", {}) == {}
