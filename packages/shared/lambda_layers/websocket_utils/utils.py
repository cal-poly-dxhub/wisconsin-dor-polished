import json
import logging
import os

import boto3

from websocket_utils.models import PlainWebSocketMessage, WebSocketMessage

logger = logging.getLogger(__name__)
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


class WebSocketServer:
    """
    For sending either JSON messages or streaming fragments over a WebSocket connection.
    """

    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        # Extract domain and stage from the WebSocket callback URL
        endpoint_url = os.environ["WEBSOCKET_CALLBACK_URL"]
        # Convert wss://domain/stage to https://domain/stage
        endpoint_url = endpoint_url.replace("wss://", "https://")
        self.client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint_url)

    async def send_json(self, body: WebSocketMessage) -> None:
        """
        Send a JSON object over websocket using API Gateway Management API.
        Args:
            body: The message body (Pydantic model).
        """

        match body:
            # For echoing
            case PlainWebSocketMessage(message=message):
                pass
            case _:
                logger.error("WebSocket client received an unknown message type")

        try:
            response = self.client.post_to_connection(
                ConnectionId=self.connection_id, Data=json.dumps(message)
            )
            logger.debug(f"Message sent successfully to connection {self.connection_id}")
            return response
        except Exception as e:
            logger.error(f"Failed to send message to connection {self.connection_id}: {e}")
            raise
