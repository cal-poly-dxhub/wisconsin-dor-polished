import os
import sys
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)

from retrieval.main import DocumentQueryResult, handler
from step_function_types.errors import ValidationError
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    RAGDocument,
    RetrieveJob,
    RetrieveResult,
)


class TestRetrievalHandler:
    @patch("retrieval.main.retrieve_documents")
    def test_handler_success_with_rag_documents(self, mock_retrieve_documents):
        # Setup mocks for RAG documents
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

        # Return DocumentQueryResult with RAG documents
        mock_retrieve_documents.return_value = DocumentQueryResult(
            document_type="RAG", documents=mock_documents
        )

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_retrieve_documents.assert_called_once_with(mock_query)

        # Verify result structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.successful is True
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
    def test_handler_success_with_faq_resource(self, mock_retrieve_documents):
        # Setup mocks for FAQ resource
        mock_query = "Show me FAQ about deployment"
        mock_query_id = "test-query-faq-123"
        mock_session_id = "test-session-456"

        mock_faq = FAQResource(
            question="How do I deploy my application?",
            answer="You can deploy your application by following these steps...",
        )

        # Return DocumentQueryResult with FAQ
        mock_retrieve_documents.return_value = DocumentQueryResult(
            document_type="FAQ", faq=mock_faq
        )

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_retrieve_documents.assert_called_once_with(mock_query)

        # Verify result structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.successful is True
        assert retrieve_result.generate_response_job is not None
        assert retrieve_result.stream_documents_job is not None

        # Validate generate_response_job for FAQ
        gen_job = retrieve_result.generate_response_job
        assert gen_job.query == mock_query
        assert gen_job.query_id == mock_query_id
        assert gen_job.session_id == mock_session_id
        assert gen_job.resource_type == "faq"
        assert isinstance(gen_job.resources, FAQResource)
        assert gen_job.resources.question == "How do I deploy my application?"
        assert (
            gen_job.resources.answer
            == "You can deploy your application by following these steps..."
        )

        # Validate stream_documents_job for FAQ
        stream_job = retrieve_result.stream_documents_job
        assert stream_job.query_id == mock_query_id
        assert stream_job.session_id == mock_session_id
        assert stream_job.resource_type == "faq"
        assert isinstance(stream_job.content, FAQResource)
        assert stream_job.content.question == "How do I deploy my application?"

    @patch("retrieval.main.retrieve_documents")
    def test_handler_with_empty_documents(self, mock_retrieve_documents):
        # Setup mocks
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        # Return DocumentQueryResult with empty documents list
        mock_retrieve_documents.return_value = DocumentQueryResult(
            document_type="RAG", documents=[]
        )

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_retrieve_documents.assert_called_once_with(mock_query)

        # Verify result structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.successful is True
        assert retrieve_result.generate_response_job is not None
        assert retrieve_result.stream_documents_job is not None

        # Validate generate_response_job with empty documents
        gen_job = retrieve_result.generate_response_job
        assert gen_job.query == mock_query
        assert gen_job.query_id == mock_query_id
        assert gen_job.session_id == mock_session_id
        assert gen_job.resource_type == "documents"
        assert isinstance(gen_job.resources, DocumentResource)

    @patch("retrieval.main.retrieve_documents")
    def test_handler_invalid_document_query_result(self, mock_retrieve_documents):
        # Test error handling for invalid DocumentQueryResult
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        # Return DocumentQueryResult with invalid state (FAQ type but no faq content)
        mock_retrieve_documents.return_value = DocumentQueryResult(
            document_type="FAQ", documents=None, faq=None
        )

        # Test event
        event = {"query": mock_query, "query_id": mock_query_id, "session_id": mock_session_id}

        result = handler(event, MagicMock())

        # Verify result indicates failure due to invalid state
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.successful is False
        assert retrieve_result.generate_response_job is None
        assert retrieve_result.stream_documents_job is None

    @patch("retrieval.main.report_error")
    def test_handler_validation_error(self, mock_report_error):
        event = {"invalid": "data"}

        result = handler(event, MagicMock())

        # Verify error handling
        mock_report_error.assert_not_called()  # No session_id available yet

        # Verify error response structure
        retrieve_result = RetrieveResult(**result)
        assert retrieve_result.successful is False
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
        assert retrieve_result.successful is False
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
            assert retrieve_result.successful is False
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
        assert retrieve_result.successful is False
        assert retrieve_result.generate_response_job is None
        assert retrieve_result.stream_documents_job is None
