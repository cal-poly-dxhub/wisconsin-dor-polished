"""Process Bedrock converse_stream() responses.

Accumulates stream events into the same message structure that converse()
returns. No real-time streaming — the full message is reconstructed and
the answer is replayed with pacing after the loop completes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StreamResult:
    """Result of processing a converse_stream response."""

    assistant_message: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def process_converse_stream(stream_response: dict[str, Any]) -> StreamResult:
    """Consume a converse_stream() response and reconstruct the message.

    Iterates through all stream events, accumulating text blocks and
    tool_use blocks into the same structure that converse() returns.
    """
    result = StreamResult()
    content_blocks: list[dict[str, Any]] = []
    current_block_type: str | None = None
    current_text = ""
    current_tool_use_id = ""
    current_tool_name = ""
    current_tool_input_json = ""

    event_stream = stream_response.get("stream")
    if not event_stream:
        logger.error("converse_stream response missing 'stream' field")
        return result

    for event in event_stream:
        if "messageStart" in event:
            result.assistant_message = {
                "role": event["messageStart"].get("role", "assistant"),
                "content": [],
            }

        elif "contentBlockStart" in event:
            block_start = event["contentBlockStart"]
            start = block_start.get("start", {})

            if "toolUse" in start:
                current_block_type = "toolUse"
                current_tool_use_id = start["toolUse"].get("toolUseId", "")
                current_tool_name = start["toolUse"].get("name", "")
                current_tool_input_json = ""
            else:
                current_block_type = "text"
                current_text = ""

        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})

            if current_block_type == "text" and "text" in delta:
                current_text += delta["text"]

            elif current_block_type == "toolUse" and "toolUse" in delta:
                current_tool_input_json += delta["toolUse"].get("input", "")

        elif "contentBlockStop" in event:
            if current_block_type == "text":
                content_blocks.append({"text": current_text})
            elif current_block_type == "toolUse":
                try:
                    parsed_input = (
                        json.loads(current_tool_input_json) if current_tool_input_json else {}
                    )
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse tool input JSON for {current_tool_name}")
                    parsed_input = {}
                content_blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": current_tool_use_id,
                            "name": current_tool_name,
                            "input": parsed_input,
                        }
                    }
                )

            current_block_type = None

        elif "messageStop" in event:
            result.stop_reason = event["messageStop"].get("stopReason", "")

        elif "metadata" in event:
            result.usage = event["metadata"].get("usage", {})

    result.assistant_message["content"] = content_blocks
    return result
