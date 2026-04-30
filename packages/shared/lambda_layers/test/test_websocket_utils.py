import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add the websocket_utils directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from websocket_utils.models import AgentEventMessage


def test_agent_event_message_camelcase_serialization():
    msg = AgentEventMessage(
        query_id="q-1",
        kind="tool_call",
        turn=2,
        seq=7,
        timestamp=1700000000000,
        payload={"toolName": "vector_search", "summary": '"ag use value"'},
        dev_payload={"toolInput": {"query": "ag use value"}, "toolUseId": "t-1"},
    )
    dumped = msg.model_dump(by_alias=True)
    assert dumped["responseType"] == "agent-event"
    assert dumped["queryId"] == "q-1"
    assert dumped["kind"] == "tool_call"
    assert dumped["turn"] == 2
    assert dumped["seq"] == 7
    assert dumped["timestamp"] == 1700000000000
    assert dumped["payload"] == {"toolName": "vector_search", "summary": '"ag use value"'}
    assert dumped["devPayload"] == {"toolInput": {"query": "ag use value"}, "toolUseId": "t-1"}


def test_agent_event_message_optional_dev_payload():
    msg = AgentEventMessage(
        query_id="q-1",
        kind="loop_start",
        seq=1,
        timestamp=1700000000000,
        payload={"maxTurns": 10},
    )
    dumped = msg.model_dump(by_alias=True)
    # dev_payload defaults to {} for consistent schema on the wire.
    assert dumped["devPayload"] == {}
    assert "turn" in dumped and dumped["turn"] is None


class TestWebSocketServer:
    """Test cases for WebSocketServer"""

    @pytest.fixture
    def mock_boto3_client(self):
        """Mock boto3 client for API Gateway Management API"""
        return MagicMock()

    @patch.dict(os.environ, {"WEBSOCKET_CALLBACK_URL": "wss://example.com/dev"})
    @patch("websocket_utils.utils.boto3")
    def test_websocket_server_instantiation(self, mock_boto3):
        """Test WebSocketServer can be instantiated with correct configuration"""
        from websocket_utils.utils import WebSocketServer

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        connection_id = "test-connection-123"
        server = WebSocketServer(connection_id)

        # Verify the connection ID is stored
        assert server.connection_id == connection_id

        # Verify boto3 client was created with correct endpoint
        mock_boto3.client.assert_called_once_with(
            "apigatewaymanagementapi", endpoint_url="https://example.com/dev"
        )

        # Verify the client is stored
        assert server.client == mock_client

    @patch.dict(os.environ, {"WEBSOCKET_CALLBACK_URL": "wss://example.com/dev"})
    @patch("websocket_utils.utils.boto3")
    @pytest.mark.asyncio
    async def test_send_json_plain_message(self, mock_boto3):
        """Test send_json successfully sends a PlainWebSocketMessage"""
        from websocket_utils.models import PlainWebSocketMessage
        from websocket_utils.utils import WebSocketServer

        # Setup mock client
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.post_to_connection.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        # Create server and message
        connection_id = "test-connection-123"
        server = WebSocketServer(connection_id)
        message = PlainWebSocketMessage(message="Hello, WebSocket!")

        # Send the message
        result = await server.send_json(message)

        # Verify post_to_connection was called with correct parameters
        mock_client.post_to_connection.assert_called_once_with(
            ConnectionId=connection_id, Data=json.dumps("Hello, WebSocket!")
        )

        # Verify the result is returned
        assert result == {"ResponseMetadata": {"HTTPStatusCode": 200}}

    @patch.dict(os.environ, {"WEBSOCKET_CALLBACK_URL": "wss://example/stage"})
    @patch("websocket_utils.utils.boto3")
    @pytest.mark.asyncio
    async def test_send_json_routes_agent_event_message(self, mock_boto3):
        """Test send_json wraps AgentEventMessage with the agent-trace streamId envelope"""
        from websocket_utils.utils import WebSocketServer

        # Setup mock client
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.post_to_connection.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        server = WebSocketServer(connection_id="conn-1")
        msg = AgentEventMessage(
            query_id="q-1",
            kind="reasoning",
            seq=3,
            timestamp=1700000000000,
            payload={"text": "I need to look this up"},
        )

        await server.send_json(msg)

        mock_client.post_to_connection.assert_called_once()
        kwargs = mock_client.post_to_connection.call_args.kwargs
        assert kwargs["ConnectionId"] == "conn-1"
        sent = json.loads(kwargs["Data"])
        assert sent["streamId"] == "agent-trace"
        assert sent["body"]["responseType"] == "agent-event"
        assert sent["body"]["kind"] == "reasoning"
