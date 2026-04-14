"""Unit tests for agentic retrieval handler."""

from unittest.mock import MagicMock, patch


def test_process_event_flat_input():
    from main import process_event

    event = {"query": "What is property tax?", "query_id": "q-1", "session_id": "s-1"}
    result = process_event(event)

    assert result.query == "What is property tax?"
    assert result.query_id == "q-1"
    assert result.session_id == "s-1"


def test_process_event_rejects_malformed():
    from main import process_event
    import pytest

    with pytest.raises(Exception):
        process_event({"bad": "data"})


def test_build_rag_documents():
    with patch("main.neptune") as mock_neptune:
        mock_neptune.get_document.return_value = {"title": "Test Doc", "id": "doc-1"}

        from main import _build_rag_documents

        chunks = [
            {"doc_id": "doc-1", "text": "chunk 1 text", "source_url": "http://example.com"},
            {"doc_id": "doc-1", "text": "chunk 2 text", "source_url": "http://example.com"},
        ]

        docs = _build_rag_documents(chunks, {"doc-1"})

        assert len(docs) == 1
        assert "chunk 1 text" in docs[0].content
        assert "chunk 2 text" in docs[0].content
