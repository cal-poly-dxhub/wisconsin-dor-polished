import json
import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
import pydantic
from aws_lambda_powertools.event_handler.api_gateway import (
    APIGatewayHttpResolver,
    CORSConfig,
    Router,
)
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from chat_api_errors import (
    ChatAPIError,
    DynamoDBError,
    EventBridgeError,
    ForbiddenError,
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


def _json_default(value: Any) -> Any:
    # boto3's TypeDeserializer hands back Decimal for any DDB number; json.dumps
    # rejects it. Whole values become int, fractions become float.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def create_api_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized API response."""
    return {
        "statusCode": status_code,
        "body": json.dumps(body, default=_json_default),
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


def emit_message_event(session_id: str, query: str, query_id: str, persona: str | None = None):
    """Emit an EventBridge event to trigger chat message processing."""
    event = MessageEvent(query=query, query_id=query_id, session_id=session_id, persona=persona)
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


def get_email_from_jwt() -> str | None:
    """Extract email from Cognito JWT claims (best-effort)."""
    try:
        claims = app.current_event.request_context.authorizer.jwt_claim
        return claims.get("email")
    except Exception:
        return None


def require_admin() -> None:
    """Raise 403 if the caller is not in the Admins Cognito group."""
    try:
        claims = app.current_event.request_context.authorizer.jwt_claim
        groups = claims.get("cognito:groups", [])
        if "Admins" not in groups:
            raise ForbiddenError()
    except ForbiddenError:
        raise
    except Exception as e:
        logger.error(f"Failed to check admin group membership: {e}")
        raise ForbiddenError() from e


def create_session(user_id: str, email: str | None = None) -> str:
    """Create a new chat session; return the session ID."""
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    item: dict[str, dict[str, str]] = {
        "sessionId": {"S": session_id},
        "userId": {"S": user_id},
        "createdAt": {"S": now},
        "lastMessageAt": {"S": now},
    }
    if email:
        item["email"] = {"S": email}

    try:
        dynamodb.put_item(TableName=session_table_name, Item=item)
    except Exception as e:
        logger.error(f"Failed to create session in DynamoDB: {e}")
        raise SessionCreationError(details={"session_id": session_id, "error": str(e)}) from e

    return session_id


@app.post("/session")
def create_session_handler() -> dict[str, Any]:
    """Create a new chat session."""
    try:
        user_id = get_user_id_from_jwt()
        email = get_email_from_jwt()
        session_id = create_session(user_id, email=email)
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


def _resolve_session_emails(session_ids: set[str]) -> dict[str, str]:
    """Batch-fetch email addresses from the Sessions table for a set of session IDs."""
    if not session_ids:
        return {}
    email_map: dict[str, str] = {}
    batch_keys = [{"sessionId": {"S": sid}} for sid in session_ids]
    for i in range(0, len(batch_keys), 100):
        chunk = batch_keys[i : i + 100]
        try:
            resp = dynamodb.batch_get_item(
                RequestItems={
                    session_table_name: {
                        "Keys": chunk,
                        "ProjectionExpression": "sessionId, email",
                    }
                }
            )
            for item in resp.get("Responses", {}).get(session_table_name, []):
                sid = item.get("sessionId", {}).get("S")
                email = item.get("email", {}).get("S")
                if sid and email:
                    email_map[sid] = email
        except Exception:
            logger.warning("Failed to batch-resolve session emails", exc_info=True)
    return email_map


@app.get("/admin/activity")
def activity_handler() -> dict[str, Any]:
    """Return paginated chat history items for the admin activity dashboard.

    Query params:
      - limit: page size (default 50, max 200)
      - cursor: opaque pagination token (base64-encoded LastEvaluatedKey)
      - after: ISO timestamp lower bound (inclusive)
      - before: ISO timestamp upper bound (exclusive)
      - feedback: 'up' | 'down' | 'rated' | 'unrated' (server-side filter)
    """
    import base64

    try:
        require_admin()
        params = app.current_event.query_string_parameters or {}
        limit = min(int(params.get("limit", "50")), 200)
        cursor = params.get("cursor")
        after = params.get("after")
        before = params.get("before")
        feedback_filter = params.get("feedback")

        key_condition = "gsi1pk = :pk"
        expr_values: dict[str, Any] = {":pk": {"S": "ALL"}}

        if after and before:
            key_condition += " AND #ts BETWEEN :after AND :before"
            expr_values[":after"] = {"S": after}
            expr_values[":before"] = {"S": before}
        elif after:
            key_condition += " AND #ts >= :after"
            expr_values[":after"] = {"S": after}
        elif before:
            key_condition += " AND #ts < :before"
            expr_values[":before"] = {"S": before}

        filter_expression = None
        if feedback_filter == "up":
            filter_expression = "thumbUp = :fv"
            expr_values[":fv"] = {"BOOL": True}
        elif feedback_filter == "down":
            filter_expression = "thumbUp = :fv"
            expr_values[":fv"] = {"BOOL": False}
        elif feedback_filter == "rated":
            filter_expression = "attribute_exists(thumbUp)"
        elif feedback_filter == "unrated":
            filter_expression = "attribute_not_exists(thumbUp)"

        query_kwargs: dict[str, Any] = {
            "TableName": message_table_name,
            "IndexName": "timestampIndex",
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeValues": expr_values,
            "ExpressionAttributeNames": {"#ts": "timestamp"},
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if filter_expression:
            query_kwargs["FilterExpression"] = filter_expression
        if cursor:
            query_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())

        response = dynamodb.query(**query_kwargs)

        deserializer = TypeDeserializer()
        results = []
        session_ids: set[str] = set()
        for item in response.get("Items", []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            trace_raw = deserialized.get("trace")
            trace = None
            if trace_raw and isinstance(trace_raw, str):
                try:
                    trace = json.loads(trace_raw)
                except (json.JSONDecodeError, TypeError):
                    trace = None
            elif isinstance(trace_raw, list):
                trace = trace_raw
            sid = deserialized.get("sessionId", "")
            if sid:
                session_ids.add(sid)
            results.append(
                {
                    "queryId": deserialized.get("queryId", ""),
                    "sessionId": sid,
                    "query": deserialized.get("query", ""),
                    "answer": deserialized.get("answer", ""),
                    "timestamp": deserialized.get("timestamp", ""),
                    "thumbUp": deserialized.get("thumbUp"),
                    "feedback": deserialized.get("feedback"),
                    "trace": trace,
                }
            )

        email_by_session = _resolve_session_emails(session_ids)
        for result in results:
            result["email"] = email_by_session.get(result["sessionId"])

        next_cursor = None
        if "LastEvaluatedKey" in response:
            next_cursor = base64.b64encode(
                json.dumps(response["LastEvaluatedKey"]).encode()
            ).decode()

        return create_api_response(
            200,
            {
                "items": results,
                "count": len(results),
                "nextCursor": next_cursor,
            },
        )

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in activity_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.get("/admin/activity/<query_id>")
def activity_detail_handler(query_id: str) -> dict[str, Any]:
    """Return a single chat history item by queryId."""
    try:
        require_admin()
        response = dynamodb.get_item(
            TableName=message_table_name,
            Key={"queryId": {"S": query_id}},
        )
        raw_item = response.get("Item")
        if not raw_item:
            return create_api_response(404, {"error": "Query not found"})

        deserializer = TypeDeserializer()
        deserialized = {k: deserializer.deserialize(v) for k, v in raw_item.items()}
        trace_raw = deserialized.get("trace")
        trace = None
        if trace_raw and isinstance(trace_raw, str):
            try:
                trace = json.loads(trace_raw)
            except (json.JSONDecodeError, TypeError):
                trace = None
        elif isinstance(trace_raw, list):
            trace = trace_raw

        sid = deserialized.get("sessionId", "")
        email_by_session = _resolve_session_emails({sid} if sid else set())

        result = {
            "queryId": deserialized.get("queryId", ""),
            "sessionId": sid,
            "query": deserialized.get("query", ""),
            "answer": deserialized.get("answer", ""),
            "timestamp": deserialized.get("timestamp", ""),
            "thumbUp": deserialized.get("thumbUp"),
            "feedback": deserialized.get("feedback"),
            "trace": trace,
            "email": email_by_session.get(sid),
        }
        return create_api_response(200, {"item": result})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in activity_detail_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.get("/admin/chunks/documents")
def chunks_documents_handler() -> dict[str, Any]:
    """List all documents that have been extracted (exist in work bucket)."""
    try:
        require_admin()
        work_bucket = os.environ.get("WORK_BUCKET_NAME", "")
        if not work_bucket:
            raise ValidationError(reason="WORK_BUCKET_NAME not configured.")

        s3 = boto3.client("s3")
        documents = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=work_bucket, Prefix="extracted/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json"):
                    doc_id = key.removeprefix("extracted/").removesuffix(".json")
                    documents.append(
                        {
                            "doc_id": doc_id,
                            "last_modified": obj["LastModified"].isoformat(),
                            "size_bytes": obj["Size"],
                        }
                    )

        documents.sort(key=lambda d: d["doc_id"])
        return create_api_response(200, {"documents": documents, "count": len(documents)})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in chunks_documents_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


@app.get("/admin/chunks/<docId>")
def chunks_detail_handler(docId: str) -> dict[str, Any]:
    """Return all chunks for a given document from the work bucket."""
    try:
        require_admin()
        work_bucket = os.environ.get("WORK_BUCKET_NAME", "")
        if not work_bucket:
            raise ValidationError(reason="WORK_BUCKET_NAME not configured.")

        s3 = boto3.client("s3")
        key = f"extracted/{docId}.json"
        try:
            obj = s3.get_object(Bucket=work_bucket, Key=key)
        except s3.exceptions.NoSuchKey:
            return create_api_response(404, {"error": f"Document not found: {docId}"})
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return create_api_response(404, {"error": f"Document not found: {docId}"})
            raise

        data = json.loads(obj["Body"].read().decode("utf-8"))
        chunks = data.get("chunks", [])

        result_chunks = []
        for i, chunk in enumerate(chunks):
            meta = chunk.get("metadata", {})
            result_chunks.append(
                {
                    "chunk_id": chunk.get("chunk_id", f"{docId}_chunk_{i:04d}"),
                    "text": chunk.get("text", ""),
                    "char_count": len(chunk.get("text", "")),
                    "idx": meta.get("chunk_index", i),
                    "heading": meta.get("heading") or None,
                    "subheading": meta.get("subheading") or None,
                    "start_page": meta.get("start_page"),
                    "end_page": meta.get("end_page"),
                    "s3_key": meta.get("source"),
                    "statute_refs": meta.get("statute_refs", []),
                    "admin_rule_refs": meta.get("admin_rule_refs", []),
                    "edition_year": meta.get("edition_year"),
                }
            )

        doc_meta = {
            "doc_id": docId,
            "title": data.get("title") or None,
            "doc_type": data.get("doc_type") or None,
            "framework_id": data.get("framework_id") or None,
            "authority_level": data.get("authority_level"),
            "source_url": data.get("source_url") or None,
            "chunk_count": len(result_chunks),
            "total_chars": sum(c["char_count"] for c in result_chunks),
            "max_chunk_chars": max((c["char_count"] for c in result_chunks), default=0),
            "min_chunk_chars": min((c["char_count"] for c in result_chunks), default=0),
        }

        return create_api_response(200, {"document": doc_meta, "chunks": result_chunks})

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in chunks_detail_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


INGEST_CATEGORIES = {
    "constitution": {
        "framework_id": "FW-WI-CONST",
        "authority_level": 1,
        "doc_type": "constitution",
    },
    "statutes": {"framework_id": "FW-WI-STAT", "authority_level": 2, "doc_type": "statute"},
    "admin_rules": {"framework_id": "FW-WI-ADMIN", "authority_level": 4, "doc_type": "admin_rule"},
    "wpam": {"framework_id": "FW-WPAM", "authority_level": 5, "doc_type": "manual"},
    "faq_pages": {"framework_id": "FW-WI-DOR", "authority_level": 6, "doc_type": "faq"},
    "gov_publications": {"framework_id": "FW-WI-DOR", "authority_level": 7, "doc_type": "guide"},
    "news_pages": {"framework_id": "FW-WI-DOR", "authority_level": 7, "doc_type": "advisory"},
    "complex_inquiry_pages": {
        "framework_id": "FW-WI-DOR",
        "authority_level": 7,
        "doc_type": "advisory",
    },
}


@app.post("/admin/ingest")
def ingest_handler() -> dict[str, Any]:
    """Download documents from URLs, upload to S3, and launch Fargate ingestion.

    Body:
      - urls: list of URL strings (1-10)
      - category: document category key
      - title_override: optional title string
    """

    try:
        require_admin()

        body = app.current_event.json_body
        if not body:
            raise ValidationError(reason="Missing request body.")

        urls = body.get("urls", [])
        category = body.get("category", "")
        title_override = body.get("title_override")

        if not urls or not isinstance(urls, list):
            raise ValidationError(reason="urls must be a non-empty list.")
        if len(urls) > 10:
            raise ValidationError(reason="Maximum 10 URLs per request.")
        if category not in INGEST_CATEGORIES:
            raise ValidationError(reason=f"Invalid category: {category}")

        for url in urls:
            if not isinstance(url, str) or not url.startswith("http"):
                raise ValidationError(reason=f"Invalid URL: {url}")

        s3 = boto3.client("s3")
        raw_bucket = os.environ.get("RAW_BUCKET_NAME", "")
        if not raw_bucket:
            raise ValidationError(reason="Ingestion not configured (RAW_BUCKET_NAME missing).")

        cat_meta = INGEST_CATEGORIES[category]
        results = []
        uploaded_doc_ids: list[str] = []

        for url in urls:
            try:
                doc_id = _make_doc_id(category, url)
                content_bytes, content_type = _download_url(url)

                ext = ".pdf" if "pdf" in content_type else ".txt"
                doc_key = f"raw/{doc_id}/{doc_id}{ext}"
                meta_key = f"raw/{doc_id}/{doc_id}{ext}.metadata.json"

                metadata = {
                    "doc_id": doc_id,
                    "source_url": url,
                    "doc_type": cat_meta["doc_type"],
                    "framework_id": cat_meta["framework_id"],
                    "authority_level": cat_meta["authority_level"],
                    "category": category,
                }
                if title_override:
                    metadata["title"] = title_override

                s3.put_object(
                    Bucket=raw_bucket,
                    Key=doc_key,
                    Body=content_bytes,
                    ContentType=content_type,
                )
                s3.put_object(
                    Bucket=raw_bucket,
                    Key=meta_key,
                    Body=json.dumps({"metadataAttributes": metadata}),
                    ContentType="application/json",
                )

                uploaded_doc_ids.append(doc_id)
                results.append(
                    {
                        "status": "success",
                        "url": url,
                        "doc_id": doc_id,
                        "size_bytes": len(content_bytes),
                    }
                )
                logger.info(f"Uploaded to S3: {doc_key} ({len(content_bytes)} bytes)")

            except Exception as e:
                logger.error(f"Failed to download/upload {url}: {e}")
                results.append({"status": "failed", "url": url, "error": str(e)})

        task_arn = None
        if uploaded_doc_ids:
            task_arn = _launch_ingestion_task(uploaded_doc_ids)

        return create_api_response(
            200,
            {
                "results": results,
                "task_arn": task_arn,
            },
        )

    except ChatAPIError as e:
        return create_api_response(e.status_code, e.to_response())
    except Exception as e:
        logger.error(f"Unexpected error in ingest_handler: {e}")
        error_response = create_error_body(e)
        return create_api_response(500, error_response)


def _download_url(url: str) -> tuple[bytes, str]:
    """Download a URL and return (content_bytes, content_type)."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "WI-DOR-Bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        content_bytes = resp.read()
    return content_bytes, content_type


def _launch_ingestion_task(doc_ids: list[str]) -> str | None:
    """Launch a Fargate task to process uploaded documents through extract→embed→load."""
    cluster_arn = os.environ.get("INGESTION_CLUSTER_ARN", "")
    task_def_arn = os.environ.get("INGESTION_TASK_DEF_ARN", "")
    subnet_ids = os.environ.get("INGESTION_SUBNET_IDS", "")
    security_group_id = os.environ.get("INGESTION_SECURITY_GROUP_ID", "")

    if not all([cluster_arn, task_def_arn, subnet_ids, security_group_id]):
        logger.warning("Ingestion ECS config missing — skipping Fargate launch")
        return None

    source_filter = _common_prefix(doc_ids)
    if not source_filter:
        source_filter = doc_ids[0]

    ecs = boto3.client("ecs")
    subnets = [s.strip() for s in subnet_ids.split(",") if s.strip()]

    env_overrides = [
        {"name": "PHASE", "value": "full"},
        {"name": "SOURCE_FILTER", "value": source_filter},
        {"name": "FORCE", "value": "true"},
    ]

    try:
        response = ecs.run_task(
            cluster=cluster_arn,
            taskDefinition=task_def_arn,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": [security_group_id],
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "ingestion",
                        "environment": env_overrides,
                    }
                ]
            },
        )
        task_arn = response["tasks"][0]["taskArn"] if response.get("tasks") else None
        logger.info(f"Launched ingestion task: {task_arn} (filter={source_filter})")
        return task_arn
    except Exception as e:
        logger.error(f"Failed to launch Fargate task: {e}")
        return None


def _common_prefix(strings: list[str]) -> str:
    """Find the longest common prefix of a list of strings."""
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _make_doc_id(category: str, url: str) -> str:
    """Derive a stable document ID from category + URL (mirrors scrape_documents.py logic)."""
    import re
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    stem = path.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[^.]+$", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")

    if stem in ("home", "index", "main", "default"):
        parent = path.rstrip("/").rsplit("/", 2)
        if len(parent) >= 2:
            parent_name = re.sub(r"[^a-z0-9]+", "-", parent[-2].lower()).strip("-")
            stem = f"{parent_name}-{stem}" if parent_name else stem

    return f"{category}-{stem}"


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
    now = datetime.now(UTC).isoformat()
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

        emit_message_event(session_id, message_request.message, query_id, message_request.persona)
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
