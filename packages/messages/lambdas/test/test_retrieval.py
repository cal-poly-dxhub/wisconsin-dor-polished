import os
import sys
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)

from retrieval.main import handler
from step_function_types.errors import ValidationError
from step_function_types.models import (
    DocumentResource,
    RAGDocument,
    RetrieveJob,
    RetrieveResult,
)


class TestRetrievalHandler:
    @patch("retrieval.main.retrieve_documents")
    def test_handler_success(self, mock_retrieve_documents):
        # Setup mocks
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_documents = [
            RAGDocument(
                document_id="doc-001",
                title="Deployment Guide",
                content="This is a deployment guide.",
                source="https://example.com/deploy",
            ),
            RAGDocument(
                document_id="doc-002",
                title="Configuration Guide",
                content="This is a configuration guide.",
                source="https://example.com/config",
            ),
        ]

        mock_retrieve_documents.return_value = mock_documents

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_retrieve_documents.assert_called_once_with(mock_query)

        # Verify result structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.generate_response_job is not None
        assert retrieve_result.stream_documents_job is not None

        # Validate generate_response_job
        gen_job = retrieve_result.generate_response_job
        assert gen_job.query == mock_query
        assert gen_job.query_id == mock_query_id
        assert gen_job.session_id == mock_session_id
        assert gen_job.resource_type == "documents"
        assert isinstance(gen_job.resources, DocumentResource)
        assert len(gen_job.resources.documents) == 2
        assert gen_job.resources.documents[0].document_id == "doc-001"
        assert gen_job.resources.documents[1].document_id == "doc-002"

        # Validate stream_documents_job
        stream_job = retrieve_result.stream_documents_job
        assert stream_job.query_id == mock_query_id
        assert stream_job.session_id == mock_session_id
        assert stream_job.resource_type == "documents"
        assert isinstance(stream_job.content, DocumentResource)
        assert len(stream_job.content.documents) == 2

    @patch("retrieval.main.retrieve_documents")
    def test_handler_with_empty_documents(self, mock_retrieve_documents):
        # Setup mocks
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_retrieve_documents.return_value = []  # Empty documents list

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_retrieve_documents.assert_called_once_with(mock_query)

        # Verify result structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.generate_response_job is not None
        assert retrieve_result.stream_documents_job is not None

        # Validate generate_response_job with empty documents
        gen_job = retrieve_result.generate_response_job
        assert gen_job.query == mock_query
        assert gen_job.query_id == mock_query_id
        assert gen_job.session_id == mock_session_id
        assert gen_job.resource_type == "documents"
        assert isinstance(gen_job.resources, DocumentResource)
        assert len(gen_job.resources.documents) == 0

    @patch("retrieval.main.report_error")
    def test_handler_validation_error(self, mock_report_error):
        event = {"invalid": "data"}

        result = handler(event, MagicMock())

        # Verify error handling
        mock_report_error.assert_not_called()  # No session_id available yet

        # Verify error response structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.generate_response_job is None
        assert retrieve_result.stream_documents_job is None

    @patch("retrieval.main.report_error")
    @patch("retrieval.main.retrieve_documents")
    def test_handler_retrieval_error(self, mock_retrieve_documents, mock_report_error):
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_retrieve_documents.side_effect = Exception("Database connection failed")

        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify error handling
        mock_retrieve_documents.assert_called_once_with(mock_query)
        mock_report_error.assert_called_once()

        # Verify error response structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.generate_response_job is None
        assert retrieve_result.stream_documents_job is None

    @patch("retrieval.main.report_error")
    def test_handler_validation_error_with_session_id(self, mock_report_error):
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        # Mock retrieve_documents to raise ValidationError after session_id is set
        with patch("retrieval.main.retrieve_documents") as mock_retrieve_documents:
            mock_retrieve_documents.side_effect = ValidationError()

            event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

            result = handler(event, MagicMock())

            # Verify error handling
            mock_retrieve_documents.assert_called_once_with(mock_query)
            mock_report_error.assert_called_once()

            # Verify error response structure
            retrieve_result = RetrieveResult(**result)
            assert retrieve_result.generate_response_job is None
            assert retrieve_result.stream_documents_job is None

    @patch("retrieval.main.report_error")
    @patch("retrieval.main.retrieve_documents")
    def test_handler_unexpected_error(self, mock_retrieve_documents, mock_report_error):
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_retrieve_documents.side_effect = RuntimeError("Unexpected error")

        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify error handling
        mock_retrieve_documents.assert_called_once_with(mock_query)
        mock_report_error.assert_called_once()

        # Verify error response structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.generate_response_job is None
        assert retrieve_result.stream_documents_job is None
