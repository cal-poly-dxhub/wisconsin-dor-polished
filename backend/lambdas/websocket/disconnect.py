import logging
import os

import boto3
from response_utils import create_error_response, create_websocket_response
from validators import validate_disconnect_event
from websocket_errors import WebSocketError

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

dynamodb = boto3.client("dynamodb")
table_name = os.environ.get("SESSIONS_TABLE_NAME")


def remove_connection_data(connection_id: str):
    """
    Clear the connection ID from sessions with the given connection ID.
    """

    # Allow errors to bubble

    response = dynamodb.query(
        TableName=table_name,
        IndexName="connectionId",
        KeyConditionExpression="connectionId = :cid",
        ExpressionAttributeValues={
            ":cid": {"S": connection_id},
        },
    )

    if not response.get("Items"):
        logger.warning(f"No session found with connection ID {connection_id}")
        return None

    for item in response.get("Items", []):
        session_id = item["sessionId"]["S"]
        # Update the session to remove the connectionId instead of deleting the session
        dynamodb.update_item(
            TableName=table_name,
            Key={"sessionId": {"S": session_id}},
            UpdateExpression="REMOVE connectionId",
            ConditionExpression="connectionId = :cid",
            ExpressionAttributeValues={
                ":cid": {"S": connection_id},
            },
        )
        logger.info(f"Successfully cleared connection ID {connection_id} from session {session_id}")
        return session_id


def handler(event, context):
    """
    Handle WebSocket disconnect events
    """
    try:
        validated_event = validate_disconnect_event(event)
        connection_id = validated_event.requestContext.connectionId
        remove_connection_data(connection_id)
        return create_websocket_response(200, {"message": "Disconnected"})

    except WebSocketError as e:
        logger.error(f"Error while processing disconnect event: {e}")
        return create_error_response(e)
