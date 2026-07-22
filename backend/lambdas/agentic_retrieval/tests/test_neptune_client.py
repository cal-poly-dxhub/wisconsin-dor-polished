"""Unit tests for NeptuneClient (mocked boto3)."""

from unittest.mock import MagicMock, patch


def test_query_returns_results():
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": [{"id": "doc-1", "title": "Test"}]}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        results = client.query("MATCH (n) RETURN n.id AS id, n.title AS title")

        assert len(results) == 1
        assert results[0]["id"] == "doc-1"
        mock_neptune.execute_query.assert_called_once()


def test_get_document_returns_none_when_not_found():
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.get_document("nonexistent")

        assert result is None


def test_vector_search_passes_embedding():
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [{"chunk_id": "c1", "text": "test", "score": 0.95}]
        }

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        results = client.vector_search([0.1] * 1024, top_k=5)

        assert len(results) == 1
        assert results[0]["score"] == 0.95


def test_get_neighbors_unfiltered_excludes_extracted_from():
    """When edge_types is None, the Cypher must exclude EXTRACTED_FROM edges
    and include a LIMIT clause."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbors("doc-1")

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = (
                mock_neptune.execute_query.call_args[0][0]
                if mock_neptune.execute_query.call_args[0]
                else ""
            )
        # Check the WHERE clause and LIMIT are present
        assert "EXTRACTED_FROM" in query_arg
        assert "LIMIT" in query_arg


def test_get_neighbors_with_edge_types_no_extracted_from_clause():
    """When edge_types is provided, no EXTRACTED_FROM WHERE clause is needed
    (the type filter already excludes it), but LIMIT should still be present."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbors("doc-1", edge_types=["CITES", "IMPLEMENTS"])

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = (
                mock_neptune.execute_query.call_args[0][0]
                if mock_neptune.execute_query.call_args[0]
                else ""
            )
        # type filter should include the specified types
        assert "CITES|IMPLEMENTS" in query_arg
        # No WHERE exclusion needed — type filter handles it
        assert "WHERE type(r) <> 'EXTRACTED_FROM'" not in query_arg
        # LIMIT should still be present
        assert "LIMIT" in query_arg


def test_resolve_case_citations():
    """resolve_case_citations passes citations list as parameter."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [
                {"id": "case-law-45-wis-2d-683", "title": "Markarian", "citation": "45 Wis. 2d 683"}
            ]
        }

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        results = client.resolve_case_citations(["45 Wis. 2d 683", "173 N.W.2d 627"])

        assert len(results) == 1
        assert results[0]["id"] == "case-law-45-wis-2d-683"
        call_kwargs = mock_neptune.execute_query.call_args.kwargs or {}
        params = call_kwargs.get("parameters", {})
        assert params.get("citations") == ["45 Wis. 2d 683", "173 N.W.2d 627"]


def test_find_case_law_with_statute_scope():
    """find_case_law with statute_id scopes to that statute's CITES edges."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.find_case_law("Markarian", statute_id="WIS-STAT-70.32")

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = (
                mock_neptune.execute_query.call_args[0][0]
                if mock_neptune.execute_query.call_args[0]
                else ""
            )
        assert "$statute_id" in query_arg
        assert "CITES" in query_arg
        assert "toLower(n.title) CONTAINS $term_0" in query_arg
        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        assert params.get("term_0") == "markarian"
        assert params.get("statute_id") == "WIS-STAT-70.32"


def test_find_case_law_splits_multi_word_search():
    """find_case_law splits 'Markarian v City of Cudahy' into significant terms."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.find_case_law("Markarian v City of Cudahy")

        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        # Collect all term_N values
        term_values = [v for k, v in params.items() if k.startswith("term_")]
        assert "markarian" in term_values
        assert "cudahy" in term_values
        assert "city" in term_values
        # Stop words filtered out
        assert "v" not in term_values
        assert "of" not in term_values


def test_get_neighbors_title_filter():
    """title_filter adds a WHERE clause to scope neighbors by title."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbors("WIS-STAT-70.11", edge_types=["CITES"], title_filter="hospital")

        query_arg = mock_neptune.execute_query.call_args.kwargs.get(
            "queryString"
        ) or mock_neptune.execute_query.call_args[1].get("queryString")
        if query_arg is None:
            query_arg = (
                mock_neptune.execute_query.call_args[0][0]
                if mock_neptune.execute_query.call_args[0]
                else ""
            )
        assert "toLower($title_filter)" in query_arg
        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        assert params.get("title_filter") == "hospital"


def test_get_chunk_statute_ids():
    """get_chunk_statute_ids returns distinct statute IDs from chunk CITES edges."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [
                {"statute_id": "WIS-STAT-70.32"},
                {"statute_id": "WIS-STAT-70.34"},
            ]
        }

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.get_chunk_statute_ids(["chunk-1", "chunk-2"])

        assert result == ["WIS-STAT-70.32", "WIS-STAT-70.34"]
        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        assert params.get("chunk_ids") == ["chunk-1", "chunk-2"]


def test_get_chunk_statute_ids_empty_input():
    """get_chunk_statute_ids returns empty list for empty input."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.get_chunk_statute_ids([])

        assert result == []
        mock_neptune.execute_query.assert_not_called()


def test_rank_neighbors_by_shared_statutes():
    """rank_neighbors_by_shared_statutes returns doc IDs ordered by overlap."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [
                {"doc_id": "gov_publications-ag-guide", "shared_statutes": 2},
                {"doc_id": "news_pages-assessor-2022", "shared_statutes": 1},
            ]
        }

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.rank_neighbors_by_shared_statutes(
            ["gov_publications-ag-guide", "news_pages-assessor-2022"],
            ["WIS-STAT-70.32", "WIS-STAT-18.05"],
            limit=3,
        )

        assert result == ["gov_publications-ag-guide", "news_pages-assessor-2022"]
        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        assert params.get("doc_ids") == ["gov_publications-ag-guide", "news_pages-assessor-2022"]
        assert params.get("statute_ids") == ["WIS-STAT-70.32", "WIS-STAT-18.05"]


def test_rank_neighbors_by_shared_statutes_empty_inputs():
    """rank_neighbors_by_shared_statutes returns empty for empty inputs."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        assert client.rank_neighbors_by_shared_statutes([], ["WIS-STAT-70.32"]) == []
        assert client.rank_neighbors_by_shared_statutes(["doc-1"], []) == []
        mock_neptune.execute_query.assert_not_called()


def test_get_chunks_text_for_docs():
    """get_chunks_text_for_docs returns flat list of text strings."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {
            "results": [
                {"text": "Agricultural classification per 2019 WI 23."},
                {"text": "See also 2018 WI 45 regarding use value."},
            ]
        }

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        result = client.get_chunks_text_for_docs(["gov_publications-ag-guide"])

        assert len(result) == 2
        assert "2019 WI 23" in result[0]
        params = mock_neptune.execute_query.call_args.kwargs.get("parameters", {})
        assert params.get("doc_ids") == ["gov_publications-ag-guide"]


def test_get_chunks_text_for_docs_empty():
    """get_chunks_text_for_docs returns empty for empty input."""
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        assert client.get_chunks_text_for_docs([]) == []
        mock_neptune.execute_query.assert_not_called()


def test_case_chunks_for_statutes_uses_one_incoming_cites_batch():
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_case_chunks_for_statutes_with_embeddings(
            ["WIS-STAT-70.47", "WIS-STAT-70.32", "WIS-STAT-70.32"], limit=200
        )

        kwargs = mock_neptune.execute_query.call_args.kwargs
        query = kwargs["queryString"]
        parameters = kwargs["parameters"]
        assert "UNWIND $statute_ids AS sid" in query
        assert "<-[:CITES]-(c:Chunk)" in query
        assert "LIMIT 200" in query
        assert query.count("neptune.algo.vectors.get") == 1
        assert parameters["statute_ids"] == ["WIS-STAT-70.32", "WIS-STAT-70.47"]


def test_neighbor_case_summaries_filters_multi_chunk_cases_to_summary():
    with patch("graph.neptune_client.boto3") as mock_boto3:
        mock_neptune = MagicMock()
        mock_boto3.client.return_value = mock_neptune
        mock_neptune.execute_query.return_value = {"results": []}

        from graph.neptune_client import NeptuneClient

        client = NeptuneClient(graph_id="test-graph")
        client.get_neighbor_case_summaries_with_embeddings("WIS-STAT-70.32")

        query = mock_neptune.execute_query.call_args.kwargs["queryString"]
        assert "c.content_role = 'summary_holding'" in query
        assert "c.heading IN ['Holding', 'Holding summary']" in query
