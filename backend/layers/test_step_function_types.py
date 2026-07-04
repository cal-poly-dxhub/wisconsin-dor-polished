from unittest.mock import AsyncMock, patch

import pytest
from step_function_types.errors import (
    ValidationError,
    report_error,
)
from websocket_utils.errors import (
    MessageDeliveryError,
    SessionNotFoundError,
)
from websocket_utils.models import ErrorMessage
from websocket_utils.utils import WebSocketServer


class TestReportError:
    @pytest.mark.asyncio
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_with_generic_exception(self, mock_get_ws_connection):
        session_id = "test-session-123"
        generic_error = RuntimeError("Database connection failed")

        mock_ws = AsyncMock(spec=WebSocketServer)
        mock_get_ws_connection.return_value = mock_ws

        await report_error(generic_error, session_id=session_id)

        mock_get_ws_connection.assert_called_once_with(session_id)
        mock_ws.send_json.assert_called_once()

        sent_message = mock_ws.send_json.call_args[0][0]
        assert isinstance(sent_message, ErrorMessage)
        assert (
            sent_message.content.error == "An unexpected error occurred while processing a message."
        )

    @pytest.mark.asyncio
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_with_messages_error(self, mock_get_ws_connection):
        session_id = "test-session-456"
        validation_error = ValidationError(details={"field": "invalid"})

        mock_ws = AsyncMock(spec=WebSocketServer)
        mock_get_ws_connection.return_value = mock_ws

        await report_error(validation_error, session_id=session_id)

        mock_get_ws_connection.assert_called_once_with(session_id)
        mock_ws.send_json.assert_called_once()

        sent_message = mock_ws.send_json.call_args[0][0]
        assert isinstance(sent_message, ErrorMessage)
        assert sent_message.content.error == "A server error occurred while processing the message."

    @pytest.mark.asyncio
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_with_websocket_error(self, mock_get_ws_connection):
        session_id = "test-session-789"
        websocket_error = MessageDeliveryError(details={"connection_id": "conn-123"})

        mock_ws = AsyncMock(spec=WebSocketServer)
        mock_get_ws_connection.return_value = mock_ws

        await report_error(websocket_error, session_id=session_id)

        mock_get_ws_connection.assert_called_once_with(session_id)
        mock_ws.send_json.assert_called_once()

        sent_message = mock_ws.send_json.call_args[0][0]
        assert isinstance(sent_message, ErrorMessage)
        assert (
            sent_message.content.error == "An unexpected error occurred while processing a message."
        )

    @pytest.mark.asyncio
    @patch("step_function_types.errors.logger")
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_connection_lookup_fails(self, mock_get_ws_connection, mock_logger):
        session_id = "invalid-session"
        error = RuntimeError("Test error")

        mock_get_ws_connection.side_effect = SessionNotFoundError(
            session_id=session_id, details={"table_response": {}}
        )

        await report_error(error, session_id=session_id)

        mock_get_ws_connection.assert_called_once_with(session_id)
        mock_logger.error.assert_called_once()

        log_call = mock_logger.error.call_args
        assert "Error getting WebSocket connection from session" in log_call[0][0]
        assert session_id in log_call[0][0]

    @pytest.mark.asyncio
    @patch("step_function_types.errors.logger")
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_send_message_fails(self, mock_get_ws_connection, mock_logger):
        session_id = "test-session-send-fail"
        error = RuntimeError("Test error")

        mock_ws = AsyncMock(spec=WebSocketServer)
        mock_ws.send_json.side_effect = MessageDeliveryError(
            details={"connection_id": "conn-123", "original_error": "Connection closed"}
        )
        mock_get_ws_connection.return_value = mock_ws

        await report_error(error, session_id=session_id)

        mock_get_ws_connection.assert_called_once_with(session_id)
        mock_ws.send_json.assert_called_once()
        mock_logger.error.assert_called_once()

        log_call = mock_logger.error.call_args
        assert "Error streaming error message to client" in log_call[0][0]

    @pytest.mark.asyncio
    @patch("step_function_types.errors.logger")
    @patch("step_function_types.errors.get_ws_connection_from_session")
    async def test_report_error_with_different_exception_types(
        self, mock_get_ws_connection, mock_logger
    ):
        session_id = "test-session-types"
        mock_ws = AsyncMock(spec=WebSocketServer)
        mock_get_ws_connection.return_value = mock_ws

        await report_error(ValueError("Invalid value"), session_id=session_id)
        assert mock_ws.send_json.call_count == 1

        await report_error(KeyError("missing_key"), session_id=session_id)
        assert mock_ws.send_json.call_count == 2

        class CustomError(Exception):
            pass

        await report_error(CustomError("Custom error message"), session_id=session_id)
        assert mock_ws.send_json.call_count == 3

        for call in mock_ws.send_json.call_args_list:
            sent_message = call[0][0]
            assert isinstance(sent_message, ErrorMessage)
            assert (
                sent_message.content.error
                == "An unexpected error occurred while processing a message."
            )
