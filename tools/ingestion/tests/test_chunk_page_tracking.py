"""Tests for page-tracking in chunk_document.

The old chunker used ``get_pages_for_chunk`` which substring-matched chunk
body lines against the entire document's line→page map. Boilerplate footers
like "Wisconsin Department of Revenue" appear on every page, so chunk
page ranges exploded to cover the whole document (55% of deployed chunks
spanned >10 pages). The rewrite tracks ``(line, page)`` pairs through
buffer building, so page ranges reflect the chunk's ACTUAL source.
"""

import pytest

from tools.ingestion.chunking.pdfChunker import chunk_document


def _make_tagged_title(text: str) -> str:
    return f"<titles><<title>><title>{text}</title><</title>>"


def _make_lpm(pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Shim: caller writes ``[(text, page), ...]`` directly."""
    return pairs


def _join_header_split(lpm: list[tuple[str, int]]) -> list[str]:
    """Reproduce pymupdf_extractor's split-on-``<titles>``."""
    full = "\n".join(text for text, _ in lpm)
    return full.split("<titles>")


# --- Test: repeating footer doesn't inflate the range ---


def test_repeating_footer_does_not_inflate_page_range() -> None:
    lpm = _make_lpm([
        (_make_tagged_title("I. Overview"), 5),
        ("This is the overview body text on page five.", 5),
        ("Wisconsin Department of Revenue", 5),
        (_make_tagged_title("II. Details"), 6),
        ("Details live on page six only.", 6),
        ("Wisconsin Department of Revenue", 6),
        (_make_tagged_title("III. Appendix"), 50),
        ("Appendix starts on page fifty.", 50),
        ("Wisconsin Department of Revenue", 50),
    ])

    chunks = chunk_document(_join_header_split(lpm), "doc.pdf", "BUCKET", lpm)

    # The "II. Details" chunk should be page 6, not 5-50.
    details = next(c for c in chunks if c["metadata"]["heading"].startswith("II."))
    assert details["metadata"]["start_page"] == 6
    assert details["metadata"]["end_page"] == 6


def test_chunk_spans_only_its_actual_pages() -> None:
    """A chunk that genuinely spans multiple pages gets the right range."""
    lpm = _make_lpm([
        (_make_tagged_title("V. Assessment Process"), 7),
        ("Classification affects tax.", 7),
        ("Continued discussion of classes.", 8),
        ("Final paragraph of the section.", 9),
        (_make_tagged_title("VI. Next Section"), 10),
        ("New topic here.", 10),
    ])

    chunks = chunk_document(_join_header_split(lpm), "doc.pdf", "BUCKET", lpm)

    v_chunk = next(c for c in chunks if c["metadata"]["heading"].startswith("V."))
    assert v_chunk["metadata"]["start_page"] == 7
    assert v_chunk["metadata"]["end_page"] == 9


# --- Test: TOC lines don't become headings ---


def test_toc_line_rejected_as_heading() -> None:
    """A roman-numeral line with dot-leaders is a TOC entry, not a heading."""
    # TOC on page 2
    lpm = _make_lpm([
        (_make_tagged_title("Table of Contents"), 2),
        ("I. Overview . . . . . . . . . . . . . . . . . . . . . . . 5", 2),
        ("II. Details . . . . . . . . . . . . . . . . . . . . . . 6", 2),
        ("XIX. Contact Information . . . . . . . . . . . . . 41", 2),
        # Real content starts on page 5
        (_make_tagged_title("I. Overview"), 5),
        ("Actual overview text here.", 5),
        (_make_tagged_title("XIX. Contact Information"), 41),
        ("Department of Revenue Equalization District Offices.", 41),
        ("eqleau@wisconsin.gov", 41),
    ])

    chunks = chunk_document(_join_header_split(lpm), "doc.pdf", "BUCKET", lpm)

    # There should be a chunk with heading "XIX. Contact Information" that is
    # exactly that text — NOT the TOC form with dots and a page number.
    xix_chunks = [c for c in chunks if "XIX" in (c["metadata"].get("heading") or "")]
    assert len(xix_chunks) >= 1
    # None of them should have a heading that contains dot-leaders.
    for c in xix_chunks:
        h = c["metadata"]["heading"]
        # A genuine heading should not have 5+ consecutive dots
        assert "..... ..... " not in h, f"heading looks like TOC: {h!r}"
        assert ". . . . . . " not in h, f"heading looks like TOC: {h!r}"

    # And the real content chunk should be attributed to page 41, not page 2.
    content_chunk = next(
        c for c in xix_chunks
        if "Department of Revenue" in c["text"]
    )
    assert content_chunk["metadata"]["start_page"] == 41
    assert content_chunk["metadata"]["end_page"] == 41


def test_toc_body_chunk_not_mixed_with_real_content() -> None:
    """Before the fix, chunk 18 had heading 'XIX.' with body that was TOC dots,
    claiming pages 2-41. After fix, the TOC and content must live in separate
    chunks with their real page attributions."""
    lpm = _make_lpm([
        (_make_tagged_title("Table of Contents"), 2),
        ("XIX. Contact Information . . . . . . . . . . . 41", 2),
        (_make_tagged_title("XIX. Contact Information"), 41),
        ("Eau Claire District Office.", 41),
    ])

    chunks = chunk_document(_join_header_split(lpm), "doc.pdf", "BUCKET", lpm)

    # Find any chunk with page range straddling 2-41 — that's the bug.
    for c in chunks:
        sp = c["metadata"]["start_page"]
        ep = c["metadata"]["end_page"]
        assert ep - sp <= 2, (
            f"chunk spans pages {sp}-{ep}: heading={c['metadata']['heading']!r}"
        )


# --- Test: empty / edge cases ---


def test_no_content_produces_no_chunks() -> None:
    chunks = chunk_document([], "doc.pdf", "BUCKET", [])
    assert chunks == []


def test_content_without_heading() -> None:
    """Document with no roman-numeral headings — all content becomes one
    heading-less chunk with correct page range."""
    lpm = _make_lpm([
        ("Plain text line one.", 3),
        ("Plain text line two.", 3),
        ("Plain text line three.", 4),
    ])
    chunks = chunk_document(_join_header_split(lpm), "doc.pdf", "BUCKET", lpm)
    assert len(chunks) >= 1
    # All content chunks should cover only pages 3-4
    for c in chunks:
        assert 3 <= c["metadata"]["start_page"] <= c["metadata"]["end_page"] <= 4
