"""Tests for boilerplate stripping.

Boilerplate text (headers, footers, navigation links, revision dates)
appears on every page of Wisconsin DOR PDFs. When it survives into chunks
it pollutes vector search. These tests verify that known boilerplate is
stripped while actual content is preserved.
"""

import pytest

from pdf_chunking.boilerplate import strip_boilerplate


# --- General patterns (stripped from all doc types) ---


class TestGeneralPatterns:
    def test_bare_page_number_stripped(self):
        lpm = [("123", 5), ("Content text here.", 5)]
        result = strip_boilerplate(lpm)
        assert result == [("Content text here.", 5)]

    def test_wisconsin_dor_footer_stripped(self):
        lpm = [
            ("Real content about taxation.", 3),
            ("Wisconsin Department of Revenue", 3),
        ]
        result = strip_boilerplate(lpm)
        assert result == [("Real content about taxation.", 3)]

    def test_dor_footer_case_insensitive(self):
        lpm = [("wisconsin department of revenue", 1)]
        result = strip_boilerplate(lpm)
        assert result == []

    def test_back_to_toc_stripped(self):
        lpm = [("Content.", 7), ("Back to table of contents", 7)]
        result = strip_boilerplate(lpm)
        assert result == [("Content.", 7)]

    def test_revision_date_stripped(self):
        lpm = [("Revised 01/2024", 1), ("Body text.", 1)]
        result = strip_boilerplate(lpm)
        assert result == [("Body text.", 1)]

    def test_published_date_stripped(self):
        lpm = [("Published January 15, 2025", 1), ("Body.", 1)]
        result = strip_boilerplate(lpm)
        assert result == [("Body.", 1)]

    def test_real_content_preserved(self):
        lpm = [
            ("The Wisconsin Department of Revenue administers property tax.", 2),
            ("Section 70.32 requires assessments at full value.", 2),
        ]
        result = strip_boilerplate(lpm)
        assert len(result) == 2

    def test_page_number_with_decimal_not_stripped(self):
        lpm = [("123.45", 3)]
        result = strip_boilerplate(lpm)
        assert len(result) == 1

    def test_empty_lines_preserved(self):
        lpm = [("Content.", 1), ("", 1), ("More content.", 1)]
        result = strip_boilerplate(lpm)
        assert ("", 1) in result

    def test_tuple_structure_preserved(self):
        lpm = [("Content line.", 7)]
        result = strip_boilerplate(lpm)
        assert result == [("Content line.", 7)]


# --- Statute-specific patterns ---


class TestStatutePatterns:
    def test_updated_header_stripped(self):
        lpm = [
            ("Updated 2023-24 Wisconsin Statutes", 1),
            ("70.32 General property taxes.", 1),
        ]
        result = strip_boilerplate(lpm, strategy="statute")
        assert len(result) == 1
        assert result[0][0] == "70.32 General property taxes."

    def test_chapter_header_stripped(self):
        lpm = [("Chapter 70", 1), ("Body text.", 1)]
        result = strip_boilerplate(lpm, strategy="statute")
        assert result == [("Body text.", 1)]

    def test_allcaps_running_header_stripped(self):
        lpm = [
            ("77.52 SALES AND USE TAXES", 10),
            ("(1) Imposition. For...", 10),
        ]
        result = strip_boilerplate(lpm, strategy="statute")
        assert len(result) == 1
        assert "(1) Imposition" in result[0][0]

    def test_statute_content_with_subsection_preserved(self):
        lpm = [("70.32(1)(a) Real property.", 5)]
        result = strip_boilerplate(lpm, strategy="statute")
        assert len(result) == 1

    def test_statute_patterns_not_applied_to_wpam(self):
        lpm = [("Chapter 70", 3)]
        result = strip_boilerplate(lpm, strategy="wpam")
        assert len(result) == 1


# --- WPAM-specific patterns ---


class TestWpamPatterns:
    def test_wpam_footer_stripped(self):
        lpm = [
            ("Assessment methodology content.", 2),
            ("Wisconsin Property Assessment Manual", 2),
        ]
        result = strip_boilerplate(lpm, strategy="wpam")
        assert len(result) == 1
        assert result[0][0] == "Assessment methodology content."

    def test_wpam_content_mentioning_manual_preserved(self):
        lpm = [("See Wisconsin Property Assessment Manual Chapter 9 for details.", 4)]
        result = strip_boilerplate(lpm, strategy="wpam")
        assert len(result) == 1

    def test_volume_page_footer_stripped(self):
        lpm = [("Vol. 1, page 2-15", 2), ("Content.", 2)]
        result = strip_boilerplate(lpm, strategy="wpam")
        assert result == [("Content.", 2)]


# --- Tagged content ---


class TestTaggedContent:
    def test_tagged_boilerplate_still_caught(self):
        lpm = [
            ("<titles><<title>><title>Wisconsin Department of Revenue</title><</title>>", 1),
            ("Real content.", 1),
        ]
        result = strip_boilerplate(lpm)
        assert len(result) == 1
        assert result[0][0] == "Real content."

    def test_tagged_real_content_preserved(self):
        lpm = [("<headers><<header>><header>Assessment Process</header><</header>>", 3)]
        result = strip_boilerplate(lpm)
        assert len(result) == 1


# --- Strategy routing ---


class TestStrategyRouting:
    def test_general_patterns_always_active(self):
        lpm = [("Wisconsin Department of Revenue", 1)]
        for strategy in ("statute", "wpam", "general"):
            result = strip_boilerplate(lpm, strategy=strategy)
            assert len(result) == 0, f"Not stripped for {strategy}"

    def test_unknown_strategy_uses_general_only(self):
        lpm = [("Wisconsin Department of Revenue", 1), ("Chapter 70", 2)]
        result = strip_boilerplate(lpm, strategy="unknown")
        assert len(result) == 1
        assert result[0][0] == "Chapter 70"
