import json
import logging
import os
from collections.abc import AsyncGenerator

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
    AnswerEventType,
    DocumentsMessage,
    ErrorMessage,
    FAQMessage,
    FragmentContent,
    FragmentMessage,
    PlainWebSocketMessage,
    WebSocketMessage,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


class WebSocketServer:
    """
    For sending either JSON messages or streaming fragments over a WebSocket connection.
    """

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
        logger.info(f"Sending message to connection {self.connection_id}")

        match body:
            case ErrorMessage(error=error):
                message = {"error": error}

            case DocumentsMessage():
                message = {
                    "streamId": "resources",
                    "body": body.model_dump(by_alias=True),
                }
            case FAQMessage():
                message = {
                    "streamId": "resources",
                    "body": body.model_dump(by_alias=True),
                }
            case AnswerEventType(event="start"):
                message = {
                    "streamId": "answer-event",
                    "body": body.model_dump(by_alias=True),
                }
            case AnswerEventType(event="stop"):
                message = {
                    "streamId": "answer-event",
                    "body": body.model_dump(by_alias=True),
                }
            case PlainWebSocketMessage(message=message):
                # For echoing during testing
                pass
            case _:
                logger.error("WebSocket client received an unknown message type")
                raise InvalidMessageError(details={"message_type": type(body).__name__})

        try:
            response = self.client.post_to_connection(
                ConnectionId=self.connection_id, Data=json.dumps(message)
            )
            logger.info(f"Message sent successfully to connection {self.connection_id}")
            return response
        except Exception as e:
            logger.error(f"Failed to send message to connection {self.connection_id}: {e}")
            raise MessageDeliveryError(
                details={"connection_id": self.connection_id, "original_error": str(e)}
            ) from e

    async def stream_fragments(self, event_stream: AsyncGenerator[str], query_id: str) -> None:
        logger.info(f"Streaming fragments to connection {self.connection_id}")

        async for fragment in event_stream:
            fragment_content = FragmentContent(fragment=fragment)
            fragment_message = FragmentMessage(query_id=query_id, content=fragment_content)
            message = {
                "streamId": "answer",
                "body": fragment_message.model_dump(by_alias=True),
            }

            # Send fragment
            try:
                response = self.client.post_to_connection(
                    ConnectionId=self.connection_id, Data=json.dumps(message)
                )
            except Exception as e:
                logger.error(f"Failed to send fragment to connection {self.connection_id}: {e}")
                raise MessageDeliveryError(
                    details={"connection_id": self.connection_id, "original_error": str(e)}
                ) from e

            # Handle non-successful error codes
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
                logger.error(
                    f"Failed to send fragment to connection {self.connection_id}: {response}"
                )
                raise MessageDeliveryError(
                    details={"connection_id": self.connection_id, "response": response}
                )


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
