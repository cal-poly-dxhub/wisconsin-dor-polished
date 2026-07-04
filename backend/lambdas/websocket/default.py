import asyncio
import logging
import os

from response_utils import create_error_response, create_websocket_response
from validators import validate_message_event
from websocket_errors import WebSocketError, create_error_body
from websocket_utils.models import PlainWebSocketMessage
from websocket_utils.utils import WebSocketServer

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


async def echo_message(connection_id: str, message: str) -> None:
    """Echo a message back to the WebSocket client"""
    websocket_server = WebSocketServer(connection_id)
    echo_message = PlainWebSocketMessage(message=message)
    await websocket_server.send_json(echo_message)


def handler(event, context):
    """
    Default WebSocket message handler - echoes messages back to the client
    """
    try:
        validated_event = validate_message_event(event)
        connection_id = validated_event.requestContext.connectionId
        message_body = validated_event.body

        asyncio.run(echo_message(connection_id, message_body))
        return create_websocket_response(200, {"message": "Message echoed successfully"})

    except WebSocketError as e:
        logger.error(f"Error while processing message event: {e}")
        return create_error_response(e)
    except Exception as e:
        logger.error(f"Unexpected error while processing message event: {e}", exc_info=True)
        body = create_error_body(e)
        return create_websocket_response(500, body)
