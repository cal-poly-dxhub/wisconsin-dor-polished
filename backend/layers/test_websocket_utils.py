import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from websocket_utils.errors import InvalidMessageError
from websocket_utils.models import (
    AgentEventMessage,
    AnswerEventType,
    ChoicesContent,
    ChoicesMessage,
    ErrorContent,
    ErrorMessage,
    FragmentContent,
    FragmentMessage,
    WebSocketMessage,
)
from websocket_utils.utils import frame, post_message


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


def test_choices_message_carries_clarification_framing():
    """The adequacy judge sends the question and axis WITH the options, so the
    frontend can frame them as one clarification block."""
    msg = ChoicesMessage(
        query_id="q-1",
        content=ChoicesContent(
            choices=["Agricultural", "Undeveloped", "Answer in general terms"],
            question="Is the parcel classified agricultural or undeveloped?",
            axis="property classification",
        ),
    )
    dumped = msg.model_dump(by_alias=True)
    assert dumped["responseType"] == "choices"
    assert dumped["queryId"] == "q-1"
    content = dumped["content"]
    assert content["choices"][0] == "Agricultural"
    # Single-word field names, so the camelCase alias generator is a no-op.
    assert content["question"] == "Is the parcel classified agricultural or undeveloped?"
    assert content["axis"] == "property classification"


def test_choices_message_framing_is_optional():
    """Options with no question or axis still serialize both fields (as null)
    so the schema is stable."""
    msg = ChoicesMessage(
        query_id="q-2",
        content=ChoicesContent(choices=["Residential", "Commercial"]),
    )
    content = msg.model_dump(by_alias=True)["content"]
    assert content["question"] is None
    assert content["axis"] is None
    assert "kind" not in content


def test_error_message_carries_query_id():
    msg = ErrorMessage(query_id="q-9", content=ErrorContent(error="boom"))
    dumped = msg.model_dump(by_alias=True)
    assert dumped == {"responseType": "error", "queryId": "q-9", "content": {"error": "boom"}}


@pytest.mark.parametrize(
    ("message", "stream_id"),
    [
        (ErrorMessage(content=ErrorContent(error="x")), "error"),
        (
            ChoicesMessage(query_id="q", content=ChoicesContent(choices=["a"])),
            "choices",
        ),
        (AnswerEventType(event="start", query_id="q"), "answer-event"),
        (FragmentMessage(query_id="q", content=FragmentContent(fragment="hi")), "answer"),
    ],
)
def test_post_message_frames_with_stream_id(message, stream_id):
    """post_message writes the exact {streamId, body} frame the client parses."""
    ws = MagicMock()
    ws.connection_id = "conn-7"
    post_message(ws, message)
    kwargs = ws.client.post_to_connection.call_args.kwargs
    assert kwargs["ConnectionId"] == "conn-7"
    assert kwargs["Data"] == json.dumps(
        {"streamId": stream_id, "body": message.model_dump(by_alias=True)}
    )


def test_frame_rejects_unknown_message_type():
    with pytest.raises(InvalidMessageError):
        frame(WebSocketMessage())


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
