"""Unit tests for agentic retrieval tools."""

from unittest.mock import MagicMock, patch


def test_execute_tool_vector_search():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test chunk", "score": 0.9}
    ]

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test query"}, mock_neptune)

    assert "chunks" in result
    assert len(result["chunks"]) == 1
    mock_neptune.vector_search.assert_called_once()


def test_execute_tool_get_document_found():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = {"id": "doc-1", "title": "Test Doc"}

    result = execute_tool("get_document", {"doc_id": "doc-1"}, mock_neptune)

    assert "document" in result
    assert result["document"]["id"] == "doc-1"


def test_execute_tool_get_document_not_found():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None

    result = execute_tool("get_document", {"doc_id": "missing"}, mock_neptune)

    assert "error" in result


def test_execute_tool_answer_is_terminal():
    from tools import execute_tool

    mock_neptune = MagicMock()
    input_data = {"response": "The answer is...", "cited_doc_ids": ["doc-1"]}

    result = execute_tool("answer", input_data, mock_neptune)

    assert result["response"] == "The answer is..."
    assert result["cited_doc_ids"] == ["doc-1"]


def test_execute_tool_unknown_tool():
    from tools import execute_tool

    mock_neptune = MagicMock()
    result = execute_tool("nonexistent", {}, mock_neptune)

    assert "error" in result


def test_execute_tool_fetch_case_opinion_success():
    from tools import execute_tool

    mock_neptune = MagicMock()

    with patch("tools.fetch_case_opinion") as mock_fetch, \
         patch("tools.RAW_BUCKET", "test-bucket"):
        mock_fetch.return_value = {
            "found": True,
            "citation": "109 Wis. 2d 290",
            "text": "CORROON v. HOSCH opinion body",
            "scholar_url": "http://scholar.google.com/scholar?q=109+Wis+2d+290",
        }
        result = execute_tool(
            "fetch_case_opinion",
            {"citation": "109 Wis. 2d 290"},
            mock_neptune,
        )

    assert result["found"] is True
    assert "CORROON" in result["text"]
    mock_fetch.assert_called_once()


def test_fetch_case_opinion_tool_in_definitions():
    from tools import TOOL_DEFINITIONS

    names = {t["toolSpec"]["name"] for t in TOOL_DEFINITIONS}
    assert "fetch_case_opinion" in names


def test_execute_tool_fetch_case_opinion_no_bucket():
    from tools import execute_tool

    mock_neptune = MagicMock()

    with patch("tools.RAW_BUCKET", ""):
        result = execute_tool(
            "fetch_case_opinion",
            {"citation": "109 Wis. 2d 290"},
            mock_neptune,
        )

    assert "error" in result


def test_get_document_falls_back_to_vector_search_on_not_found():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "match", "doc_id": "real-doc-id", "score": 0.8},
    ]

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "typo-or-format-mismatch"}, mock_neptune
        )

    # Fallback kicked in; returns a suggestion result, not a bare error
    assert "fallback_matches" in result
    assert len(result["fallback_matches"]) == 1
    assert result["fallback_matches"][0]["doc_id"] == "real-doc-id"
    # Original error context still present
    assert result.get("error", "").startswith("Document")


def test_get_document_no_fallback_matches_returns_error():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "nonsense"}, mock_neptune
        )

    assert "error" in result
    assert result.get("fallback_matches", []) == []
