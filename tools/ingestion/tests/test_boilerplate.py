"""Tests for boilerplate stripping.

Boilerplate text (headers, footers, navigation links, revision dates)
appears on every page of Wisconsin DOR PDFs. When it survives into chunks
it pollutes vector search. These tests verify that known boilerplate is
stripped while actual content is preserved.
"""


from tools.ingestion.chunking.boilerplate import strip_boilerplate

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
        lpm = [("77.52 SALES AND USE TAXES", 3)]
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


# --- WPAM running header stripping ---


class TestWpamRunningHeaders:
    def test_running_header_stripped_above_threshold(self):
        """Headers appearing >3 times are running headers; only first kept."""
        lpm = []
        for page in range(449, 455):
            lpm.append(("Chapter 14 Agricultural Valuation", page))
            lpm.append(("Content about land use.", page))
        result = strip_boilerplate(lpm, strategy="wpam")
        ch14 = [l for l, _ in result if "Chapter 14" in l]
        assert len(ch14) == 1

    def test_first_occurrence_preserved(self):
        """The preserved header is the first one (true chapter heading)."""
        lpm = []
        for page in range(196, 202):
            lpm.append(("Chapter 9 Real Property Valuation", page))
            lpm.append((f"Content page {page}.", page))
        result = strip_boilerplate(lpm, strategy="wpam")
        ch9_entries = [(l, p) for l, p in result if "Chapter 9" in l]
        assert len(ch9_entries) == 1
        assert ch9_entries[0][1] == 196

    def test_at_threshold_not_stripped(self):
        """Headers appearing exactly 3 times are not treated as running headers."""
        lpm = [
            ("Chapter 16 Real Property Assessment - Special", 613),
            ("Content.", 613),
            ("Chapter 16 Real Property Assessment - Special", 614),
            ("Content.", 614),
            ("Chapter 16 Real Property Assessment - Special", 615),
            ("Content.", 615),
        ]
        result = strip_boilerplate(lpm, strategy="wpam")
        ch16 = [l for l, _ in result if "Chapter 16" in l]
        assert len(ch16) == 3

    def test_multiple_chapters_independent(self):
        """Each chapter's first occurrence is kept independently."""
        lpm = [("Chapter 1 Overview of the Property Tax", 11), ("Content.", 11)]
        for page in range(12, 16):
            lpm.append(("Chapter 1 Overview of the Property Tax", page))
        lpm.append(("Chapter 12 Residential Property Valuation", 309))
        lpm.append(("Content.", 309))
        for page in range(310, 314):
            lpm.append(("Chapter 12 Residential Property Valuation", page))
        result = strip_boilerplate(lpm, strategy="wpam")
        ch1 = [(l, p) for l, p in result if "Chapter 1 " in l]
        ch12 = [(l, p) for l, p in result if "Chapter 12" in l]
        assert len(ch1) == 1
        assert ch1[0][1] == 11
        assert len(ch12) == 1
        assert ch12[0][1] == 309

    def test_not_applied_to_non_wpam_strategy(self):
        """Running header stripping only activates for WPAM strategy."""
        lpm = [
            ("Chapter 14 Agricultural Valuation", 1),
            ("Content.", 1),
            ("Chapter 14 Agricultural Valuation", 2),
            ("Chapter 14 Agricultural Valuation", 3),
            ("Chapter 14 Agricultural Valuation", 4),
            ("Chapter 14 Agricultural Valuation", 5),
        ]
        result = strip_boilerplate(lpm, strategy="general")
        ch14 = [l for l, _ in result if "Chapter 14" in l]
        assert len(ch14) == 5

    def test_lowercase_after_number_not_matched(self):
        """Only uppercase-starting titles are treated as chapter headers."""
        lpm = [
            ("Chapter 14 agricultural notes and references", 1),
            ("Chapter 14 agricultural notes and references", 2),
            ("Chapter 14 agricultural notes and references", 3),
            ("Chapter 14 agricultural notes and references", 4),
            ("Chapter 14 agricultural notes and references", 5),
        ]
        result = strip_boilerplate(lpm, strategy="wpam")
        assert len([l for l, _ in result if "agricultural" in l]) == 5

    def test_content_lines_never_removed(self):
        """Non-header content lines are never affected by the stripping."""
        lpm = [
            ("Chapter 20 Board of Review and Assessment Appeals", 717),
            ("The board of review hears objections to assessments.", 717),
            ("Chapter 20 Board of Review and Assessment Appeals", 718),
            ("Procedures for filing an appeal are described below.", 718),
            ("Chapter 20 Board of Review and Assessment Appeals", 719),
            ("The assessor must provide evidence of value.", 719),
            ("Chapter 20 Board of Review and Assessment Appeals", 720),
            ("Wisconsin Stat. 70.47 governs the process.", 720),
        ]
        result = strip_boilerplate(lpm, strategy="wpam")
        content = [l for l, _ in result if "Chapter 20" not in l]
        assert len(content) == 4

    def test_toc_occurrence_skipped_for_real_chapter_start(self):
        """When the first occurrence is in a TOC (leader-dot context), keep the next non-TOC one."""
        lpm = [
            # TOC area — leader dots surround the chapter heading
            ("Staffing Requirements ..................................", 5),
            ("Chapter 7 Parcel and Information Systems", 5),
            ("Listing ................................................", 5),
            ("Assessment Roll ........................................", 5),
        ]
        # Spacer content to separate TOC from real start
        for i in range(10):
            lpm.append((f"Filler content line {i}.", 50 + i))
        # Real chapter start (no leader dots nearby)
        lpm.append(("Content about prior chapter.", 138))
        lpm.append(("Chapter 7 Parcel and Information Systems", 139))
        lpm.append(("7-1", 139))
        lpm.append(("Content about parcel systems.", 139))
        # More running header occurrences to exceed threshold
        for page in range(140, 155):
            lpm.append(("Chapter 7 Parcel and Information Systems", page))
            lpm.append(("More content.", page))

        result = strip_boilerplate(lpm, strategy="wpam")
        ch7 = [(l, p) for l, p in result if "Chapter 7" in l]
        assert len(ch7) == 1
        assert ch7[0][1] == 139  # kept the real chapter start, not the TOC one

    def test_fallback_to_first_when_all_in_toc(self):
        """If every occurrence is in TOC context, fall back to keeping the first."""
        lpm = [
            ("Intro .................................................", 4),
            ("Chapter 99 Hypothetical Chapter", 4),
            ("Details ................................................", 4),
            ("More dots ..............................................", 5),
            ("Chapter 99 Hypothetical Chapter", 5),
            ("Even more ..............................................", 5),
            ("Chapter 99 Hypothetical Chapter", 6),
            ("Still dotted ...........................................", 6),
            ("Chapter 99 Hypothetical Chapter", 7),
            ("Dotted .................................................", 7),
        ]
        result = strip_boilerplate(lpm, strategy="wpam")
        ch99 = [(l, p) for l, p in result if "Chapter 99" in l]
        assert len(ch99) == 1
        assert ch99[0][1] == 4


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
