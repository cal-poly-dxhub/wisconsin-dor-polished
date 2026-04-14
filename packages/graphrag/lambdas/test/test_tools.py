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
