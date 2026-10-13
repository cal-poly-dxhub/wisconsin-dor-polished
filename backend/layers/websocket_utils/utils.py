import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from websocket_utils.errors import (
    ConnectionNotFoundError,
    InvalidMessageError,
    MessageDeliveryError,
    SessionLookupError,
    SessionNotFoundError,
    WebSocketConnectionError,
)
from websocket_utils.models import (
    AgentEventMessage,
    AnswerEventType,
    ChoicesMessage,
    DocumentsMessage,
    ErrorMessage,
    FAQMessage,
    FlowchartMessage,
    FragmentMessage,
    WebSocketMessage,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

# The frame's `streamId` for each message type. The frontend validates it
# against its own enum, then routes on the body's `responseType`. Adding a
# message type means adding it here (and to the frontend Zod union).
STREAM_IDS: dict[type[WebSocketMessage], str] = {
    ErrorMessage: "error",
    DocumentsMessage: "resources",
    FAQMessage: "resources",
    FlowchartMessage: "resources",
    AnswerEventType: "answer-event",
    FragmentMessage: "answer",
    AgentEventMessage: "agent-trace",
    ChoicesMessage: "choices",
}


def frame(body: WebSocketMessage) -> str:
    """Serialize a message as the `{streamId, body}` frame the client expects."""
    stream_id = STREAM_IDS.get(type(body))
    if stream_id is None:
        logger.error("Unknown WebSocket message type: %s", type(body).__name__)
        raise InvalidMessageError(details={"message_type": type(body).__name__})
    return json.dumps({"streamId": stream_id, "body": body.model_dump(by_alias=True)})


def post_message(ws_server: Any, body: WebSocketMessage) -> Any:
    """Post one typed message to the server's connection (synchronous).

    Delivery errors propagate unchanged; callers decide whether a dead
    connection matters.
    """
    return ws_server.client.post_to_connection(
        ConnectionId=ws_server.connection_id, Data=frame(body)
    )


class WebSocketServer:
    """Sends typed messages to one API Gateway WebSocket connection."""

    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        endpoint_url = os.environ["WEBSOCKET_CALLBACK_URL"]

        # Convert wss://domain/stage to https://domain/stage
        endpoint_url = endpoint_url.replace("wss://", "https://")
        try:
            self.client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint_url)
        except Exception as e:
            logger.error(f"Failed to create WebSocket client: {e}")
            raise WebSocketConnectionError(details={"original_error": str(e)}) from e

    async def send_json(self, body: WebSocketMessage) -> None:
        """Post a message, wrapping a delivery failure in MessageDeliveryError."""
        data = frame(body)
        try:
            response = self.client.post_to_connection(ConnectionId=self.connection_id, Data=data)
            logger.info(f"Message sent successfully to connection {self.connection_id}")
            return response
        except Exception as e:
            logger.error(f"Failed to send message to connection {self.connection_id}: {e}")
            raise MessageDeliveryError(
                details={"connection_id": self.connection_id, "original_error": str(e)}
            ) from e


def get_ws_connection_from_session(session_id: str) -> WebSocketServer:
    """
    Look up the websocket connection ID for a session in DynamoDB and return a WebSocket client.
    """
    dynamodb = boto3.client("dynamodb")
    table_name = os.environ["SESSIONS_TABLE_NAME"]

    logger.info(f"Looking up connection ID for session {session_id} in table {table_name}")

    try:
        resp = dynamodb.get_item(
            TableName=table_name,
            Key={"sessionId": {"S": session_id}},
        )
    except ClientError as e:
        logger.error(f"Failed to lookup connection ID for session {session_id}: {e}")
        raise SessionLookupError(session_id=session_id, details={"aws_error": str(e)}) from e

    item = resp.get("Item")
    if not item:
        logger.error(f"No session found with session ID {session_id}")
        raise SessionNotFoundError(session_id=session_id, details={"table_response": resp})
    if not item.get("connectionId") or not item["connectionId"].get("S"):
        logger.error(f"No connection ID found for session ID {session_id}")
        raise ConnectionNotFoundError(session_id=session_id, details={"table_response": resp})

    connection_id = item["connectionId"]["S"]
    return WebSocketServer(connection_id)
