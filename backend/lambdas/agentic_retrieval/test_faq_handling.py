"""Tests for the faq_handling module."""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic")


class FakeFAQ(pydantic.BaseModel):
    faq_id: str
    question: str
    answer: str
    source_url: str | None = None


class FakeFAQResource(pydantic.BaseModel):
    faqs: list[FakeFAQ]


sys.modules["step_function_types.models"].FAQ = FakeFAQ
sys.modules["step_function_types.models"].FAQResource = FakeFAQResource

from faq_handling import (
    build_cited_faq_resource,
    build_faq_resource,
    faq_id_from_uri,
    faq_search_direct,
    lookup_faq_url,
    normalize_faq_question,
    parse_faq_text,
)


class TestNormalizeFaqQuestion:
    def test_basic_normalization(self):
        assert normalize_faq_question("What is TID?") == "what is tid"

    def test_strips_trailing_punctuation(self):
        assert normalize_faq_question("ends with q mark and dot?.") == "ends with q mark and dot"

    def test_collapses_whitespace(self):
        assert normalize_faq_question("  a\tb\n c  ") == "a b c"

    def test_non_breaking_space(self):
        assert normalize_faq_question("Non\xa0breaking\xa0space?") == "non breaking space"

    def test_zero_width_chars(self):
        assert normalize_faq_question("zero​width") == "zerowidth"

    def test_bom_prefix(self):
        assert normalize_faq_question("﻿BOM prefix") == "bom prefix"

    def test_empty(self):
        assert normalize_faq_question("") == ""

    def test_matches_seed_script(self):
        """Normalization must match tools/graphrag/faq_url_map.py."""
        import importlib.util

        script_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "tools", "graphrag", "faq_url_map.py"
        )
        if not os.path.exists(script_path):
            pytest.skip("faq_url_map.py not found")

        spec = importlib.util.spec_from_file_location("faq_url_map", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        cases = [
            "What is the homestead credit?",
            "Already clean.",
            "  leading and trailing  ",
            "a\tb\n c",
            "Non\xa0breaking\xa0space?",
            "zero​width",
            "﻿BOM prefix",
            "",
            "MiXeD CaSe Question???",
        ]
        for text in cases:
            assert normalize_faq_question(text) == mod.normalize_question(text), f"drift on {text!r}"


class TestParseFaqText:
    def test_valid_qa(self):
        result = parse_faq_text("Q: What is X?\nA: It is Y.")
        assert result == ("What is X?", "It is Y.")

    def test_multiline_answer(self):
        result = parse_faq_text("Q: Question\nA: Line 1\nLine 2")
        assert result == ("Question", "Line 1\nLine 2")

    def test_malformed_returns_none(self):
        assert parse_faq_text("no Q/A format here") is None
        assert parse_faq_text("") is None


class TestFaqIdFromUri:
    def test_normal_uri(self):
        assert faq_id_from_uri("s3://bucket/path/faq_1.txt") == "faq_1"

    def test_empty(self):
        assert faq_id_from_uri("") == "faq"

    def test_no_extension(self):
        assert faq_id_from_uri("s3://bucket/faq_2") == "faq_2"


class TestBuildFaqResource:
    def test_parses_valid_entries(self):
        results = [
            {"text": "Q: Is X a Y?\nA: Yes it is.", "source_uri": "s3://b/faq_1.txt"},
            {"text": "Q: Second?\nA: Also yes.", "source_uri": "s3://b/faq_2.txt"},
        ]
        with patch("faq_handling.lookup_faq_url", return_value=None):
            resource = build_faq_resource(results)
        assert resource is not None
        assert len(resource.faqs) == 2
        assert resource.faqs[0].faq_id == "faq_1"
        assert resource.faqs[0].question == "Is X a Y?"

    def test_skips_unparseable(self):
        results = [
            {"text": "no Q/A format", "source_uri": "s3://b/faq_1.txt"},
        ]
        with patch("faq_handling.lookup_faq_url", return_value=None):
            resource = build_faq_resource(results)
        assert resource is None

    def test_attaches_source_url(self):
        results = [
            {"text": "Q: Is X a Y?\nA: Yes.", "source_uri": "s3://b/faq_1.txt"},
        ]
        with patch("faq_handling.lookup_faq_url", return_value="https://revenue.wi.gov/x"):
            resource = build_faq_resource(results)
        assert resource.faqs[0].source_url == "https://revenue.wi.gov/x"

    def test_caps_at_max_faqs(self):
        results = [
            {"text": f"Q: Q{i}?\nA: A{i}.", "source_uri": f"s3://b/faq_{i}.txt"}
            for i in range(10)
        ]
        with patch("faq_handling.lookup_faq_url", return_value=None):
            resource = build_faq_resource(results)
        assert len(resource.faqs) == 3  # MAX_FAQS


class TestBuildCitedFaqResource:
    def test_filters_to_cited_ids(self):
        results = [
            {"text": "Q: A?\nA: B.", "source_uri": "s3://b/faq_1.txt"},
            {"text": "Q: C?\nA: D.", "source_uri": "s3://b/faq_2.txt"},
        ]
        with patch("faq_handling.lookup_faq_url", return_value=None):
            resource = build_cited_faq_resource(results, {"faq_1"})
        assert resource is not None
        assert len(resource.faqs) == 1
        assert resource.faqs[0].faq_id == "faq_1"


class TestLookupFaqUrl:
    def test_returns_none_when_table_unconfigured(self):
        with patch("faq_handling.FAQ_URL_TABLE", ""):
            assert lookup_faq_url("anything") is None

    def test_returns_url_on_match(self):
        fake_table = MagicMock()
        fake_table.get_item.return_value = {
            "Item": {"normalized_question": "what is x", "source_url": "https://example.com"}
        }
        with patch("faq_handling.FAQ_URL_TABLE", "FaqTable"), \
             patch("faq_handling._faq_url_table", return_value=fake_table):
            assert lookup_faq_url("What is X?") == "https://example.com"

    def test_returns_none_on_miss(self):
        fake_table = MagicMock()
        fake_table.get_item.return_value = {}
        with patch("faq_handling.FAQ_URL_TABLE", "FaqTable"), \
             patch("faq_handling._faq_url_table", return_value=fake_table):
            assert lookup_faq_url("Unknown?") is None


class TestFaqSearchDirect:
    def test_calls_execute_tool(self):
        mock_neptune = MagicMock()
        mock_execute = MagicMock(return_value={"faqs": [], "count": 0})
        result = faq_search_direct("test query", mock_neptune, mock_execute)
        mock_execute.assert_called_once_with("faq_search", {"query": "test query"}, mock_neptune)
        assert result == {"faqs": [], "count": 0}
