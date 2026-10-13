from typing import Any


class WebSocketError(Exception):
    """Base exception class for WebSocket-related errors."""

    def __init__(
        self,
        log_message: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(log_message)
        self.details = details


class WebSocketConnectionError(WebSocketError):
    """Raised when the API Gateway management client cannot be created."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)


class MessageDeliveryError(WebSocketError):
    """Raised when message delivery over WebSocket fails."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)


class SessionNotFoundError(WebSocketError):
    """Raised when a session cannot be found for WebSocket connection lookup."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(log_message=f"Session not found: {session_id}", details=details)


class InvalidMessageError(WebSocketError):
    """Raised when a message type has no stream id (see utils.STREAM_IDS)."""

    def __init__(self, details: dict[str, Any] | None = None):
        super().__init__(details=details)


class SessionLookupError(WebSocketError):
    """Raised when session lookup fails."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(
            log_message=f"Failed to lookup session {session_id} in database", details=details
        )


class ConnectionNotFoundError(WebSocketError):
    """Raised when the session does not have a WebSocket connection."""

    def __init__(self, session_id: str, details: dict[str, Any] | None = None):
        self.session_id = session_id
        super().__init__(details=details)
