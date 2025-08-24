import os
import sys
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)

from resource_streaming.main import handler
from step_function_types.errors import UnknownResourceType
from step_function_types.models import (
    StreamResourcesResult,
)


class TestResourceStreamingHandler:
    @patch("resource_streaming.main._stream_resources_async")
    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_with_document_resource_success(
        self, mock_get_ws_connection, mock_stream_resources
    ):
        # Setup mocks
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.return_value = None

        # Test event
        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {
                "documents": [
                    {
                        "document_id": "doc-001",
                        "title": "Deployment Guide",
                        "content": "This is a deployment guide.",
                        "source": "https://example.com/deploy",
                    },
                    {
                        "document_id": "doc-002",
                        "title": "Configuration Guide",
                        "content": "This is a configuration guide.",
                        "source": "https://example.com/config",
                    },
                ]
            },
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()

        # Verify result structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    @patch("resource_streaming.main._stream_resources_async")
    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_with_faq_resource_success(self, mock_get_ws_connection, mock_stream_resources):
        # Setup mocks
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.return_value = None

        # Test event
        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "faq",
            "content": {"question": "Example Question?", "answer": "This is an example answer."},
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()

        # Verify result structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    def test_handler_process_event_error(self):
        event = {"invalid": "data"}

        result = handler(event, MagicMock())

        # Verify error handling - process_event will fail with invalid data

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_no_websocket_connection(self, mock_get_ws_connection):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_get_ws_connection.return_value = None  # No WebSocket connection found

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_websocket_connection_error(self, mock_get_ws_connection):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_get_ws_connection.side_effect = Exception("WebSocket connection failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("resource_streaming.main.report_error")
    @patch("resource_streaming.main._stream_resources_async")
    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_streaming_error(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = Exception("Streaming failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert (
            stream_result.successful is True
        )  # Note: this lambda returns successful=True even on streaming errors

    @patch("resource_streaming.main.report_error")
    @patch("resource_streaming.main._stream_resources_async")
    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_unknown_resource_type(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = UnknownResourceType()

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    @patch("resource_streaming.main.report_error")
    @patch("resource_streaming.main._stream_resources_async")
    @patch("resource_streaming.main.get_ws_connection_from_session")
    def test_handler_report_error_failure(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()
        mock_ws_connection.send_json.side_effect = Exception("Error reporting failed")

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = Exception("Streaming failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True
