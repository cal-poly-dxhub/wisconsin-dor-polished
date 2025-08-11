import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chat_api"))


with patch.dict(os.environ, {"SESSIONS_TABLE_NAME": "test-sessions-table", "LOG_LEVEL": "INFO"}):
    from chat_api.main import (
        create_session_handler,
        handler,
        router,
        send_message_handler,
    )


@patch("chat_api.main.dynamodb")
def test_create_session_success(mock_dynamodb):
    """Test happy path for create_session_handler.

    Validates:
    - Session is inserted into DynamoDB correctly
    - Response has correct status code and structure
    """

    mock_dynamodb.put_item.return_value = {}

    # Call the handler directly
    response = create_session_handler()

    assert response["statusCode"] == 201
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "sessionId" in response_body
    session_id = response_body["sessionId"]

    mock_dynamodb.put_item.assert_called_once_with(
        TableName="test-sessions-table", Item={"sessionId": {"S": session_id}}
    )

    assert response["headers"]["Content-Type"] == "application/json"


@patch("chat_api.main.dynamodb")
def test_create_session_unexpected_error(mock_dynamodb):
    """Test create_session_handler with unexpected error during DynamoDB operation.

    Validates:
    - Unexpected errors are handled correctly
    - Response has 500 status code
    - Any error message is returned
    """

    # Mock DynamoDB to raise an unexpected error (not a ChatAPIError)
    mock_dynamodb.put_item.side_effect = RuntimeError("Unexpected DynamoDB error")

    # Call the handler directly
    response = create_session_handler()

    # Verify error response structure
    assert response["statusCode"] == 500
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "message" in response_body["error"]

    mock_dynamodb.put_item.assert_called_once()


@patch("chat_api.main.eventbridge")
@patch("chat_api.main.dynamodb")
def test_send_message_success(mock_dynamodb, mock_eventbridge):
    """Test happy path for send_message_handler.

    Validates:
    - Session existence is validated
    - EventBridge event is emitted with correct structure
    - Response includes query_id
    """

    # Mock DynamoDB response for session validation
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}

    # Mock EventBridge response
    mock_eventbridge.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

    # Mock the router's current_event to provide json_body
    mock_event = MagicMock()
    mock_event.json_body = {"message": "Hello, how can I help you?"}
    router.current_event = mock_event

    # Call the handler directly
    response = send_message_handler("test-session-id")

    # Verify response structure
    assert response["statusCode"] == 200
    assert "body" in response

    response_body = json.loads(response["body"])
    assert "query_id" in response_body
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

    # Verify the query_id in the event matches the response
    assert event_detail["query_id"] == response_body["query_id"]

    assert response["headers"]["Content-Type"] == "application/json"


@patch("chat_api.main.dynamodb")
def test_send_message_invalid_request(mock_dynamodb):
    """Test send_message_handler with invalid MessageRequest.

    Validates:
    - ValidationError is handled for invalid request structure
    - Response has 400 status code
    - Query ID is included in error response
    """

    # Mock DynamoDB response for session validation
    mock_dynamodb.get_item.return_value = {"Item": {"sessionId": {"S": "test-session-id"}}}

    # Mock the router's current_event with invalid body (missing required 'message' field)
    mock_event = MagicMock()
    mock_event.json_body = {"invalid_field": "some value"}
    router.current_event = mock_event

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


@patch("chat_api.main.dynamodb")
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

    # Mock the router's current_event (won't be used due to early session validation failure)
    mock_event = MagicMock()
    mock_event.json_body = {"message": "Hello, how can I help you?"}
    router.current_event = mock_event

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


@patch("chat_api.main.eventbridge")
@patch("chat_api.main.dynamodb")
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

    # Mock the router's current_event with valid body
    mock_event = MagicMock()
    mock_event.json_body = {"message": "Hello, how can I help you?"}
    router.current_event = mock_event

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


@patch("chat_api.main.create_session")
def test_session_route_calls_create_session(mock_create_session):
    """Test that invoking the session/ route calls the create_session function with the event."""

    # Mock the create_session function to return a test session ID
    mock_create_session.return_value = "test-session-id"

    # API Gateway v2 event structure for a POST to /session
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
            "requestId": "test-request-id",
            "stage": "dev",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    # Mock context
    mock_context = MagicMock()

    # Call the main handler with the test event
    response = handler(test_event, mock_context)

    # Assert that create_session was called once
    mock_create_session.assert_called_once()
