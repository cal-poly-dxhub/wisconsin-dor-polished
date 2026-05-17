import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
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
from step_function_types.models import FeedbackRequest, MessageEvent, MessageRequest

router = Router()
dynamodb = boto3.client("dynamodb")
session_table_name = os.environ["SESSIONS_TABLE_NAME"]
message_table_name = os.environ["MESSAGES_TABLE_NAME"]
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


def validate_feedback_request(body: dict[str, Any]) -> FeedbackRequest:
    """Validate the feedback request body and return the FeedbackRequest."""
    if not body:
        raise ValidationError(reason="Missing request body.")

    try:
        feedback_request = FeedbackRequest(**body)
        return feedback_request
    except pydantic.ValidationError as e:
        logger.error(f"Validation error in feedback request: {e}")
        reason = ""
        for error in e.errors():
            if "loc" in error and isinstance(error["loc"], tuple) and len(error["loc"]) > 0:
                field = error["loc"][0]
                reason += f"{field}: {error['msg']}; "
        raise ValidationError(reason=reason.strip()) from e
    except Exception as e:
        raise ValidationError() from e


def get_user_id_from_jwt() -> str:
    """Extract userId from Cognito JWT in request context."""
    try:
        claims = app.current_event.request_context.authorizer.jwt_claim
        user_id = claims.get("sub")
        if not user_id:
            raise ValidationError(reason="Missing user ID in JWT claims")
        return user_id
    except Exception as e:
        logger.error(f"Failed to extract user ID from JWT: {e}")
        raise ValidationError(reason="Invalid authentication token") from e


def create_session(user_id: str) -> str:
    """Create a new chat session; return the session ID."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    try:
        dynamodb.put_item(
            TableName=session_table_name,
            Item={
                "sessionId": {"S": session_id},
                "userId": {"S": user_id},
                "createdAt": {"S": now},
                "lastMessageAt": {"S": now},
            },
        )
    except Exception as e:
        logger.error(f"Failed to create session in DynamoDB: {e}")
        raise SessionCreationError(details={"session_id": session_id, "error": str(e)}) from e

    return session_id


@app.post("/session")
def create_session_handler() -> dict[str, Any]:
    """Create a new chat session."""
    try:
        user_id = get_user_id_from_jwt()
        session_id = create_session(user_id)
        return create_api_response(201, {"sessionId": session_id})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in create_session: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.get("/sessions")
def list_sessions_handler() -> dict[str, Any]:
    """List all sessions for the authenticated user, sorted by most recent."""
    try:
        user_id = get_user_id_from_jwt()

        response = dynamodb.query(
            TableName=session_table_name,
            IndexName="userIdIndex",
            KeyConditionExpression="userId = :uid",
            ExpressionAttributeValues={":uid": {"S": user_id}},
            ScanIndexForward=False,
            Limit=50,
        )

        sessions = []
        for item in response.get("Items", []):
            session_data = {
                "sessionId": item["sessionId"]["S"],
                "createdAt": item.get("createdAt", {}).get("S"),
                "lastMessageAt": item.get("lastMessageAt", {}).get("S"),
            }
            if "title" in item:
                session_data["title"] = item["title"]["S"]
            sessions.append(session_data)

        return create_api_response(200, {"sessions": sessions})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in list_sessions: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.patch("/session/<session_id>")
def update_session_handler(session_id: str) -> dict[str, Any]:
    """Update session metadata (e.g. title)."""
    try:
        user_id = get_user_id_from_jwt()

        session_response = dynamodb.get_item(
            TableName=session_table_name, Key={"sessionId": {"S": session_id}}
        )

        if "Item" not in session_response:
            raise SessionNotFoundError(session_id)

        session_user_id = session_response["Item"].get("userId", {}).get("S")
        if session_user_id != user_id:
            raise ValidationError(reason="Not authorized to update this session")

        body = app.current_event.json_body
        if not body:
            raise ValidationError(reason="Missing request body.")

        title = body.get("title")
        if title is None or not isinstance(title, str):
            raise ValidationError(reason="title must be a non-empty string.")

        title = title.strip()[:100]
        if not title:
            raise ValidationError(reason="title must be a non-empty string.")

        dynamodb.update_item(
            TableName=session_table_name,
            Key={"sessionId": {"S": session_id}},
            UpdateExpression="SET title = :title",
            ExpressionAttributeValues={":title": {"S": title}},
        )

        return create_api_response(200, {"message": "Session updated", "title": title})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in update_session: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.delete("/session/<session_id>")
def delete_session_handler(session_id: str) -> dict[str, Any]:
    """Delete a session and all its chat history."""
    try:
        user_id = get_user_id_from_jwt()

        # Verify session exists and belongs to user
        session_response = dynamodb.get_item(
            TableName=session_table_name, Key={"sessionId": {"S": session_id}}
        )

        if "Item" not in session_response:
            raise SessionNotFoundError(session_id)

        session_user_id = session_response["Item"].get("userId", {}).get("S")
        if session_user_id != user_id:
            raise ValidationError(reason="Not authorized to delete this session")

        # Delete all chat history for this session
        history_response = dynamodb.query(
            TableName=message_table_name,
            IndexName="sessionIdKey",
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": {"S": session_id}},
            ProjectionExpression="queryId",
        )

        for item in history_response.get("Items", []):
            query_id = item["queryId"]["S"]
            dynamodb.delete_item(TableName=message_table_name, Key={"queryId": {"S": query_id}})

        # Delete the session itself
        dynamodb.delete_item(TableName=session_table_name, Key={"sessionId": {"S": session_id}})

        return create_api_response(200, {"message": "Session deleted successfully"})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in delete_session: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


def update_query_feedback(session_id: str, feedback_request: FeedbackRequest):
    """Update the DynamoDB entry for a particular query's feedback."""
    try:
        dynamodb.update_item(
            TableName=message_table_name,
            Key={"queryId": {"S": feedback_request.query_id}},
            UpdateExpression="SET thumbUp = :thumbUp, feedback = :feedback",
            ExpressionAttributeValues={
                ":thumbUp": {"BOOL": feedback_request.thumb_up},
                ":feedback": {"S": feedback_request.feedback or ""},
            },
        )
    except Exception as e:
        logger.error(f"Failed to update query feedback in DynamoDB: {e}")
        raise DynamoDBError(
            "update_item",
            details={
                "session_id": session_id,
                "queryId": feedback_request.query_id,
                "error": str(e),
            },
        ) from e


@app.get("/session/<session_id>/history")
def get_session_history_handler(session_id: str) -> dict[str, Any]:
    """Get chat history for a session."""
    try:
        validate_session_exists(session_id)

        response = dynamodb.query(
            TableName=message_table_name,
            IndexName="sessionIdKey",
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": {"S": session_id}},
            ScanIndexForward=True,
        )

        messages = []
        for item in response.get("Items", []):
            message = {
                "queryId": item["queryId"]["S"],
                "query": item.get("query", {}).get("S", ""),
                "answer": item.get("answer", {}).get("S", ""),
                "timestamp": item.get("timestamp", {}).get("S"),
            }

            if "resources" in item:
                deserializer = TypeDeserializer()
                message["resources"] = deserializer.deserialize(item["resources"])

            messages.append(message)

        return create_api_response(200, {"messages": messages})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in get_session_history: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.post("/session/<session_id>/feedback")
def feedback_handler(session_id) -> dict[str, Any]:
    """Assign feedback to a particular query."""
    try:
        validate_session_exists(session_id)

        body = app.current_event.json_body
        feedback_request = validate_feedback_request(body)

        update_query_feedback(session_id, feedback_request)

        response_body = {
            "message": "Feedback assigned successfully",
            "queryId": feedback_request.query_id,
        }

        return create_api_response(200, response_body)

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in feedback_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


def update_session_timestamp(session_id: str) -> None:
    """Update the lastMessageAt timestamp for a session."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        dynamodb.update_item(
            TableName=session_table_name,
            Key={"sessionId": {"S": session_id}},
            UpdateExpression="SET lastMessageAt = :timestamp",
            ExpressionAttributeValues={":timestamp": {"S": now}},
        )
    except Exception as e:
        logger.error(f"Failed to update session timestamp: {e}")
        # Non-critical error, don't raise


def set_session_title_if_missing(session_id: str, message: str) -> None:
    """Set session title from the first message, only if no title exists yet."""
    title = message.strip()[:50]
    try:
        dynamodb.update_item(
            TableName=session_table_name,
            Key={"sessionId": {"S": session_id}},
            UpdateExpression="SET title = :title",
            ConditionExpression="attribute_not_exists(title)",
            ExpressionAttributeValues={":title": {"S": title}},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            pass
        else:
            logger.error(f"Failed to set session title: {e}")
    except Exception as e:
        logger.error(f"Failed to set session title: {e}")


@app.post("/session/<session_id>/message")
def send_message_handler(session_id: str) -> dict[str, Any]:
    """Process chat message and emit EventBridge event with session information"""
    query_id = str(uuid.uuid4())

    try:
        validate_session_exists(session_id)

        body = app.current_event.json_body
        message_request = validate_message_request(body)

        logger.info(f"Processing message with query_id {query_id} for session {session_id}")

        emit_message_event(session_id, message_request.message, query_id)
        update_session_timestamp(session_id)
        set_session_title_if_missing(session_id, message_request.message)

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
