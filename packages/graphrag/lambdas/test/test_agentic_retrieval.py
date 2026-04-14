"""Unit tests for agentic retrieval handler.

These tests mock the Lambda layer dependencies (step_function_types, websocket_utils)
since they are only available at deploy time via Lambda layers.
"""

import sys
import os
from unittest.mock import MagicMock, patch

# Mock Lambda layer dependencies before importing main
sys.modules["step_function_types"] = MagicMock()
sys.modules["step_function_types.errors"] = MagicMock()
sys.modules["step_function_types.models"] = MagicMock()
sys.modules["websocket_utils"] = MagicMock()
sys.modules["websocket_utils.models"] = MagicMock()
sys.modules["websocket_utils.utils"] = MagicMock()

# Set up realistic mock models
from unittest.mock import PropertyMock
from types import SimpleNamespace
import pydantic


class MockUserQuery(pydantic.BaseModel):
    query: str
    query_id: str
    session_id: str


class MockRAGDocument(pydantic.BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None


# Patch the models module to provide real Pydantic models
models_mock = sys.modules["step_function_types.models"]
models_mock.UserQuery = MockUserQuery
models_mock.RAGDocument = MockRAGDocument
models_mock.DocumentResource = MagicMock()
models_mock.GenerateResponseJob = MagicMock()
models_mock.RetrieveResult = MagicMock()
models_mock.StreamResourcesJob = MagicMock()

errors_mock = sys.modules["step_function_types.errors"]
errors_mock.ValidationError = Exception
errors_mock.report_error = MagicMock()


def test_process_event_flat_input():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import process_event

    event = {"query": "What is property tax?", "query_id": "q-1", "session_id": "s-1"}
    result = process_event(event)

    assert result.query == "What is property tax?"
    assert result.query_id == "q-1"
    assert result.session_id == "s-1"


def test_process_event_rejects_malformed():
    import pytest

    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import process_event

    with pytest.raises(Exception):
        process_event({"bad": "data"})


def test_build_rag_documents():
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()
        mock_instance.get_document.return_value = {"title": "Test Doc", "id": "doc-1"}
        MockNeptune.return_value = mock_instance

        # Re-import to get fresh module with mocked neptune
        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_rag_documents

            chunks = [
                {"doc_id": "doc-1", "text": "chunk 1 text", "source_url": "http://example.com"},
                {"doc_id": "doc-1", "text": "chunk 2 text", "source_url": "http://example.com"},
            ]

            docs = _build_rag_documents(chunks, {"doc-1"})

            assert len(docs) == 1
            assert "chunk 1 text" in docs[0].content
            assert "chunk 2 text" in docs[0].content
