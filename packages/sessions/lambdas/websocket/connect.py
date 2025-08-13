import logging
import os
import time
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
from response_utils import create_error_response, create_websocket_response
from validators import validate_connect_event
from websocket_errors import SessionNotFound, WebSocketError, create_error_body

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

# Initialize DynamoDB client
dynamodb = boto3.client("dynamodb")
table_name = os.environ.get("SESSIONS_TABLE_NAME")


def record_session_data(session_id: str, connection_id: str):
    try:
        dynamodb.put_item(
            TableName=table_name,
            Item={
                "sessionId": {"S": session_id},
                "connectionId": {"S": connection_id},
                "timestamp": {"S": datetime.now().isoformat()},
                "ttl": {"N": str(int(time.time()) + 7200)},
            },
            ConditionExpression="attribute_exists(sessionId)",  # Ensure session exists
        )
        logger.info(
            f"Successfully updated session {session_id} with connection ID: {connection_id}"
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"DynamoDB error while updating session {session_id}: {error_code}")

        if error_code == "ConditionalCheckFailedException":
            raise SessionNotFound(session_id) from e
        else:
            raise e


def handler(event, context):
    """
    Handle WebSocket connect events
    """

    try:
        logger.info(f"Received connect event: {event}")
        validated_event = validate_connect_event(event)
        logger.info(
            f"Query string params of validated event: {validated_event.queryStringParameters}"
        )

        connection_id = validated_event.requestContext.connectionId
        session_id = validated_event.queryStringParameters.sessionId

        logger.info("Recording session data for connection")
        record_session_data(session_id, connection_id)

        logger.info(
            f"Connection established for session {session_id} with connection ID: {connection_id}"
        )
        return create_websocket_response(200, {"message": "Connected"})

    except WebSocketError as e:
        logger.error(f"Error while processing connect event: {e}")
        return create_error_response(e)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        body = create_error_body(e)
        return create_websocket_response(500, body)
