import logging
import os
from abc import abstractmethod
from typing import Any

from websocket_utils.models import ErrorMessage, ErrorContent
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


class MessagesError(Exception):
    """Base exception class for errors that occur during message processing."""

    def __init__(
        self,
        log_message: str | None = None,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(log_message)
        self.log_message = log_message
        self.status_code = status_code
        self.error_code = self.__class__.__name__

    @abstractmethod
    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Define how the error's displayed to the user."""
        pass


class ValidationError(MessagesError):
    """Raised when request validation fails."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""
        return ErrorMessage(
            content=ErrorContent(
                error="A server error occurred while processing the message.",
            )
        )


class UnexpectedError(MessagesError):
    """Raised when an unexpected error occurs."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        return ErrorMessage(
            content=ErrorContent(
                error="An unexpected error occurred while processing a message.",
            )
        )


class UnknownResourceType(MessagesError):
    """Raised when an unknown resource type is encountered."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        return ErrorMessage(
            content=ErrorContent(
                error="Internal server error occurred while processing message.",
            )
        )


class GenericStreamingError(MessagesError):
    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_resonse(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        return ErrorMessage(
            content=ErrorContent(
                error="Internal server error occurred while streaming a response.",
            )
        )


class ThrottlingError(MessagesError):
    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        return ErrorMessage(
            content=ErrorContent(
                error="Request was throttled due to too many requests. Please wait and try again.",
            )
        )


class ConfigNotFound(MessagesError):
    """Raised when a config is not found."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        return ErrorMessage(
            content=ErrorContent(
                error="Internal server error occurred while processing message.",
            )
        )


async def report_error(
    error: Exception,
    ws_connect: WebSocketServer | None = None,
    session_id: str | None = None,
):
    """
    Attempts to report an error over WebSocket, creating a connection from
    session ID if no connection's provided.
    """

    if not ws_connect and not session_id:
        logger.error("No WebSocket connection or session ID provided; skipping error report.")
        return

    if not ws_connect and session_id:
        try:
            ws_connect = get_ws_connection_from_session(session_id)
        except Exception as e:
            logger.error(f"Error getting WebSocket connection from session: {e}", exc_info=True)
            return

    # If both ws_connect and session_id are provided, ws_connect takes
    # precedence.

    if not isinstance(error, MessagesError):
        error = UnexpectedError(details={"original_error": str(error)})

    if ws_connect is None:
        logger.error("WebSocket connection is None; skipping error report.")
        return

    try:
        await ws_connect.send_json(error.to_response())
    except Exception as e:
        logger.error(f"Error streaming error message to client: {e}", exc_info=True)
        return
