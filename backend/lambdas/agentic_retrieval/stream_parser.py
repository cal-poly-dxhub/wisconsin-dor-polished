"""Incremental JSON parser for streaming the cite_documents tool's response field.

When Bedrock streams a tool_use input, we receive the JSON as a series of
string fragments. This parser detects when we're inside the "response" string
value and yields those characters in real-time, while accumulating the full
JSON for final parsing of cited_doc_ids.
"""

from __future__ import annotations

from enum import Enum, auto


class _State(Enum):
    SEARCHING = auto()
    IN_RESPONSE_VALUE = auto()
    DONE = auto()


class AnswerToolStreamParser:
    """Feeds incremental JSON chunks and yields response text as it arrives.

    Usage:
        parser = AnswerToolStreamParser()
        for chunk in bedrock_stream:
            for text_fragment in parser.feed(chunk):
                send_to_websocket(text_fragment)
        full_json = parser.get_accumulated()
    """

    _RESPONSE_PREFIX = '"response"'

    def __init__(self) -> None:
        self._state = _State.SEARCHING
        self._accumulated = ""
        self._search_buffer = ""
        self._found_key = False
        self._waiting_for_open_quote = False
        self._escape_next = False

    def feed(self, chunk: str) -> list[str]:
        """Feed a JSON fragment. Returns list of response text pieces to stream."""
        self._accumulated += chunk
        if self._state == _State.DONE:
            return []

        fragments: list[str] = []

        for char in chunk:
            if self._state == _State.SEARCHING:
                self._search_buffer += char
                if not self._found_key:
                    if self._RESPONSE_PREFIX in self._search_buffer:
                        self._found_key = True
                        self._waiting_for_open_quote = True
                        self._search_buffer = ""
                    elif len(self._search_buffer) > 200:
                        self._search_buffer = self._search_buffer[-50:]
                else:
                    if self._waiting_for_open_quote:
                        if char == '"':
                            self._state = _State.IN_RESPONSE_VALUE
                            self._waiting_for_open_quote = False
                            self._search_buffer = ""

            elif self._state == _State.IN_RESPONSE_VALUE:
                if self._escape_next:
                    if char == 'n':
                        fragments.append('\n')
                    elif char == 't':
                        fragments.append('\t')
                    elif char == '\\':
                        fragments.append('\\')
                    elif char == '"':
                        fragments.append('"')
                    elif char == '/':
                        fragments.append('/')
                    else:
                        fragments.append('\\' + char)
                    self._escape_next = False
                elif char == '\\':
                    self._escape_next = True
                elif char == '"':
                    self._state = _State.DONE
                else:
                    fragments.append(char)

        return fragments

    def get_accumulated(self) -> str:
        """Return the full accumulated JSON string for final parsing."""
        return self._accumulated

    @property
    def is_done(self) -> bool:
        return self._state == _State.DONE

    @property
    def is_streaming(self) -> bool:
        return self._state == _State.IN_RESPONSE_VALUE
