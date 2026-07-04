from abc import abstractmethod
from typing import Any

from websocket_utils.models import ErrorContent, ErrorMessage


class WebSocketError(Exception):
    """Base exception class for WebSocket-related errors."""

    def __init__(
        self,
        log_message: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(log_message)
        self.log_message = log_message
        self.error_code = self.__class__.__name__
        self.details = details

    @abstractmethod
    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Define how the error's displayed to the user."""
        pass


class WebSocketConnectionError(WebSocketError):
    """Raised when WebSocket connection operations fail."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""
        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="Failed to establish WebSocket connection. Try signing in again."
            ),
        )


class MessageDeliveryError(WebSocketError):
    """Raised when message delivery over WebSocket fails."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""
        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="Failed to deliver message over WebSocket. Try signing in again."
            ),
        )


class SessionNotFoundError(WebSocketError):
    """Raised when a session cannot be found for WebSocket connection lookup."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(log_message=f"Session not found: {session_id}", details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""
        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="Session not found. Try signing in again.",
            ),
        )


class InvalidMessageError(WebSocketError):
    """Raised when an invalid message type is sent over WebSocket."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""
        # return ErrorMessage(error="Internal server error.")
        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="Internal server error.",
            ),
        )


class SessionLookupError(WebSocketError):
    """Raised when session lookup fails."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(
            log_message=f"Failed to lookup session {session_id} in database", details=details
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""

        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="Unable to retrieve session information. Try signing in again."
            ),
        )


class ConnectionNotFoundError(WebSocketError):
    """Raised when the sesssion does not have a WebSocket connection."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> ErrorMessage:
        """Convert error to response body."""

        return ErrorMessage(
            response_type="error",
            content=ErrorContent(
                error="WebSocket connection not found. Try signing in again.",
            ),
        )
