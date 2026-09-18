"""Tests for the WPAM TOC heuristic in the WPAM chunker.

Regression cover for the bug that discarded ~89% of every WPAM Volume 1
edition: the heuristic ran on the flushed chunk text (which has the
"Chapter N ..." heading prepended) and dropped anything matching
``^Chapter N`` plus any ``\\d+-\\d+`` token. Every WPAM page carries a
running footer like "7-40", so ordinary prose was thrown away on the
strength of a single footer line. A bare keyword test ("appendix",
"glossary", "revisions") dropped further real body content — including the
glossary itself, where definitions such as "clear height" live.

The replacement is structural: dot-leader runs, or a high share of short
bare page-reference lines.
"""

from tools.ingestion.chunking.pdfChunker import chunk_document_wpam, wpam_is_probably_toc

PROSE_LINES = [
    "The assessor must value all taxable real property at full value as of "
    f"January 1 of the assessment year, using the best information available ({i}).".replace(
        "  ", " "
    )
    for i in range(30)
]


def _chunk_wpam(lines: list[str], page: int = 40) -> list[dict]:
    """Run the production WPAM chunker over (line, page) pairs."""
    mapping = [(line, page) for line in lines]
    return chunk_document_wpam(["\n".join(lines)], "wpam-2026", "test-bucket", mapping)


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


class TestWpamIsProbablyToc:
    def test_single_footer_line_does_not_trigger(self):
        assert wpam_is_probably_toc([*PROSE_LINES, "7-40"]) is False

    def test_leader_dot_lines_flagged(self):
        lines = [
            "Introduction . . . . . . . . . . . . . . . . 1-1",
            "Assessment Process . . . . . . . . . . . . 2-1",
            "Property Tax Cycle . . . . . . . . . . . . 3-1",
        ]
        assert wpam_is_probably_toc(lines) is True

    def test_two_leader_lines_not_enough(self):
        lines = [
            "Introduction . . . . . . . . . . . . . . . . 1-1",
            "Assessment Process . . . . . . . . . . . . 2-1",
            *PROSE_LINES,
        ]
        assert wpam_is_probably_toc(lines) is False

    def test_bare_page_reference_block_flagged(self):
        # A TOC column whose leader dots did not survive extraction.
        lines = ["1-1", "2-14", "3-7", "4-22", "Overview", "Valuation"]
        assert wpam_is_probably_toc(lines) is True

    def test_prose_containing_a_range_not_flagged(self):
        lines = [
            "See the discussion of classification at pages 7-40 through 7-52 of this chapter.",
            "The board of review must follow the procedure in sec. 70.47, Wis. Stats.",
            "Assessors should document each adjustment applied to a comparable sale.",
            "A property owner may object to the assessed value at the board of review.",
            "The clerk publishes the notice of the board of review meeting.",
        ]
        assert wpam_is_probably_toc(lines) is False

    def test_short_buffer_not_flagged(self):
        assert wpam_is_probably_toc(["7-40"]) is False
        assert wpam_is_probably_toc([]) is False

    def test_keywords_alone_never_trigger(self):
        for word in ("appendix", "glossary", "revisions", "table of contents"):
            lines = [f"The {word} is discussed in the paragraphs that follow.", *PROSE_LINES]
            assert wpam_is_probably_toc(lines) is False, word


# ---------------------------------------------------------------------------
# End-to-end through chunk_document_wpam
# ---------------------------------------------------------------------------


class TestChunkDocumentWpam:
    def test_prose_with_footer_is_kept(self):
        chunks = _chunk_wpam(["Chapter 7 Property Classification", *PROSE_LINES, "7-40"])
        assert chunks, "chapter heading + prose + one footer line must survive"
        body = "\n".join(c["text"] for c in chunks)
        assert "full value as of" in body
        assert chunks[0]["metadata"]["heading"] == "Chapter 7 Property Classification"

    def test_front_matter_toc_is_dropped(self):
        toc_lines = [
            "Chapter 1 Introduction . . . . . . . . . . . . . . . . 1-1",
            "Chapter 2 Assessment Process . . . . . . . . . . . . . 2-1",
            "Chapter 3 Property Tax Cycle . . . . . . . . . . . . . 3-1",
            "Chapter 4 Valuation . . . . . . . . . . . . . . . . . 4-1",
        ]
        assert _chunk_wpam(toc_lines) == []

    def test_glossary_body_is_kept(self):
        lines = [
            "Chapter 24 Glossary",
            "Clear height - the vertical distance from the finished floor to the lowest "
            "point of the roof structure or the bottom of any overhead obstruction.",
            *PROSE_LINES,
            "24-3",
        ]
        chunks = _chunk_wpam(lines)
        body = "\n".join(c["text"] for c in chunks)
        assert "Clear height" in body

    def test_prose_mentioning_appendix_is_kept(self):
        lines = [
            "Chapter 12 Assessment Roll",
            "A sample assessment roll is reproduced in the appendix to this chapter, "
            "and the appendix also lists the statutory deadlines.",
            *PROSE_LINES,
            "12-8",
        ]
        chunks = _chunk_wpam(lines)
        body = "\n".join(c["text"] for c in chunks)
        assert "appendix" in body
