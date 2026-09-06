import json
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("SESSIONS_TABLE_NAME", "test-sessions")
os.environ.setdefault("MESSAGES_TABLE_NAME", "test-messages")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "layers"))
sys.path.insert(0, os.path.dirname(__file__))


from aws_lambda_powertools.event_handler.api_gateway import BaseRouter
from botocore.exceptions import ClientError

with patch.dict(os.environ, {"SESSIONS_TABLE_NAME": "test-sessions-table", "LOG_LEVEL": "INFO"}):
    import main as chat_api_main
    from main import (
        activity_handler,
        create_session_handler,
        feedback_handler,
        handler,
        send_message_handler,
    )


# Capture chat_api's `main` module object at import time and patch attributes on
# THIS reference, not the string "main.dynamodb". Several lambdas share the
# module name "main"; agentic_retrieval's test suite reassigns
# sys.modules["main"] to its own module at runtime, so a string patch target
# like @patch_dynamodb() would resolve to the wrong module when the full
# suite runs and fail with "module 'main' ... does not have the attribute
# 'dynamodb'". patch.object against the captured module is immune to that swap.
def patch_dynamodb():
    return patch.object(chat_api_main, "dynamodb")


def patch_eventbridge():
    return patch.object(chat_api_main, "eventbridge")


def patch_create_session():
    return patch.object(chat_api_main, "create_session")


def _set_current_event(json_body=None, claims=None, query_params=None):
    """Point the resolver's current_event at a mock the handlers can read.

    Handlers pull the request body from ``app.current_event.json_body`` and the
    Cognito identity from ``app.current_event.request_context.authorizer.jwt_claim``.
    When a handler is called directly (not via ``app.resolve``) that attribute is
    whatever the last resolve left behind, so each test that exercises a handler
    directly must seed it.

    We assign to ``BaseRouter.current_event`` — the *class* attribute that both
    ``app`` and ``router`` read through and that ``app.resolve()`` overwrites on
    every request. Setting an instance attribute on ``app`` instead would shadow
    the class attribute and leak a stale mock into the route-level test (which
    goes through ``app.resolve``).
    """
    event = MagicMock()
    event.json_body = json_body
    event.query_string_parameters = query_params or {}
    event.request_context.authorizer.jwt_claim = (
        claims if claims is not None else {"sub": "test-user", "email": "user@example.com"}
    )
    BaseRouter.current_event = event
    return event


@patch_dynamodb()
def test_create_session_success(mock_dynamodb):
    """Test happy path for create_session_handler.

    Validates:
    - Session is inserted into DynamoDB with the full item (id, user, timestamps)
    - Response has correct status code and structure
    """

    mock_dynamodb.put_item.return_value = {}
    _set_current_event(claims={"sub": "test-user", "email": "user@example.com"})

    # Call the handler directly
    response = create_session_handler()

    assert response["statusCode"] == 201
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "sessionId" in response_body
    session_id = response_body["sessionId"]

    # create_session writes sessionId + userId + timestamps (+ email when present).
    # Timestamps are wall-clock, so assert on structure rather than exact equality.
    mock_dynamodb.put_item.assert_called_once()
    put_kwargs = mock_dynamodb.put_item.call_args.kwargs
    assert put_kwargs["TableName"] == "test-sessions-table"
    item = put_kwargs["Item"]
    assert item["sessionId"] == {"S": session_id}
    assert item["userId"] == {"S": "test-user"}
    assert item["email"] == {"S": "user@example.com"}
    assert "createdAt" in item and "S" in item["createdAt"]
    assert "lastMessageAt" in item and "S" in item["lastMessageAt"]

    assert response["headers"]["Content-Type"] == "application/json"


@patch_dynamodb()
def test_create_session_unexpected_error(mock_dynamodb):
    """Test create_session_handler with unexpected error during DynamoDB operation.

    Validates:
    - Unexpected errors are handled correctly
    - Response has 500 status code
    - Any error message is returned
    """

    # Mock DynamoDB to raise an unexpected error during put_item.
    mock_dynamodb.put_item.side_effect = RuntimeError("Unexpected DynamoDB error")
    _set_current_event()

    # Call the handler directly
    response = create_session_handler()

    # Verify error response structure
    assert response["statusCode"] == 500
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "message" in response_body["error"]

    mock_dynamodb.put_item.assert_called_once()


@patch_eventbridge()
@patch_dynamodb()
def test_send_message_success(mock_dynamodb, mock_eventbridge):
    """Test happy path for send_message_handler.

    Validates:
    - Session existence is validated
    - EventBridge event is emitted with correct structure
    - Response includes queryId
    """

    # Mock DynamoDB response for session validation
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}

    # Mock EventBridge response
    mock_eventbridge.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

    # Provide the request body via the resolver's current_event.
    _set_current_event(json_body={"message": "Hello, how can I help you?"})

    # Call the handler directly
    response = send_message_handler("test-session-id")

    # Verify response structure
    assert response["statusCode"] == 200
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "queryId" in response_body
    assert "message" in response_body
    assert response_body["message"] == "Message received and processing started"

    # Verify session validation was called
    mock_dynamodb.get_item.assert_called_once_with(
        TableName="test-sessions-table", Key={"sessionId": {"S": "test-session-id"}}
    )

    # Verify EventBridge event was emitted
    mock_eventbridge.put_events.assert_called_once()
    call_args = mock_eventbridge.put_events.call_args[1]
    entries = call_args["Entries"]

    assert len(entries) == 1
    event_entry = entries[0]
    assert event_entry["Source"] == "wisconsin-dor.chat-api"
    assert event_entry["DetailType"] == "ChatMessageReceived"
    assert event_entry["EventBusName"] == "default"

    # Parse and validate the event detail
    event_detail = json.loads(event_entry["Detail"])
    assert "query" in event_detail
    assert "query_id" in event_detail
    assert "session_id" in event_detail
    assert event_detail["query"] == "Hello, how can I help you?"
    assert event_detail["session_id"] == "test-session-id"

    # Verify the query_id in the emitted event matches the response queryId
    assert event_detail["query_id"] == response_body["queryId"]

    assert response["headers"]["Content-Type"] == "application/json"


@patch_eventbridge()
@patch_dynamodb()
def test_send_message_writes_pending_row(mock_dynamodb, mock_eventbridge):
    """send_message_handler persists a pending message row before the event.

    A page reload immediately after send must be able to restore the in-flight
    query, so the row (query text, empty answer) is written synchronously,
    keyed by the same queryId returned to the caller and later overwritten by
    the async worker.
    """
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}
    mock_eventbridge.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}
    _set_current_event(json_body={"message": "How is my home assessed?"})

    response = send_message_handler("test-session-id")

    assert response["statusCode"] == 200
    query_id = json.loads(response["body"])["queryId"]

    # The pending row is the only write to the messages table.
    pending_puts = [
        c
        for c in mock_dynamodb.put_item.call_args_list
        if c.kwargs.get("TableName") == "test-messages"
    ]
    assert len(pending_puts) == 1
    item = pending_puts[0].kwargs["Item"]
    assert item["queryId"] == {"S": query_id}
    assert item["sessionId"] == {"S": "test-session-id"}
    assert item["query"] == {"S": "How is my home assessed?"}
    assert item["answer"] == {"S": ""}
    assert item["gsi1pk"] == {"S": "ALL"}
    assert "timestamp" in item


@patch_dynamodb()
def test_send_message_invalid_request(mock_dynamodb):
    """Test send_message_handler with invalid MessageRequest.

    Validates:
    - ValidationError is handled for invalid request structure
    - Response has 400 status code
    - Query ID is included in error response
    """

    # Mock DynamoDB response for session validation
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}

    # Body missing the required 'message' field.
    _set_current_event(json_body={"invalid_field": "some value"})

    # Call the handler directly
    response = send_message_handler("test-session-id")

    # Verify error response structure
    assert response["statusCode"] == 400
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "query_id" in response_body["error"]
    assert "Invalid request" in response_body["error"]["message"]

    # Verify session validation was called
    mock_dynamodb.get_item.assert_called_once_with(
        TableName="test-sessions-table", Key={"sessionId": {"S": "test-session-id"}}
    )


@patch_dynamodb()
def test_send_message_session_not_found(mock_dynamodb):
    """Test send_message_handler with non-existent session.

    Validates:
    - SessionNotFoundError is handled correctly
    - Response has 404 status code
    - Query ID is included in error response
    - An error message is returned
    """

    # Mock DynamoDB response for session validation (no Item returned)
    mock_dynamodb.get_item.return_value = {}

    # Body is present but never reached — session validation fails first.
    _set_current_event(json_body={"message": "Hello, how can I help you?"})

    # Call the handler directly
    response = send_message_handler("nonexistent-session-id")

    # Verify error response structure
    assert response["statusCode"] == 404
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "query_id" in response_body["error"]
    assert "Could not find session" in response_body["error"]["message"]

    # Verify session validation was called
    mock_dynamodb.get_item.assert_called_once_with(
        TableName="test-sessions-table", Key={"sessionId": {"S": "nonexistent-session-id"}}
    )


@patch_eventbridge()
@patch_dynamodb()
def test_send_message_eventbridge_error(mock_dynamodb, mock_eventbridge):
    """Test send_message_handler with EventBridge error.

    Validates:
    - EventBridge errors are handled correctly
    - Response has 500 status code
    - Query ID is included in error response
    - An error message is returned
    """

    # Mock DynamoDB response for session validation
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}

    # Mock EventBridge to raise an error
    mock_eventbridge.put_events.side_effect = RuntimeError("AWS EventBridge service error")

    # Provide a valid request body.
    _set_current_event(json_body={"message": "Hello, how can I help you?"})

    # Call the handler directly
    response = send_message_handler("test-session-id")

    # Verify error response structure
    assert response["statusCode"] == 500
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "query_id" in response_body["error"]
    # Ensure the error message actually exists
    assert "message" in response_body["error"]

    # Verify session validation was called
    mock_dynamodb.get_item.assert_called_once_with(
        TableName="test-sessions-table", Key={"sessionId": {"S": "test-session-id"}}
    )

    # Verify EventBridge was attempted
    mock_eventbridge.put_events.assert_called_once()


@patch_create_session()
def test_session_route_calls_create_session(mock_create_session):
    """Test that invoking the session/ route calls the create_session function."""

    # Mock the create_session function to return a test session ID
    mock_create_session.return_value = "test-session-id"

    # API Gateway v2 event structure for a POST to /session. The route handler
    # reads the Cognito 'sub' claim before creating the session, so the event
    # must carry a JWT authorizer context or get_user_id_from_jwt raises 400.
    test_event = {
        "version": "2.0",
        "routeKey": "POST /session",
        "rawPath": "/dev/session",
        "rawQueryString": "",
        "headers": {
            "Content-Type": "application/json",
        },
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/session",
            },
            "authorizer": {
                "jwt": {
                    "claims": {"sub": "test-user", "email": "user@example.com"},
                    "scopes": [],
                }
            },
            "requestId": "test-request-id",
            "stage": "dev",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    # Mock context
    mock_context = MagicMock()

    # Call the main handler with the test event
    handler(test_event, mock_context)

    # Assert that create_session was called once (the resolver wraps the
    # handler's return value, so we assert on the side effect, not the status).
    mock_create_session.assert_called_once()


def _activity_ddb_item(query_id="query-1", session_id="session-1", thumb_up=None):
    item = {
        "queryId": {"S": query_id},
        "sessionId": {"S": session_id},
        "query": {"S": "How is agricultural land assessed?"},
        "timestamp": {"S": "2026-07-21T20:00:00+00:00"},
        "feedback": {"S": "Useful response"},
        # These large detail attributes must never be returned by the list API.
        "answer": {"S": "Long answer"},
        "trace": {"S": "[]"},
        "resources": {"L": []},
    }
    if thumb_up is not None:
        item["thumbUp"] = {"BOOL": thumb_up}
    return item


@patch_dynamodb()
def test_activity_list_returns_lean_projected_summaries(mock_dynamodb):
    mock_dynamodb.query.return_value = {
        "Items": [_activity_ddb_item(thumb_up=True)],
        "ScannedCount": 1,
        "ConsumedCapacity": {"CapacityUnits": 0.5},
    }
    mock_dynamodb.batch_get_item.return_value = {
        "Responses": {
            "test-sessions-table": [
                {
                    "sessionId": {"S": "session-1"},
                    "email": {"S": "admin@example.com"},
                }
            ]
        }
    }
    _set_current_event(
        claims={"cognito:groups": ["Admins"]},
        query_params={"limit": "25", "after": "2026-07-01T00:00:00+00:00"},
    )

    response = activity_handler()

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["count"] == 1
    assert body["items"] == [
        {
            "queryId": "query-1",
            "sessionId": "session-1",
            "query": "How is agricultural land assessed?",
            "timestamp": "2026-07-21T20:00:00+00:00",
            "thumbUp": True,
            "rating": None,
            "feedback": "Useful response",
            "email": "admin@example.com",
        }
    ]

    query_kwargs = mock_dynamodb.query.call_args.kwargs
    assert query_kwargs["IndexName"] == "activityIndexV2"
    assert query_kwargs["Limit"] == 25
    assert query_kwargs["ProjectionExpression"] == (
        "queryId, sessionId, #q, #ts, thumbUp, feedback, #rating"
    )
    assert query_kwargs["ReturnConsumedCapacity"] == "INDEXES"


@patch_dynamodb()
def test_activity_feedback_filter_fills_sparse_page(mock_dynamodb):
    first_cursor = {
        "queryId": {"S": "skipped-query"},
        "gsi1pk": {"S": "ALL"},
        "timestamp": {"S": "2026-07-21T20:01:00+00:00"},
    }
    mock_dynamodb.query.side_effect = [
        {"Items": [], "ScannedCount": 1, "LastEvaluatedKey": first_cursor},
        {"Items": [_activity_ddb_item(thumb_up=False)], "ScannedCount": 1},
    ]
    mock_dynamodb.batch_get_item.return_value = {"Responses": {"test-sessions-table": []}}
    _set_current_event(
        claims={"cognito:groups": ["Admins"]},
        query_params={"limit": "1", "feedback": "down"},
    )

    response = activity_handler()

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["count"] == 1
    assert body["items"][0]["thumbUp"] is False
    assert mock_dynamodb.query.call_count == 2
    assert mock_dynamodb.query.call_args_list[0].kwargs["FilterExpression"] == "#rating = :rv"
    assert mock_dynamodb.query.call_args_list[0].kwargs["ExpressionAttributeValues"][":rv"] == {
        "S": "down"
    }
    assert mock_dynamodb.query.call_args_list[1].kwargs["ExclusiveStartKey"] == first_cursor


@patch_dynamodb()
def test_activity_list_falls_back_during_index_rollout(mock_dynamodb):
    missing_index = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "Index not active"}},
        "Query",
    )
    mock_dynamodb.query.side_effect = [missing_index, {"Items": [], "ScannedCount": 0}]
    _set_current_event(claims={"cognito:groups": ["Admins"]})

    response = activity_handler()

    assert response["statusCode"] == 200
    assert mock_dynamodb.query.call_count == 2
    assert mock_dynamodb.query.call_args_list[0].kwargs["IndexName"] == "activityIndexV2"
    assert mock_dynamodb.query.call_args_list[1].kwargs["IndexName"] == "timestampIndex"


@patch_dynamodb()
def test_feedback_legacy_payload_writes_only_scalars(mock_dynamodb):
    """A plain thumbUp/feedback payload must not write richFeedback."""
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "s-1"}}}
    _set_current_event(
        json_body={"queryId": "q-1", "thumbUp": True, "feedback": "great"}
    )

    response = feedback_handler("s-1")

    assert response["statusCode"] == 200
    update_kwargs = mock_dynamodb.update_item.call_args.kwargs
    assert update_kwargs["UpdateExpression"] == (
        "SET thumbUp = :thumbUp, feedback = :feedback, #rating = :rating"
    )
    assert update_kwargs["ExpressionAttributeNames"] == {"#rating": "rating"}
    values = update_kwargs["ExpressionAttributeValues"]
    assert values[":thumbUp"] == {"BOOL": True}
    assert values[":feedback"] == {"S": "great"}
    # thumb-only payload derives the rating from the boolean.
    assert values[":rating"] == {"S": "up"}
    assert ":richFeedback" not in values


@patch_dynamodb()
def test_feedback_rich_payload_writes_derived_thumb_and_map(mock_dynamodb):
    """A rich payload derives thumbUp from rating and stores a richFeedback map."""
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "s-1"}}}
    _set_current_event(
        json_body={
            "queryId": "q-2",
            "thumbUp": False,  # mid → false, sent by the client
            "richFeedback": {
                "rating": "mid",
                "positiveComment": "",
                "response": {"relevance": {"answer": "no", "comment": "off topic"}},
                "sourcesOk": "no",
                "sourceNotes": [
                    {
                        "id": "n1",
                        "sourceId": "doc-x",
                        "citedFully": "no",
                        "missedDetail": "stat 70.32",
                        "comment": "",
                    }
                ],
                "linksWork": "yes",
                "brokenLinkIds": [],
                "brokenLinksReason": "",
                "annotations": [
                    {
                        "id": "a1",
                        "startOffset": 10,
                        "endOffset": 20,
                        "quote": "dark store",
                        "comment": "wrong",
                    }
                ],
                "speedTimely": "no",
                "speedComment": "took 100s",
            },
        }
    )

    response = feedback_handler("s-1")

    assert response["statusCode"] == 200
    update_kwargs = mock_dynamodb.update_item.call_args.kwargs
    assert "richFeedback = :richFeedback" in update_kwargs["UpdateExpression"]
    assert "feedbackSubmittedAt = :submittedAt" in update_kwargs["UpdateExpression"]
    values = update_kwargs["ExpressionAttributeValues"]
    assert values[":thumbUp"] == {"BOOL": False}
    # The middle rating is preserved as a first-class scalar (not collapsed to
    # thumbs-down like the boolean).
    assert values[":rating"] == {"S": "mid"}
    # richFeedback is a DynamoDB map with camelCase keys preserved.
    rich = values[":richFeedback"]
    assert "M" in rich
    rich_map = rich["M"]
    assert rich_map["rating"] == {"S": "mid"}
    assert "sourceNotes" in rich_map and "speedTimely" in rich_map
    # Nested list of maps survives serialization.
    note = rich_map["sourceNotes"]["L"][0]["M"]
    assert note["missedDetail"] == {"S": "stat 70.32"}
    annotation = rich_map["annotations"]["L"][0]["M"]
    assert annotation["startOffset"] == {"N": "10"}
