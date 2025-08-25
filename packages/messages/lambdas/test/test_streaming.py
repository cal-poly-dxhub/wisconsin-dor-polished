import os
import sys
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "streaming"))

with patch.dict(os.environ, {"SESSIONS_TABLE_NAME": "test-sessions-table", "LOG_LEVEL": "INFO"}):
    from step_function_types.errors import ValidationError
    from step_function_types.models import (
        GenerateResponseResult,
    )
    from streaming.main import handler


class TestStreamingHandler:
    @patch("streaming.main.get_ws_connection_from_session")
    @patch("streaming.main._stream_message_async")
    @patch("streaming.main.generate_response_async")
    def test_handler_with_faq_resource_success(
        self, mock_generate_response, mock_stream_message, mock_get_ws_connection
    ):
        # Setup mocks
        mock_query = "What is an example?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()
        mock_get_ws_connection.return_value = mock_ws_connection

        # Mock the async generator
        async def mock_response_gen():
            yield "Response fragment 1"
            yield "Response fragment 2"

        mock_generate_response.return_value = mock_response_gen()
        mock_stream_message.return_value = True

        # Test event
        event = {
            "query": mock_query,
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "faq",
            "resources": {"question": "Example Question?", "answer": "This is an example answer."},
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_generate_response.assert_called_once()
        mock_stream_message.assert_called_once()

        # Verify result structure
        response_result = GenerateResponseResult(**result)
        assert response_result.successful is True

    @patch("streaming.main.get_ws_connection_from_session")
    @patch("streaming.main._stream_message_async")
    @patch("streaming.main.generate_response_async")
    def test_handler_with_document_resource_success(
        self, mock_generate_response, mock_stream_message, mock_get_ws_connection
    ):
        # Setup mocks
        mock_query = "How do I deploy?"
        mock_query_id = "test-query-789"
        mock_session_id = "test-session-101"

        mock_ws_connection = MagicMock()
        mock_get_ws_connection.return_value = mock_ws_connection

        # Mock the async generator
        async def mock_response_gen():
            yield "Document-based response"

        mock_generate_response.return_value = mock_response_gen()
        mock_stream_message.return_value = True

        # Test event
        event = {
            "query": mock_query,
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "resources": {
                "documents": [
                    {
                        "document_id": "doc-001",
                        "title": "Deployment Guide",
                        "content": "This is a deployment guide.",
                        "source": "https://example.com/deploy",
                    }
                ]
            },
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_generate_response.assert_called_once()
        mock_stream_message.assert_called_once()

        # Verify result structure
        response_result = GenerateResponseResult(**result)
        assert response_result.successful is True

    @patch("streaming.main.report_error")
    def test_handler_validation_error(self, mock_report_error):
        event = {"invalid": "data"}

        result = handler(event, MagicMock())

        # Verify error handling
        mock_report_error.assert_not_called()  # No session_id available yet

        # Verify error response structure
        response_result = GenerateResponseResult(**result)
        assert response_result.successful is False

    @patch("streaming.main.get_ws_connection_from_session")
    @patch("streaming.main.report_error")
    @patch("streaming.main._stream_message_async")
    @patch("streaming.main.generate_response_async")
    def test_handler_streaming_error(
        self, mock_generate_response, mock_stream_message, mock_report_error, mock_get_ws_connection
    ):
        mock_query = "What is an example?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()
        mock_get_ws_connection.return_value = mock_ws_connection

        # Mock the async generator
        async def mock_response_gen():
            yield "Response fragment"

        mock_generate_response.return_value = mock_response_gen()
        mock_stream_message.side_effect = Exception("WebSocket connection failed")

        event = {
            "query": mock_query,
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "faq",
            "resources": {"question": "Example Question?", "answer": "This is an example answer."},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_generate_response.assert_called_once()
        mock_stream_message.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        response_result = GenerateResponseResult(**result)
        assert response_result.successful is False

    @patch("streaming.main.get_ws_connection_from_session")
    @patch("streaming.main.report_error")
    @patch("streaming.main.generate_response_async")
    def test_handler_generate_response_error(
        self, mock_generate_response, mock_report_error, mock_get_ws_connection
    ):
        mock_query = "What is an example?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()
        mock_get_ws_connection.return_value = mock_ws_connection

        mock_generate_response.side_effect = ValueError("Invalid resource type")

        event = {
            "query": mock_query,
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "faq",
            "resources": {"question": "Example Question?", "answer": "This is an example answer."},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_generate_response.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        response_result = GenerateResponseResult(**result)
        assert response_result.successful is False

    @patch("streaming.main.get_ws_connection_from_session")
    @patch("streaming.main.report_error")
    def test_handler_validation_error_with_session_id(
        self, mock_report_error, mock_get_ws_connection
    ):
        mock_query = "What is an example?"
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        # Mock WebSocket connection
        mock_ws_connection = MagicMock()
        mock_get_ws_connection.return_value = mock_ws_connection

        # Mock generate_response_async to raise ValidationError after session_id is set
        with patch("streaming.main.generate_response_async") as mock_generate_response:
            mock_generate_response.side_effect = ValidationError()

            event = {
                "query": mock_query,
                "query_id": mock_query_id,
                "session_id": mock_session_id,
                "resource_type": "faq",
                "resources": {
                    "question": "Example Question?",
                    "answer": "This is an example answer.",
                },
            }

            result = handler(event, MagicMock())

            # Verify error handling
            mock_generate_response.assert_called_once()
            mock_report_error.assert_called_once()

            # Verify error response structure
            response_result = GenerateResponseResult(**result)
            assert response_result.successful is False
