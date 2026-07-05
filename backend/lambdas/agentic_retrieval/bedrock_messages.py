"""
Adapter for Bedrock invoke_model with Anthropic Messages API format.

Enables prompt caching by converting Converse-format tool definitions and
messages to the native Anthropic format, injecting cache_control breakpoints,
and mapping responses back to the Converse-compatible shape that main.py expects.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


def _convert_tool_definitions(
    converse_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Converse toolSpec format to Anthropic Messages tool format."""
    tools = []
    for item in converse_tools:
        spec = item["toolSpec"]
        tools.append({
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec["inputSchema"]["json"],
        })
    return tools


def _convert_content_to_messages(
    converse_content: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert a single message's content blocks from Converse to Messages format."""
    blocks = []
    for block in converse_content:
        if "text" in block:
            blocks.append({"type": "text", "text": block["text"]})
        elif "toolUse" in block:
            tu = block["toolUse"]
            blocks.append({
                "type": "tool_use",
                "id": tu["toolUseId"],
                "name": tu["name"],
                "input": tu["input"],
            })
        elif "toolResult" in block:
            tr = block["toolResult"]
            content_parts = []
            for c in tr.get("content", []):
                if "json" in c:
                    content_parts.append({
                        "type": "text",
                        "text": json.dumps(c["json"], separators=(",", ":")),
                    })
                elif "text" in c:
                    content_parts.append({"type": "text", "text": c["text"]})
            blocks.append({
                "type": "tool_result",
                "tool_use_id": tr["toolUseId"],
                "content": content_parts,
            })
    return blocks


def _convert_messages(
    converse_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert full message list from Converse to Anthropic Messages format."""
    messages = []
    for msg in converse_messages:
        messages.append({
            "role": msg["role"],
            "content": _convert_content_to_messages(msg["content"]),
        })
    return messages


def _convert_response_content(
    anthropic_content: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic Messages response content to Converse format."""
    blocks = []
    for block in anthropic_content:
        if block["type"] == "text":
            blocks.append({"text": block["text"]})
        elif block["type"] == "tool_use":
            blocks.append({
                "toolUse": {
                    "toolUseId": block["id"],
                    "name": block["name"],
                    "input": block["input"],
                }
            })
    return blocks


def _convert_usage(anthropic_usage: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic usage fields to Converse-style camelCase."""
    return {
        "inputTokens": anthropic_usage.get("input_tokens", 0),
        "outputTokens": anthropic_usage.get("output_tokens", 0),
        "totalTokens": (
            anthropic_usage.get("input_tokens", 0)
            + anthropic_usage.get("output_tokens", 0)
        ),
        "cacheReadInputTokens": anthropic_usage.get("cache_read_input_tokens", 0),
        "cacheWriteInputTokens": anthropic_usage.get("cache_creation_input_tokens", 0),
    }


def _stop_reason_to_converse(anthropic_stop: str) -> str:
    """Map Anthropic stop_reason to Converse stopReason."""
    mapping = {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "stop_sequence": "end_turn",
    }
    return mapping.get(anthropic_stop, anthropic_stop)


def converse_with_cache(
    bedrock_client,
    model_id: str,
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]],
    tool_config: dict[str, Any] | None = None,
    inference_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Drop-in replacement for bedrock.converse() that uses invoke_model
    with the Anthropic Messages API format to enable prompt caching.

    Returns data in the same shape as bedrock.converse() so callers
    don't need to change.
    """
    inference_config = inference_config or {}

    # Build Anthropic Messages API body
    converted_messages = _convert_messages(messages)

    # Messages with cache breakpoint on last message (turn-over-turn caching)
    if converted_messages:
        last_msg = converted_messages[-1]
        if last_msg.get("content") and isinstance(last_msg["content"], list):
            last_msg["content"][-1]["cache_control"] = {"type": "ephemeral"}

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": inference_config.get("maxTokens", 4096),
        "messages": converted_messages,
    }

    if inference_config.get("temperature") is not None:
        body["temperature"] = inference_config["temperature"]

    # System prompt with cache breakpoint
    if system:
        system_blocks = []
        for i, s in enumerate(system):
            block = {"type": "text", "text": s["text"]}
            if i == len(system) - 1:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        body["system"] = system_blocks

    # Tools with cache breakpoint on last tool
    if tool_config and tool_config.get("tools"):
        tools = _convert_tool_definitions(tool_config["tools"])
        if tools:
            tools[-1]["cache_control"] = {"type": "ephemeral"}
        body["tools"] = tools

    response = bedrock_client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    result = json.loads(response["body"].read())

    # Convert to Converse-compatible response shape
    return {
        "output": {
            "message": {
                "role": result.get("role", "assistant"),
                "content": _convert_response_content(result.get("content", [])),
            }
        },
        "stopReason": _stop_reason_to_converse(result.get("stop_reason", "")),
        "usage": _convert_usage(result.get("usage", {})),
    }


def converse_stream_with_cache(
    bedrock_client,
    model_id: str,
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]],
    inference_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Drop-in replacement for bedrock.converse_stream() that uses
    invoke_model_with_response_stream with the Anthropic Messages API.

    Returns {"stream": <iterator>} where the iterator yields events in
    the same format as Converse stream events, so Phase B code works unchanged.
    """
    inference_config = inference_config or {}

    converted_messages = _convert_messages(messages)

    # Messages with cache breakpoint on last message (turn-over-turn caching)
    if converted_messages:
        last_msg = converted_messages[-1]
        if last_msg.get("content") and isinstance(last_msg["content"], list):
            last_msg["content"][-1]["cache_control"] = {"type": "ephemeral"}

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": inference_config.get("maxTokens", 4096),
        "messages": converted_messages,
    }

    if inference_config.get("temperature") is not None:
        body["temperature"] = inference_config["temperature"]

    if system:
        system_blocks = []
        for i, s in enumerate(system):
            block = {"type": "text", "text": s["text"]}
            if i == len(system) - 1:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        body["system"] = system_blocks

    response = bedrock_client.invoke_model_with_response_stream(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    def _event_adapter() -> Iterator[dict[str, Any]]:
        """Yield Converse-compatible stream events from Anthropic SSE stream."""
        usage: dict[str, Any] = {}

        for event in response["body"]:
            chunk = json.loads(event["chunk"]["bytes"])
            event_type = chunk.get("type", "")

            if event_type == "message_start":
                msg_usage = chunk.get("message", {}).get("usage", {})
                usage["input_tokens"] = msg_usage.get("input_tokens", 0)
                usage["cache_read_input_tokens"] = msg_usage.get(
                    "cache_read_input_tokens", 0
                )
                usage["cache_creation_input_tokens"] = msg_usage.get(
                    "cache_creation_input_tokens", 0
                )

            elif event_type == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield {
                        "contentBlockDelta": {
                            "delta": {"text": delta.get("text", "")},
                            "contentBlockIndex": chunk.get("index", 0),
                        }
                    }

            elif event_type == "message_delta":
                delta_usage = chunk.get("usage", {})
                usage["output_tokens"] = delta_usage.get("output_tokens", 0)
                yield {
                    "metadata": {
                        "usage": _convert_usage(usage),
                    }
                }

            elif event_type == "message_stop":
                pass

    return {"stream": _event_adapter()}
