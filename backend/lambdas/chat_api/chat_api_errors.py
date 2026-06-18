from abc import abstractmethod
from typing import Any


class ChatAPIError(Exception):
    """Base exception class for Chat API errors."""

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
        """Convert error to error response body."""
        pass


class ValidationError(ChatAPIError):
    """Raised when request validation fails."""

    def __init__(self, details: dict[str, Any] | None = None, reason: str | None = None):
        super().__init__(status_code=400, details=details)
        self.reason = reason

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": f"Invalid request. Reason: {self.reason}"
                if self.reason
                else "Invalid request.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class SessionNotFoundError(ChatAPIError):
    """Raised when we expected a session and one isn't found."""

    def __init__(self, session_id: str):
        super().__init__(
            log_message=f"Session '{session_id}' was not found.",
            status_code=404,
            details={"session_id": session_id},
        )
        self.session_id = session_id

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Could not find session. Try again with a new session.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class SessionCreationError(ChatAPIError):
    """Raised when session creation fails."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(
            log_message="Failed to create session.",
            status_code=500,
            details=details,
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Could not create session. Try again later.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class EventBridgeError(ChatAPIError):
    """Raised when EventBridge event creation fails."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(
            log_message="Failed to create an EventBridge event.",
            status_code=500,
            details=details,
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Internal server error.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class DynamoDBError(ChatAPIError):
    """Raised when DynamoDB operations fail."""

    def __init__(self, operation: str, details: dict[str, Any] | None = None):
        super().__init__(
            log_message=f"DynamoDB {operation} operation failed",
            status_code=500,
            details={"operation": operation, **details} if details else {"operation": operation},
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Internal server error.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class UnauthorizedError(ChatAPIError):
    """Raised when authentication/authorization fails."""

    def __init__(self, message: str = "Unauthorized access"):
        super().__init__(
            message,
            status_code=401,
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "Unauthorized request. Try signing out and signing in again.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


class UnexpectedError(ChatAPIError):
    """Used to describe a generic error not anticipated by ChatAPIError."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(
            log_message=None,
            status_code=500,
            details=details,
        )

    def to_response(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert error to response body."""
        response = {
            "error": {
                "message": "An unexpected error occurred.",
            },
        }
        if extra:
            response["error"].update(extra)
        return response


def create_error_body(error: Exception, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Defines a response body for an error."""
    if isinstance(error, ChatAPIError):
        return error.to_response(extra)

    # Handle unexpected errors
    unexpected_error = UnexpectedError(
        details={"original_error": str(error)},
    )

    return unexpected_error.to_response(extra)
