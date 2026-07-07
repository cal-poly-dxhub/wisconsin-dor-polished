"""Tests for the tracing module."""

from unittest.mock import MagicMock

from tracing import (
    compact_log_value,
    emit_trace,
    filter_metadata,
    query_log_fields,
    truncate_text,
)


class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("hello", 10) == "hello"

    def test_long_text_truncated(self):
        result = truncate_text("a" * 100, 10)
        assert len(result) < 100
        assert "truncated 90 chars" in result

    def test_exact_length_unchanged(self):
        assert truncate_text("abcde", 5) == "abcde"


class TestCompactLogValue:
    def test_string_truncation(self):
        result = compact_log_value("x" * 50, 10)
        assert "truncated" in result

    def test_dict_recursion(self):
        result = compact_log_value({"key": "x" * 50}, 10)
        assert "truncated" in result["key"]

    def test_list_capped_at_10(self):
        result = compact_log_value(list(range(15)), 500)
        assert len(result) == 11
        assert "5 more" in result[-1]

    def test_non_string_passthrough(self):
        assert compact_log_value(42, 500) == 42
        assert compact_log_value(None, 500) is None


class TestFilterMetadata:
    def test_keeps_allowed_keys(self):
        # `query` is intentionally in ALLOWED_METADATA_KEYS — the refined query
        # is surfaced in the retrieval trace modal for debugging. Keys not in
        # the allowlist (e.g. rawUserText) are dropped.
        out = filter_metadata(
            {
                "chunkCount": 3,
                "topScore": 0.9,
                "latencyMs": 120,
                "query": "shown in trace",
                "rawUserText": "dropped — not allowlisted",
            }
        )
        assert out == {
            "chunkCount": 3,
            "topScore": 0.9,
            "latencyMs": 120,
            "query": "shown in trace",
        }

    def test_non_dict_returns_empty(self):
        assert filter_metadata(None) == {}
        assert filter_metadata("not a dict") == {}
        assert filter_metadata([("key", "value")]) == {}


class TestQueryLogFields:
    def test_includes_hash_and_chars(self):
        fields = query_log_fields("hello", log_query_text=True, max_chars=500)
        assert "query_hash" in fields
        assert fields["query_chars"] == 5
        assert fields["query_preview"] == "hello"

    def test_omits_preview_when_disabled(self):
        fields = query_log_fields("hello", log_query_text=False, max_chars=500)
        assert "query_preview" not in fields


class TestEmitTrace:
    def test_noop_when_ws_is_none(self):
        emit_trace(None, lambda: 1, emit_enabled=True, query_id="q", kind="test")

    def test_noop_when_disabled(self):
        mock_ws = MagicMock()
        emit_trace(mock_ws, lambda: 1, emit_enabled=False, query_id="q", kind="test")
        mock_ws.send_json.assert_not_called()

    def test_swallows_exceptions(self):
        from unittest.mock import patch

        mock_ws = MagicMock()
        with patch("tracing.emitter.asyncio.run", side_effect=RuntimeError("boom")):
            emit_trace(mock_ws, lambda: 1, emit_enabled=True, query_id="q", kind="test")
