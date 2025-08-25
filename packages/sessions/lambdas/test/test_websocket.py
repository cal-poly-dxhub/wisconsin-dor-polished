import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the websocket directory to sys.path so we can import the modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "websocket"))
# Add the shared lambda layers directory to sys.path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "lambda_layers")
)


class TestWebSocketHandlers:
    """Test cases for WebSocket Lambda handlers"""

    @pytest.fixture
    def connect_event(self):
        """Sample WebSocket connect event"""
        return {
            "requestContext": {
                "connectionId": "test-connection-123",
                "domainName": "example.com",
                "stage": "dev",
                "eventType": "CONNECT",
            },
            "queryStringParameters": {"sessionId": "test-session-456"},
        }

    @pytest.fixture
    def disconnect_event(self):
        """Sample WebSocket disconnect event"""
        return {
            "requestContext": {
                "connectionId": "test-connection-123",
                "domainName": "example.com",
                "stage": "dev",
                "eventType": "DISCONNECT",
            }
        }

    @pytest.fixture
    def message_event(self):
        """Sample WebSocket message event"""
        return {
            "requestContext": {
                "connectionId": "test-connection-123",
                "domainName": "example.com",
                "stage": "dev",
                "eventType": "MESSAGE",
            },
            "body": json.dumps("Hello World"),
            "queryStringParameters": {"sessionId": "test-session-456"},
        }

    @pytest.fixture
    def mock_context(self):
        """Mock Lambda context"""
        context = MagicMock()
        context.aws_request_id = "test-request-id"
        return context

    @patch("connect.dynamodb")
    def test_connect_handler_happy_path(self, mock_dynamodb, connect_event, mock_context):
        """Test successful WebSocket connection"""
        from connect import handler

        # Mock successful DynamoDB put_item
        mock_dynamodb.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        result = handler(connect_event, mock_context)

        # Verify DynamoDB was called correctly
        mock_dynamodb.put_item.assert_called_once()
        call_args = mock_dynamodb.put_item.call_args
        assert call_args[1]["Item"]["sessionId"]["S"] == "test-session-456"
        assert call_args[1]["Item"]["connectionId"]["S"] == "test-connection-123"
        assert call_args[1]["ConditionExpression"] == "attribute_exists(sessionId)"

        # Verify successful response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Connected"

    @patch("disconnect.dynamodb")
    def test_disconnect_handler_happy_path(self, mock_dynamodb, disconnect_event, mock_context):
        """Test successful WebSocket disconnection"""
        from disconnect import handler

        # Mock successful query and update operations
        mock_dynamodb.query.return_value = {"Items": [{"sessionId": {"S": "test-session-456"}}]}
        mock_dynamodb.update_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        result = handler(disconnect_event, mock_context)

        # Verify DynamoDB query was called correctly
        mock_dynamodb.query.assert_called_once()
        query_args = mock_dynamodb.query.call_args
        assert query_args[1]["IndexName"] == "connectionId"
        assert query_args[1]["KeyConditionExpression"] == "connectionId = :cid"

        # Verify DynamoDB update was called correctly to remove connectionId
        mock_dynamodb.update_item.assert_called_once()
        update_args = mock_dynamodb.update_item.call_args
        assert update_args[1]["Key"]["sessionId"]["S"] == "test-session-456"
        assert update_args[1]["UpdateExpression"] == "REMOVE connectionId"
        assert update_args[1]["ConditionExpression"] == "connectionId = :cid"

        # Verify successful response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Disconnected"

    @patch("websocket_utils.utils.WebSocketServer")
    @patch.dict("default.os.environ", {"WEBSOCKET_CALLBACK_URL": "wss://example.com/dev"})
    def test_default_handler_happy_path(
        self, mock_websocket_server_class, message_event, mock_context
    ):
        """Test successful message echo"""
        from default import handler

        # Mock WebSocketServer instance
        mock_server = MagicMock()
        mock_websocket_server_class.return_value = mock_server
        # Make send_json an async mock
        mock_server.send_json = AsyncMock(return_value=None)

        result = handler(message_event, mock_context)

        # Verify WebSocketServer was created with correct connection ID
        mock_websocket_server_class.assert_called_once_with("test-connection-123")

        # Verify send_json was called with echo message
        mock_server.send_json.assert_called_once()
        sent_message = mock_server.send_json.call_args[0][0]
        assert json.loads(sent_message.message) == "Hello World"

        # Verify successful response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Message echoed successfully"

    @patch("disconnect.dynamodb")
    def test_disconnect_handler_no_session_found(
        self, mock_dynamodb, disconnect_event, mock_context
    ):
        """Test disconnect when no session is found (still successful)"""
        from disconnect import handler

        # Mock query returning no items
        mock_dynamodb.query.return_value = {"Items": []}

        result = handler(disconnect_event, mock_context)

        # Verify query was called
        mock_dynamodb.query.assert_called_once()

        # Verify update was not called since no session found
        mock_dynamodb.update_item.assert_not_called()

        # Verify successful response (disconnect should succeed even if no session)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Disconnected"

    def test_message_event_validation(self, message_event):
        """Test that message events are properly validated"""
        from validators import validate_message_event

        validated_event = validate_message_event(message_event)

        assert validated_event.requestContext.connectionId == "test-connection-123"
        assert validated_event.requestContext.eventType == "MESSAGE"
        assert json.loads(validated_event.body) == "Hello World"

    def test_connect_event_validation(self, connect_event):
        """Test that connect events are properly validated"""
        from validators import validate_connect_event

        validated_event = validate_connect_event(connect_event)

        assert validated_event.requestContext.connectionId == "test-connection-123"
        assert validated_event.requestContext.eventType == "CONNECT"
        assert validated_event.queryStringParameters.sessionId == "test-session-456"

    def test_disconnect_event_validation(self, disconnect_event):
        """Test that disconnect events are properly validated"""
        from validators import validate_disconnect_event

        validated_event = validate_disconnect_event(disconnect_event)

        assert validated_event.requestContext.connectionId == "test-connection-123"
        assert validated_event.requestContext.eventType == "DISCONNECT"
