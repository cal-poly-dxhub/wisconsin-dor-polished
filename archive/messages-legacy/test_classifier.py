import os
import sys
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)

from classifier.main import handler
from step_function_types.errors import ValidationError
from step_function_types.models import (
    ClassifierResult,
    FAQResource,
    MessageEvent,
)


class TestClassifierHandler:
    @patch("classifier.main.try_match_faq")
    @patch("classifier.main.process_query")
    def test_handler_with_faq_match(self, mock_process_query, mock_try_match_faq):
        mock_query = MessageEvent(
            query="What is an example?", query_id="test-query-123", session_id="test-session-456"
        )
        mock_faq = FAQResource(question="Example Question?", answer="This is an example answer.")

        mock_process_query.return_value = mock_query
        mock_try_match_faq.return_value = mock_faq

        event = {
            "detail": {
                "query": "What is an example?",
                "query_id": "test-query-123",
                "session_id": "test-session-456",
            }
        }
        result = handler(event, MagicMock())

        mock_process_query.assert_called_once_with(event)
        mock_try_match_faq.assert_called_once_with(mock_query.query)

        classifier_result = ClassifierResult(**result)
        assert classifier_result.query_class == "faq"
        assert classifier_result.stream_documents_job is not None
        assert classifier_result.generate_response_job is not None
        assert classifier_result.retrieve_job is None

        # Validate stream_documents_job
        stream_job = classifier_result.stream_documents_job
        assert stream_job.query_id == mock_query.query_id
        assert stream_job.session_id == mock_query.session_id
        assert stream_job.resource_type == "faq"
        assert stream_job.content == mock_faq

        # Validate generate_response_job
        gen_job = classifier_result.generate_response_job
        assert gen_job.query == mock_query.query
        assert gen_job.query_id == mock_query.query_id
        assert gen_job.session_id == mock_query.session_id
        assert gen_job.resource_type == "faq"
        assert gen_job.resources == mock_faq

    @patch("classifier.main.try_match_faq")
    @patch("classifier.main.process_query")
    def test_handler_with_no_faq_match(self, mock_process_query, mock_try_match_faq):
        mock_query = MessageEvent(
            query="How do I deploy?", query_id="test-query-123", session_id="test-session-456"
        )

        mock_process_query.return_value = mock_query
        mock_try_match_faq.return_value = None

        event = {
            "detail": {
                "query": "How do I deploy?",
                "query_id": "test-query-123",
                "session_id": "test-session-456",
            }
        }
        result = handler(event, MagicMock())

        mock_process_query.assert_called_once_with(event)
        mock_try_match_faq.assert_called_once_with(mock_query.query)

        classifier_result = ClassifierResult(**result)
        assert classifier_result.query_class == "rag"
        assert classifier_result.retrieve_job is not None
        assert classifier_result.stream_documents_job is None
        assert classifier_result.generate_response_job is None

        # Validate retrieve_job
        retrieve_job = classifier_result.retrieve_job
        assert retrieve_job.query == mock_query.query
        assert retrieve_job.query_id == mock_query.query_id
        assert retrieve_job.session_id == mock_query.session_id

    @patch("classifier.main.report_error")
    @patch("classifier.main.process_query")
    def test_handler_validation_error(self, mock_process_query, mock_report_error):
        mock_process_query.side_effect = ValidationError()

        event = {"detail": {"invalid": "data"}}

        result = handler(event, MagicMock())

        # No error reporting before validation; session_id was not retrieved yet.
        mock_process_query.assert_called_once_with(event)
        mock_report_error.assert_not_called()

        # Verify error response structure
        classifier_result = ClassifierResult(**result)
        assert classifier_result.query_class is None
        assert classifier_result.stream_documents_job is None
        assert classifier_result.generate_response_job is None
        assert classifier_result.retrieve_job is None

    @patch("classifier.main.report_error")
    @patch("classifier.main.try_match_faq")
    @patch("classifier.main.process_query")
    def test_handler_unexpected_error_in_try_match_faq(
        self, mock_process_query, mock_try_match_faq, mock_report_error
    ):
        mock_query = MessageEvent(
            query="What is an example?", query_id="test-query-123", session_id="test-session-456"
        )
        mock_process_query.return_value = mock_query
        mock_try_match_faq.side_effect = RuntimeError("Database connection failed")

        event = {
            "detail": {
                "query": "What is an example?",
                "query_id": "test-query-123",
                "session_id": "test-session-456",
            }
        }

        result = handler(event, MagicMock())

        mock_process_query.assert_called_once_with(event)
        mock_try_match_faq.assert_called_once_with(mock_query.query)

        # Verify that report_error was called with the exception and session_id
        mock_report_error.assert_called_once()
        call_args = mock_report_error.call_args[0]
        call_kwargs = mock_report_error.call_args[1]
        assert isinstance(call_args[0], RuntimeError)
        assert call_kwargs["session_id"] == "test-session-456"

        # Verify error response structure
        classifier_result = ClassifierResult(**result)
        assert classifier_result.query_class is None
        assert classifier_result.stream_documents_job is None
        assert classifier_result.generate_response_job is None
        assert classifier_result.retrieve_job is None

    @patch("classifier.main.report_error")
    @patch("classifier.main.process_query")
    def test_handler_validation_error_with_session_id(self, mock_process_query, mock_report_error):
        mock_query = MessageEvent(
            query="Invalid query", query_id="test-query-123", session_id="test-session-456"
        )
        mock_process_query.return_value = mock_query

        # Mock try_match_faq to raise ValidationError after session_id is set
        with patch("classifier.main.try_match_faq") as mock_try_match_faq:
            mock_try_match_faq.side_effect = ValidationError()

            event = {
                "detail": {
                    "query": "Invalid query",
                    "query_id": "test-query-123",
                    "session_id": "test-session-456",
                }
            }

            result = handler(event, MagicMock())

            mock_process_query.assert_called_once_with(event)
            mock_try_match_faq.assert_called_once_with(mock_query.query)

            # Verify that report_error was called with the exception and session_id
            mock_report_error.assert_called_once()
            call_args = mock_report_error.call_args[0]
            call_kwargs = mock_report_error.call_args[1]
            assert isinstance(call_args[0], ValidationError)
            assert call_kwargs["session_id"] == "test-session-456"

            # Verify error response structure
            classifier_result = ClassifierResult(**result)
            assert classifier_result.query_class is None
            assert classifier_result.stream_documents_job is None
            assert classifier_result.generate_response_job is None
            assert classifier_result.retrieve_job is None
