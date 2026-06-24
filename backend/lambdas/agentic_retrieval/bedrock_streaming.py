"""Process Bedrock converse_stream() responses.

Accumulates stream events into the same message structure that converse()
returns. Text blocks are streamed in real-time via on_answer_fragment
callbacks — Bedrock delivers text deltas token-by-token, giving true
incremental streaming over WebSocket.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_STREAM_FRAGMENT_MIN_SIZE = 40


@dataclass
class StreamResult:
    """Result of processing a converse_stream response."""

    assistant_message: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    answer_streamed: bool = False


def process_converse_stream(
    stream_response: dict[str, Any],
    *,
    on_answer_fragment: Callable[[str], None] | None = None,
    on_answer_start: Callable[[], None] | None = None,
    on_text_block: Callable[[str], None] | None = None,
    is_answer_turn: bool = False,
) -> StreamResult:
    """Consume a converse_stream() response and reconstruct the message.

    When is_answer_turn=True, text blocks are streamed in real-time:
      - Calls on_answer_start() when the first text delta arrives
      - Calls on_answer_fragment(text) with buffered fragments (~40+ chars)

    For non-answer turns, text blocks are accumulated normally.
    Returns a StreamResult with the full reconstructed message.
    """
    result = StreamResult()
    content_blocks: list[dict[str, Any]] = []
    current_block_type: str | None = None  # "text" or "toolUse"
    current_text = ""
    current_tool_use_id = ""
    current_tool_name = ""
    current_tool_input_json = ""
    fragment_buffer = ""
    answer_started_emitted = False

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
                text_chunk = delta["text"]
                current_text += text_chunk

                if is_answer_turn and on_answer_fragment:
                    if not answer_started_emitted and on_answer_start:
                        on_answer_start()
                        answer_started_emitted = True
                        result.answer_streamed = True

                    fragment_buffer += text_chunk
                    while len(fragment_buffer) >= _STREAM_FRAGMENT_MIN_SIZE:
                        on_answer_fragment(fragment_buffer[:_STREAM_FRAGMENT_MIN_SIZE])
                        fragment_buffer = fragment_buffer[_STREAM_FRAGMENT_MIN_SIZE:]

            elif current_block_type == "toolUse" and "toolUse" in delta:
                current_tool_input_json += delta["toolUse"].get("input", "")

        elif "contentBlockStop" in event:
            if current_block_type == "text":
                content_blocks.append({"text": current_text})
                if is_answer_turn and fragment_buffer and on_answer_fragment:
                    on_answer_fragment(fragment_buffer)
                    fragment_buffer = ""
                if current_text and on_text_block:
                    on_text_block(current_text)
            elif current_block_type == "toolUse":
                try:
                    parsed_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to parse tool input JSON for {current_tool_name}"
                    )
                    parsed_input = {}
                content_blocks.append({
                    "toolUse": {
                        "toolUseId": current_tool_use_id,
                        "name": current_tool_name,
                        "input": parsed_input,
                    }
                })

            current_block_type = None

        elif "messageStop" in event:
            result.stop_reason = event["messageStop"].get("stopReason", "")

        elif "metadata" in event:
            result.usage = event["metadata"].get("usage", {})

    result.assistant_message["content"] = content_blocks
    return result
