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
            # Split text into chunks to simulate real streaming
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

    def test_text_block_streams_when_answer_turn(self):
        """Text blocks stream fragments in real-time when is_answer_turn=True."""
        answer_text = "This is the answer with enough text to exceed the buffer threshold for streaming."
        events = _make_stream_events([
            {"type": "text", "text": answer_text, "chunk_size": 10},
            {"type": "toolUse", "id": "t1", "name": "cite_documents", "input": {"cited_doc_ids": ["doc-1"]}},
        ], stop_reason="tool_use")

        fragments = []
        start_called = []

        result = process_converse_stream(
            {"stream": iter(events)},
            on_answer_fragment=lambda f: fragments.append(f),
            on_answer_start=lambda: start_called.append(True),
            is_answer_turn=True,
        )

        assert result.answer_streamed
        assert start_called == [True]
        assert "".join(fragments) == answer_text
        # Text is still accumulated correctly
        assert result.assistant_message["content"][0] == {"text": answer_text}
        # Tool input is parsed
        tool_input = result.assistant_message["content"][1]["toolUse"]["input"]
        assert tool_input["cited_doc_ids"] == ["doc-1"]

    def test_text_block_does_not_stream_when_not_answer_turn(self):
        """Text blocks are accumulated without streaming when is_answer_turn=False."""
        events = _make_stream_events([
            {"type": "text", "text": "Let me search for that."},
            {"type": "toolUse", "id": "t1", "name": "vector_search", "input": {"query": "tax"}},
        ], stop_reason="tool_use")

        fragments = []
        result = process_converse_stream(
            {"stream": iter(events)},
            on_answer_fragment=lambda f: fragments.append(f),
            on_answer_start=lambda: None,
            is_answer_turn=False,
        )

        assert not result.answer_streamed
        assert fragments == []
        assert result.assistant_message["content"][0] == {"text": "Let me search for that."}

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

    def test_large_text_sends_multiple_fragments(self):
        """When a large text block arrives, it should be split into multiple sends."""
        answer_text = "A" * 200
        # Deliver as one big delta (simulates large chunk)
        events = [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
            {"contentBlockDelta": {"delta": {"text": answer_text}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {}}},
        ]

        fragments = []
        result = process_converse_stream(
            {"stream": iter(events)},
            on_answer_fragment=lambda f: fragments.append(f),
            on_answer_start=lambda: None,
            is_answer_turn=True,
        )

        assert result.answer_streamed
        assert "".join(fragments) == answer_text
        assert len(fragments) >= 4
        for frag in fragments[:-1]:
            assert len(frag) == 40

    def test_incremental_text_streaming(self):
        """Token-by-token text delivery produces smooth fragment output."""
        answer_text = "The assessment ratio for residential property is 100%."
        # Simulate token-by-token delivery (small chunks)
        events = [{"messageStart": {"role": "assistant"}}]
        events.append({"contentBlockStart": {"contentBlockIndex": 0, "start": {}}})
        for word in answer_text.split(" "):
            events.append({"contentBlockDelta": {"delta": {"text": word + " "}}})
        events.append({"contentBlockStop": {"contentBlockIndex": 0}})
        events.append({"messageStop": {"stopReason": "end_turn"}})
        events.append({"metadata": {"usage": {}}})

        fragments = []
        result = process_converse_stream(
            {"stream": iter(events)},
            on_answer_fragment=lambda f: fragments.append(f),
            on_answer_start=lambda: None,
            is_answer_turn=True,
        )

        assert result.answer_streamed
        # Fragments are buffered to ~40 chars each
        combined = "".join(fragments)
        assert combined == answer_text + " "  # trailing space from word split

    def test_no_stream_field_returns_empty(self):
        result = process_converse_stream({})
        assert result.assistant_message == {}
        assert result.stop_reason == ""
