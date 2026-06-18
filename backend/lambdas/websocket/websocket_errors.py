from abc import abstractmethod
from typing import Any


class WebSocketError(Exception):
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
    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Define how the error's displayed to the user."""
        pass


class ValidationError(WebSocketError):
    """
    Raised when the websocket request body (into connect, disconnect, default) is invalid.
    """

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(
        self, extra: dict[str, Any] | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "A server error occurred while processing a WebSocket request.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class SessionNotFound(WebSocketError):
    """
    Raised when a session ID is not found in the database.
    """

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        super().__init__(status_code=404, details=details)
        self.session_id = session_id

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Session not found. Try logging out and logging back in.",
                "sessionId": self.session_id,
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class UnexpectedError(WebSocketError):
    """
    Raised when an unexpected error occurs while processing a WebSocket request.
    """

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(status_code=500, details=details)

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "A server error occurred while processing a WebSocket request.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


def create_error_body(error: Exception) -> dict[str, Any]:
    """
    Create a response from any Exception.
    """
    if isinstance(error, WebSocketError):
        return error.to_response()

    unexpected_error = UnexpectedError(
        details={"original_error": str(error)},
    )

    return unexpected_error.to_response()
