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
