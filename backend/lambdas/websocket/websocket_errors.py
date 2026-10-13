from abc import abstractmethod
from typing import Any


class WebSocketError(Exception):
    """Base exception class for errors that occur during message processing."""

    def __init__(self, log_message: str | None = None, status_code: int = 500):
        super().__init__(log_message)
        self.status_code = status_code

    @abstractmethod
    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Define how the error's displayed to the user."""
        pass


class ValidationError(WebSocketError):
    """
    Raised when the websocket event (into connect or disconnect) is invalid.
    """

    def __init__(self):
        super().__init__(status_code=500)

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


class SessionNotFound(WebSocketError):
    """
    Raised when a session ID is not found in the database.
    """

    def __init__(self, session_id: str):
        super().__init__(status_code=404)
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


class Unauthorized(WebSocketError):
    """Raised when $connect carries no valid Cognito ID token."""

    def __init__(self, reason: str):
        super().__init__(log_message=reason, status_code=401)
        self.reason = reason

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        response = {"error": {"message": "Sign in again to continue."}}
        if extra:
            response["error"].update(extra)
        return response


class UnexpectedError(WebSocketError):
    """
    Raised when an unexpected error occurs while processing a WebSocket request.
    """

    def __init__(self):
        super().__init__(status_code=500)

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

    return UnexpectedError().to_response()
