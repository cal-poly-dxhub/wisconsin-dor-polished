"""Unit tests for NeptuneClient (mocked boto3)."""

from unittest.mock import MagicMock, patch


def test_query_returns_results():
    with patch("neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": [{"id": "doc-1", "title": "Test"}]}

        from neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        results = client.query("MATCH (n) RETURN n.id AS id, n.title AS title")

        assert len(results) == 1
        assert results[0]["id"] == "doc-1"
        mock_neptune.execute_query.assert_called_once()


def test_get_document_returns_none_when_not_found():
    with patch("neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.get_document("nonexistent")

        assert result is None


def test_vector_search_passes_embedding():
    with patch("neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [{"chunk_id": "c1", "text": "test", "score": 0.95}]
        }

        from neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        results = client.vector_search([0.1] * 1024, top_k=5)

        assert len(results) == 1
        assert results[0]["score"] == 0.95


def test_get_neighbors_unfiltered_excludes_extracted_from():
    """When edge_types is None, the Cypher must exclude EXTRACTED_FROM edges
    and include a LIMIT clause."""
    with patch("neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbors("doc-1")

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = mock_neptune.execute_query.call_args[0][0] if mock_neptune.execute_query.call_args[0] else ""
        # Check the WHERE clause and LIMIT are present
        assert "EXTRACTED_FROM" in query_arg
        assert "LIMIT" in query_arg


def test_get_neighbors_with_edge_types_no_extracted_from_clause():
    """When edge_types is provided, no EXTRACTED_FROM WHERE clause is needed
    (the type filter already excludes it), but LIMIT should still be present."""
    with patch("neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbors("doc-1", edge_types=["CITES", "IMPLEMENTS"])

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = mock_neptune.execute_query.call_args[0][0] if mock_neptune.execute_query.call_args[0] else ""
        # type filter should include the specified types
        assert "CITES|IMPLEMENTS" in query_arg
        # No WHERE exclusion needed — type filter handles it
        assert "WHERE type(r) <> 'EXTRACTED_FROM'" not in query_arg
        # LIMIT should still be present
        assert "LIMIT" in query_arg
