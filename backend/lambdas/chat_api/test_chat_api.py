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

with patch.dict(os.environ, {"SESSIONS_TABLE_NAME": "test-sessions-table", "LOG_LEVEL": "INFO"}):
    import main as chat_api_main
    from main import (
        create_session_handler,
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


def _set_current_event(json_body=None, claims=None):
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
