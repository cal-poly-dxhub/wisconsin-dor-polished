"""Tests for Bedrock converse_stream processing."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from bedrock_streaming import process_converse_stream


def _make_stream_events(content_blocks, stop_reason="end_turn", usage=None):
    """Build a list of stream events from content block specs."""
    events = [{"messageStart": {"role": "assistant"}}]
    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            events.append({"contentBlockStart": {"contentBlockIndex": idx, "start": {}}})
            events.append({"contentBlockDelta": {"delta": {"text": block["text"]}}})
            events.append({"contentBlockStop": {"contentBlockIndex": idx}})
        elif block["type"] == "toolUse":
            events.append({
                "contentBlockStart": {
                    "contentBlockIndex": idx,
                    "start": {"toolUse": {"toolUseId": block["id"], "name": block["name"]}},
                }
            })
            input_json = json.dumps(block["input"])
            # Split into chunks to simulate real streaming
            mid = len(input_json) // 2
            events.append({"contentBlockDelta": {"delta": {"toolUse": {"input": input_json[:mid]}}}})
            events.append({"contentBlockDelta": {"delta": {"toolUse": {"input": input_json[mid:]}}}})
            events.append({"contentBlockStop": {"contentBlockIndex": idx}})

    events.append({"messageStop": {"stopReason": stop_reason}})
    events.append({"metadata": {"usage": usage or {}}})
    return events


class TestProcessConverseStream:
    def test_text_block(self):
        events = _make_stream_events([{"type": "text", "text": "Hello world"}])
        result = process_converse_stream({"stream": iter(events)})
        assert result.assistant_message["role"] == "assistant"
        assert result.assistant_message["content"] == [{"text": "Hello world"}]
        assert result.stop_reason == "end_turn"
        assert not result.answer_streamed

    def test_tool_use_block(self):
        events = _make_stream_events([{
            "type": "toolUse",
            "id": "t1",
            "name": "vector_search",
            "input": {"query": "property tax"},
        }])
        result = process_converse_stream({"stream": iter(events)})
        content = result.assistant_message["content"]
        assert len(content) == 1
        assert content[0]["toolUse"]["name"] == "vector_search"
        assert content[0]["toolUse"]["input"] == {"query": "property tax"}
        assert not result.answer_streamed

    def test_answer_tool_streams_response(self):
        answer_text = "This is the answer with enough text to exceed the buffer threshold for streaming."
        events = _make_stream_events([{
            "type": "toolUse",
            "id": "t1",
            "name": "answer",
            "input": {"response": answer_text, "cited_doc_ids": ["doc-1"]},
        }])

        fragments = []
        start_called = []

        result = process_converse_stream(
            {"stream": iter(events)},
            on_answer_fragment=lambda f: fragments.append(f),
            on_answer_start=lambda: start_called.append(True),
        )

        assert result.answer_streamed
        assert start_called == [True]
        assert "".join(fragments) == answer_text
        # The full tool input is still parsed correctly
        tool_input = result.assistant_message["content"][0]["toolUse"]["input"]
        assert tool_input["response"] == answer_text
        assert tool_input["cited_doc_ids"] == ["doc-1"]

    def test_mixed_text_and_tools(self):
        events = _make_stream_events([
            {"type": "text", "text": "Let me search."},
            {"type": "toolUse", "id": "t1", "name": "vector_search", "input": {"query": "tax"}},
        ], stop_reason="tool_use")

        text_blocks = []
        result = process_converse_stream(
            {"stream": iter(events)},
            on_text_block=lambda t: text_blocks.append(t),
        )

        assert result.stop_reason == "tool_use"
        content = result.assistant_message["content"]
        assert content[0] == {"text": "Let me search."}
        assert content[1]["toolUse"]["name"] == "vector_search"
        assert text_blocks == ["Let me search."]

    def test_no_stream_field_returns_empty(self):
        result = process_converse_stream({})
        assert result.assistant_message == {}
        assert result.stop_reason == ""
