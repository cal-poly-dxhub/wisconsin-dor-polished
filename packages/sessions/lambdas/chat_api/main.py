import json
import logging
import os
import uuid
from typing import Any

import boto3
import pydantic
from aws_lambda_powertools.event_handler.api_gateway import (
    APIGatewayHttpResolver,
    CORSConfig,
    Router,
)
from aws_lambda_powertools.utilities.typing import LambdaContext
from chat_api_errors import (
    ChatAPIError,
    DynamoDBError,
    EventBridgeError,
    SessionCreationError,
    SessionNotFoundError,
    ValidationError,
    create_error_body,
)
from step_function_types.models import MessageEvent, MessageRequest

router = Router()
dynamodb = boto3.client("dynamodb")
session_table_name = os.environ["SESSIONS_TABLE_NAME"]
eventbridge = boto3.client("events")

cors_config = CORSConfig(
    allow_origin="*",
    allow_headers=[
        "Content-Type",
        "Authorization",
    ],
    allow_credentials=True,
)

app = APIGatewayHttpResolver(cors=cors_config)
app.include_router(router)

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


def create_api_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized API response."""
    return {
        "statusCode": status_code,
        "body": json.dumps(body),
        "isBase64Encoded": False,
        "headers": {
            "Content-Type": "application/json",
        },
    }


def create_error_response(
    error: ChatAPIError, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a standardized error response."""
    return create_api_response(error.status_code, error.to_response(extra))


def emit_message_event(session_id: str, query: str, query_id: str):
    """Emit an EventBridge event to trigger chat message processing."""
    event = MessageEvent(query=query, query_id=query_id, session_id=session_id)
    logger.info(f"Emitting event: {event}")

    try:
        response = eventbridge.put_events(
            Entries=[
                {
                    "Source": "wisconsin-dor.chat-api",
                    "DetailType": "ChatMessageReceived",
                    "Detail": event.model_dump_json(),
                    "EventBusName": "default",
                }
            ]
        )
        logger.info(f"EventBridge response: {response}")

    except Exception as e:
        logger.error(f"Failed to emit EventBridge event: {e}")
        raise EventBridgeError(details={"original_error": str(e)}) from e

    if response["FailedEntryCount"] > 0:
        logger.error(f"Failed to emit event: {response['Entries']}")
        raise EventBridgeError(details={"response": response})


def validate_session_exists(session_id: str) -> None:
    """Validate that a session exists in DynamoDB."""
    try:
        response = dynamodb.get_item(
            TableName=session_table_name, Key={"sessionId": {"S": session_id}}
        )

    except Exception as e:
        logger.error(f"Error checking session existence: {e}")
        raise DynamoDBError("get_item", details={"session_id": session_id, "error": str(e)}) from e

    if "Item" not in response:
        raise SessionNotFoundError(session_id)


def validate_message_request(body: dict[str, Any]) -> MessageRequest:
    """Validate the message request body and return the MessageRequest."""
    if not body:
        raise ValidationError(reason="Missing request body.")

    try:
        message_request = MessageRequest(**body)
        return message_request
    except pydantic.ValidationError as e:
        logger.error(f"Validation error in message request: {e}")
        reason = ""
        for error in e.errors():
            if "loc" in error and isinstance(error["loc"], tuple) and len(error["loc"]) > 0:
                field = error["loc"][0]
                reason += f"{field}: {error['msg']}; "

        raise ValidationError(reason=reason.strip()) from e
    except Exception as e:
        raise ValidationError() from e


def create_session() -> str:
    """Create a new chat session; return the session ID."""
    session_id = str(uuid.uuid4())

    try:
        dynamodb.put_item(TableName=session_table_name, Item={"sessionId": {"S": session_id}})
    except Exception as e:
        logger.error(f"Failed to create session in DynamoDB: {e}")
        raise SessionCreationError(details={"session_id": session_id, "error": str(e)}) from e

    return session_id


@app.post("/session")
def create_session_handler() -> dict[str, Any]:
    """Create a new chat session."""
    try:
        session_id = create_session()
        return create_api_response(201, {"sessionId": session_id})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in create_session: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.post("/session/<session_id>/message")
def send_message_handler(session_id: str) -> dict[str, Any]:
    """Process chat message and emit EventBridge event with session information"""
    query_id = str(uuid.uuid4())

    try:
        validate_session_exists(session_id)

        body = router.current_event.json_body
        message_request = validate_message_request(body)

        logger.info(f"Processing message with query_id {query_id} for session {session_id}")

        emit_message_event(session_id, message_request.message, query_id)

        response_body = {
            "message": "Message received and processing started",
            "queryId": query_id,
        }

        return create_api_response(200, response_body)

    except ChatAPIError as e:
        response = create_error_response(e, {"query_id": query_id})
        logger.error(f"Error returned in send_message_handler: {response}", exc_info=True)
        return response
    except Exception as e:
        logger.error(f"Unexpected error in send_message: {e}", exc_info=True)
        error_response = create_error_body(e, {"query_id": query_id})
        return create_api_response(500, error_response)


def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Main Lambda handler function."""
    try:
        logger.info(f"Received event: {event}")
        response = app.resolve(event, context)
        return response
    except Exception as e:
        logger.error(f"Unhandled error in main handler: {e}", exc_info=True)
        error_response = create_error_body(e)
        return create_api_response(500, error_response)
