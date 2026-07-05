"""Tests for Bedrock converse_stream processing."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from bedrock_streaming import process_converse_stream


def _make_stream_events(content_blocks, stop_reason="end_turn", usage=None):
    """Build a list of stream events from content block specs."""
    events = [{"messageStart": {"role": "assistant"}}]
    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            events.append({"contentBlockStart": {"contentBlockIndex": idx, "start": {}}})
            text = block["text"]
            chunk_size = block.get("chunk_size", len(text))
            for i in range(0, len(text), chunk_size):
                events.append({"contentBlockDelta": {"delta": {"text": text[i:i + chunk_size]}}})
            events.append({"contentBlockStop": {"contentBlockIndex": idx}})
        elif block["type"] == "toolUse":
            events.append({
                "contentBlockStart": {
                    "contentBlockIndex": idx,
                    "start": {"toolUse": {"toolUseId": block["id"], "name": block["name"]}},
                }
            })
            input_json = json.dumps(block["input"])
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

    def test_mixed_text_and_tools(self):
        events = _make_stream_events([
            {"type": "text", "text": "Let me search."},
            {"type": "toolUse", "id": "t1", "name": "vector_search", "input": {"query": "tax"}},
        ], stop_reason="tool_use")

        result = process_converse_stream({"stream": iter(events)})

        assert result.stop_reason == "tool_use"
        content = result.assistant_message["content"]
        assert content[0] == {"text": "Let me search."}
        assert content[1]["toolUse"]["name"] == "vector_search"

    def test_prepare_answer_tool_input_accumulated(self):
        """prepare_answer tool input is fully accumulated and parsed."""
        tool_input = {"cited_doc_ids": ["WIS-STAT-70.32"], "answer_plan": "Explain assessment requirements"}
        events = _make_stream_events([{
            "type": "toolUse",
            "id": "t1",
            "name": "prepare_answer",
            "input": tool_input,
        }], stop_reason="tool_use")

        result = process_converse_stream({"stream": iter(events)})

        tool_block = result.assistant_message["content"][0]["toolUse"]
        assert tool_block["input"]["cited_doc_ids"] == ["WIS-STAT-70.32"]
        assert tool_block["input"]["answer_plan"] == "Explain assessment requirements"

    def test_usage_extracted(self):
        events = _make_stream_events(
            [{"type": "text", "text": "hi"}],
            usage={"inputTokens": 100, "outputTokens": 50},
        )
        result = process_converse_stream({"stream": iter(events)})
        assert result.usage == {"inputTokens": 100, "outputTokens": 50}

    def test_no_stream_field_returns_empty(self):
        result = process_converse_stream({})
        assert result.assistant_message == {}
        assert result.stop_reason == ""
