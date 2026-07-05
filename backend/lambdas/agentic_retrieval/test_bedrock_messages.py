"""Unit tests for bedrock_messages adapter module."""

import json
from unittest.mock import MagicMock

from bedrock_messages import (
    _convert_messages,
    _convert_response_content,
    _convert_tool_definitions,
    _convert_usage,
    converse_stream_with_cache,
    converse_with_cache,
)


class TestConvertToolDefinitions:
    def test_converts_single_tool(self):
        converse_tools = [
            {
                "toolSpec": {
                    "name": "vector_search",
                    "description": "Search for chunks",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        }
                    },
                }
            }
        ]
        result = _convert_tool_definitions(converse_tools)
        assert len(result) == 1
        assert result[0]["name"] == "vector_search"
        assert result[0]["description"] == "Search for chunks"
        assert result[0]["input_schema"] == {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    def test_multiple_tools(self):
        converse_tools = [
            {
                "toolSpec": {
                    "name": "tool_a",
                    "description": "A",
                    "inputSchema": {"json": {"type": "object"}},
                }
            },
            {
                "toolSpec": {
                    "name": "tool_b",
                    "description": "B",
                    "inputSchema": {"json": {"type": "object"}},
                }
            },
        ]
        result = _convert_tool_definitions(converse_tools)
        assert len(result) == 2
        assert result[0]["name"] == "tool_a"
        assert result[1]["name"] == "tool_b"


class TestConvertMessages:
    def test_text_message(self):
        messages = [{"role": "user", "content": [{"text": "hello"}]}]
        result = _convert_messages(messages)
        assert result == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    def test_tool_use_message(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "t1",
                            "name": "vector_search",
                            "input": {"query": "test"},
                        }
                    }
                ],
            }
        ]
        result = _convert_messages(messages)
        assert result[0]["content"][0] == {
            "type": "tool_use",
            "id": "t1",
            "name": "vector_search",
            "input": {"query": "test"},
        }

    def test_tool_result_message(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"json": {"chunks": [{"text": "hello"}]}}],
                        }
                    }
                ],
            }
        ]
        result = _convert_messages(messages)
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t1"
        assert block["content"][0]["type"] == "text"
        parsed = json.loads(block["content"][0]["text"])
        assert parsed == {"chunks": [{"text": "hello"}]}


class TestConvertResponseContent:
    def test_text_block(self):
        content = [{"type": "text", "text": "I'll search"}]
        result = _convert_response_content(content)
        assert result == [{"text": "I'll search"}]

    def test_tool_use_block(self):
        content = [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "vector_search",
                "input": {"query": "test"},
            }
        ]
        result = _convert_response_content(content)
        assert result == [
            {
                "toolUse": {
                    "toolUseId": "t1",
                    "name": "vector_search",
                    "input": {"query": "test"},
                }
            }
        ]

    def test_mixed_content(self):
        content = [
            {"type": "text", "text": "reasoning"},
            {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
        ]
        result = _convert_response_content(content)
        assert len(result) == 2
        assert "text" in result[0]
        assert "toolUse" in result[1]


class TestConvertUsage:
    def test_full_usage(self):
        usage = {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        }
        result = _convert_usage(usage)
        assert result == {
            "inputTokens": 1000,
            "outputTokens": 200,
            "totalTokens": 1200,
            "cacheReadInputTokens": 800,
            "cacheWriteInputTokens": 100,
        }

    def test_no_cache_fields(self):
        usage = {"input_tokens": 500, "output_tokens": 50}
        result = _convert_usage(usage)
        assert result["cacheReadInputTokens"] == 0
        assert result["cacheWriteInputTokens"] == 0
        assert result["totalTokens"] == 550


class TestConverseWithCache:
    def test_end_to_end(self):
        mock_client = MagicMock()
        anthropic_response = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me search."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "vector_search",
                    "input": {"query": "test"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 100,
                "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 0,
            },
        }
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(anthropic_response).encode()
        mock_client.invoke_model.return_value = {"body": mock_body}

        result = converse_with_cache(
            mock_client,
            model_id="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            system=[{"text": "You are helpful."}],
            tool_config={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "vector_search",
                            "description": "Search",
                            "inputSchema": {"json": {"type": "object"}},
                        }
                    }
                ]
            },
            inference_config={"maxTokens": 4096, "temperature": 0.0},
        )

        # Verify the response has Converse shape
        assert result["stopReason"] == "tool_use"
        msg = result["output"]["message"]
        assert msg["role"] == "assistant"
        assert msg["content"][0] == {"text": "Let me search."}
        assert msg["content"][1]["toolUse"]["toolUseId"] == "t1"
        assert result["usage"]["cacheReadInputTokens"] == 4000

        # Verify the request body sent to invoke_model has cache_control
        call_kwargs = mock_client.invoke_model.call_args[1]
        body = json.loads(call_kwargs["body"])
        assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert body["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert body["temperature"] == 0.0


class TestConverseStreamWithCache:
    def test_streams_text_events(self):
        mock_client = MagicMock()

        sse_events = [
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "usage": {"input_tokens": 500, "cache_read_input_tokens": 400}
                            },
                        }
                    ).encode()
                }
            },
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Hello"},
                        }
                    ).encode()
                }
            },
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": " world"},
                        }
                    ).encode()
                }
            },
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "message_delta",
                            "usage": {"output_tokens": 10},
                        }
                    ).encode()
                }
            },
            {"chunk": {"bytes": json.dumps({"type": "message_stop"}).encode()}},
        ]
        mock_client.invoke_model_with_response_stream.return_value = {"body": iter(sse_events)}

        result = converse_stream_with_cache(
            mock_client,
            model_id="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system=[{"text": "system prompt"}],
            inference_config={"maxTokens": 4096, "temperature": 0.0},
        )

        events = list(result["stream"])
        text_deltas = [
            e["contentBlockDelta"]["delta"]["text"] for e in events if "contentBlockDelta" in e
        ]
        assert text_deltas == ["Hello", " world"]

        metadata_events = [e for e in events if "metadata" in e]
        assert len(metadata_events) == 1
        usage = metadata_events[0]["metadata"]["usage"]
        assert usage["inputTokens"] == 500
        assert usage["outputTokens"] == 10
        assert usage["cacheReadInputTokens"] == 400
