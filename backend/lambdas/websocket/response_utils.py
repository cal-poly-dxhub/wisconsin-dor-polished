import json
from typing import Any

from websocket_errors import UnexpectedError, WebSocketError


def create_websocket_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized response from a WebSocket handler."""
    return {
        "statusCode": status_code,
        "body": json.dumps(body),
        "isBase64Encoded": False,
        "headers": {
            "Content-Type": "application/json",
        },
    }


def create_error_response(error: WebSocketError) -> dict[str, Any]:
    """Create a standardized error response from any Exception."""
    body = create_error_body(error)
    return create_websocket_response(error.status_code, body)


def create_error_body(error: Exception) -> dict[str, Any]:
    """Create a standardized error response from any Exception."""
    if isinstance(error, WebSocketError):
        return error.to_response()

    unexpected_error = UnexpectedError(
        details={"original_error": str(error)},
    )

    return unexpected_error.to_response()
