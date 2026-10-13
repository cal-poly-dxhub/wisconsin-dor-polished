"""Report a failed request to the user over the WebSocket."""

import logging
import os

from websocket_utils.models import ErrorContent, ErrorMessage
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


class MessagesError(Exception):
    """An error whose `user_message` is safe to show the user."""

    user_message = "An unexpected error occurred while processing a message."


class ValidationError(MessagesError):
    """Raised when the incoming request fails validation."""

    user_message = "A server error occurred while processing the message."


async def report_error(
    error: Exception,
    ws_connect: WebSocketServer | None = None,
    session_id: str | None = None,
    query_id: str | None = None,
):
    """
    Attempts to report an error over WebSocket, creating a connection from
    session ID if no connection's provided. Any error that is not a
    MessagesError is reported with the generic unexpected-error text.
    """

    if not ws_connect and not session_id:
        logger.error("No WebSocket connection or session ID provided; skipping error report.")
        return

    # If both ws_connect and session_id are provided, ws_connect takes
    # precedence.
    if not ws_connect and session_id:
        try:
            ws_connect = get_ws_connection_from_session(session_id)
        except Exception as e:
            logger.error(f"Error getting WebSocket connection from session: {e}", exc_info=True)
            return

    if ws_connect is None:
        logger.error("WebSocket connection is None; skipping error report.")
        return

    text = error.user_message if isinstance(error, MessagesError) else MessagesError.user_message
    message = ErrorMessage(query_id=query_id, content=ErrorContent(error=text))
    try:
        await ws_connect.send_json(message)
    except Exception as e:
        logger.error(f"Error streaming error message to client: {e}", exc_info=True)
        return
