"""Tests for the cite_documents tool streaming JSON parser."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from stream_parser import AnswerToolStreamParser


class TestAnswerToolStreamParser:
    def test_single_chunk_complete_json(self):
        parser = AnswerToolStreamParser()
        json_str = '{"response": "Hello world", "cited_doc_ids": ["doc-1"]}'
        fragments = parser.feed(json_str)
        assert "".join(fragments) == "Hello world"
        assert parser.is_done
        assert parser.get_accumulated() == json_str

    def test_chunked_response(self):
        parser = AnswerToolStreamParser()
        chunks = ['{"respon', 'se": "He', 'llo wor', 'ld", "cited', '_doc_ids": []}']
        all_fragments = []
        for chunk in chunks:
            all_fragments.extend(parser.feed(chunk))
        assert "".join(all_fragments) == "Hello world"
        assert parser.is_done

    def test_escaped_characters(self):
        parser = AnswerToolStreamParser()
        json_str = r'{"response": "line1\nline2\ttab\\backslash\"quote", "cited_doc_ids": []}'
        fragments = parser.feed(json_str)
        text = "".join(fragments)
        assert text == 'line1\nline2\ttab\\backslash"quote'
        assert parser.is_done

    def test_response_with_markdown(self):
        parser = AnswerToolStreamParser()
        json_str = '{"response": "## Title\\n\\nSome **bold** text with `code`", "cited_doc_ids": ["a"]}'
        fragments = parser.feed(json_str)
        text = "".join(fragments)
        assert "## Title" in text
        assert "**bold**" in text
        assert parser.is_done

    def test_empty_response(self):
        parser = AnswerToolStreamParser()
        json_str = '{"response": "", "cited_doc_ids": []}'
        fragments = parser.feed(json_str)
        assert "".join(fragments) == ""
        assert parser.is_done

    def test_not_done_until_string_ends(self):
        parser = AnswerToolStreamParser()
        chunk1 = '{"response": "still going'
        fragments1 = parser.feed(chunk1)
        assert not parser.is_done
        assert parser.is_streaming
        assert "".join(fragments1) == "still going"

        chunk2 = ' more text"'
        fragments2 = parser.feed(chunk2)
        assert parser.is_done
        assert "".join(fragments2) == " more text"

    def test_accumulates_full_json(self):
        parser = AnswerToolStreamParser()
        chunks = ['{"resp', 'onse": "hi', '", "cited_doc', '_ids": ["d1", "d2"]}']
        for chunk in chunks:
            parser.feed(chunk)
        assert parser.get_accumulated() == '{"response": "hi", "cited_doc_ids": ["d1", "d2"]}'

    def test_response_key_not_first(self):
        """Parser works even if response isn't the first key."""
        parser = AnswerToolStreamParser()
        json_str = '{"cited_doc_ids": ["a"], "response": "answer text"}'
        fragments = parser.feed(json_str)
        assert "".join(fragments) == "answer text"
        assert parser.is_done

    def test_character_by_character_delivery(self):
        """Simulates Bedrock delivering one character at a time."""
        parser = AnswerToolStreamParser()
        json_str = '{"response": "token by token", "cited_doc_ids": []}'
        all_fragments = []
        for char in json_str:
            all_fragments.extend(parser.feed(char))
        assert "".join(all_fragments) == "token by token"
        assert parser.is_done
