"""Process Bedrock converse_stream() responses.

Accumulates stream events into the same message structure that converse()
returns, with the ability to forward the answer tool's response field as
real-time WebSocket fragments.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from stream_parser import AnswerToolStreamParser

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
) -> StreamResult:
    """Consume a converse_stream() response and reconstruct the message.

    When the answer tool is detected:
      - Calls on_answer_start() when the answer tool's response field begins streaming
      - Calls on_answer_fragment(text) with buffered fragments of the response value
      - Text fragments are buffered to ~40+ chars to avoid excessive WebSocket calls

    For non-answer tools and text blocks, accumulates normally.
    Returns a StreamResult with the full reconstructed message.
    """
    result = StreamResult()
    content_blocks: list[dict[str, Any]] = []
    current_block_index = -1
    current_block_type: str | None = None  # "text" or "toolUse"
    current_text = ""
    current_tool_use_id = ""
    current_tool_name = ""
    current_tool_input_json = ""
    answer_parser: AnswerToolStreamParser | None = None
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
            current_block_index = block_start.get("contentBlockIndex", 0)
            start = block_start.get("start", {})

            if "toolUse" in start:
                current_block_type = "toolUse"
                current_tool_use_id = start["toolUse"].get("toolUseId", "")
                current_tool_name = start["toolUse"].get("name", "")
                current_tool_input_json = ""
                if current_tool_name == "answer":
                    answer_parser = AnswerToolStreamParser()
            else:
                current_block_type = "text"
                current_text = ""

        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})

            if current_block_type == "text" and "text" in delta:
                current_text += delta["text"]

            elif current_block_type == "toolUse" and "toolUse" in delta:
                input_chunk = delta["toolUse"].get("input", "")
                current_tool_input_json += input_chunk

                if answer_parser and input_chunk:
                    fragments = answer_parser.feed(input_chunk)
                    if fragments:
                        if not answer_started_emitted and on_answer_start:
                            on_answer_start()
                            answer_started_emitted = True
                            result.answer_streamed = True

                        fragment_buffer += "".join(fragments)
                        if len(fragment_buffer) >= _STREAM_FRAGMENT_MIN_SIZE:
                            if on_answer_fragment:
                                on_answer_fragment(fragment_buffer)
                            fragment_buffer = ""

        elif "contentBlockStop" in event:
            if current_block_type == "text":
                content_blocks.append({"text": current_text})
                if current_text and on_text_block:
                    on_text_block(current_text)
            elif current_block_type == "toolUse":
                # Flush remaining answer fragment buffer
                if answer_parser and fragment_buffer:
                    if on_answer_fragment:
                        on_answer_fragment(fragment_buffer)
                    fragment_buffer = ""

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
