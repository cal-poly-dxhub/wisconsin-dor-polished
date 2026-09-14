"""Tests for the statute chunker's split logic.

The statute chunker splits by section header (70.XX, Tax 16.01, etc.) then
splits oversized sections at subsection boundaries → sentence boundaries →
line breaks. Tiny tail fragments are merged back into the predecessor.
"""

from __future__ import annotations

import re

from tools.ingestion.chunking.pdfChunker import (
    CHUNK_MAX_CHARS,
    STATUTE_SUBSECTION_TARGET_CHARS,
    _split_section_by_subsections,
    _split_statute_section,
    chunk_document_admin_rule,
    chunk_document_statute,
    get_chunk_cap,
)


def _statute_mapping(sections: list[tuple[str, list[tuple[str, int]]]]):
    """Build a line_page_mapping from (heading, [(line, page), ...]) pairs."""
    mapping = []
    for heading, lines in sections:
        mapping.append((heading, lines[0][1] if lines else 1))
        for line, page in lines:
            mapping.append((line, page))
    return mapping


class TestSplitStatuteSection:
    """Unit tests for the _split_statute_section helper."""

    def test_short_text_returns_single_chunk(self):
        text = "70.05 Short section.\n(1) One subsection only."
        parts = _split_statute_section(text)
        assert parts == [text]

    def test_splits_at_subsection_boundaries(self):
        sub1 = "(1) " + "First subsection content. " * 70  # ~1820 chars
        sub2 = "(2) " + "Second subsection content. " * 70  # ~1890 chars
        text = f"70.05 Heading\n{sub1}\n{sub2}"
        assert len(text) > CHUNK_MAX_CHARS

        parts = _split_statute_section(text)
        assert len(parts) == 2
        assert parts[0].startswith("70.05 Heading")
        assert "(1)" in parts[0]
        assert parts[1].startswith("(2)")
        for p in parts:
            assert len(p) <= CHUNK_MAX_CHARS

    def test_greedy_merges_small_subsections(self):
        subs = [f"({i}) Small sub {i}. Content here." for i in range(1, 6)]
        text = "70.11 Heading\n" + "\n".join(subs)
        assert len(text) < CHUNK_MAX_CHARS

        parts = _split_statute_section(text)
        assert len(parts) == 1
        assert parts[0] == text

    def test_falls_back_to_sentence_split_when_no_subsections(self):
        sentences = [
            f"Sentence {i} describes a tax assessment rule in detail.  " for i in range(80)
        ]
        text = "70.32 Long section no subsections\n" + "".join(sentences)
        assert len(text) > CHUNK_MAX_CHARS

        parts = _split_statute_section(text)
        assert len(parts) >= 2
        for p in parts:
            assert len(p) <= CHUNK_MAX_CHARS
        # Split should land after a period, not mid-word
        for p in parts[:-1]:
            assert p.rstrip().endswith(".")

    def test_merges_tiny_tails_into_predecessor(self):
        sub1 = "(1) " + "Long content here. " * 90  # ~1710 chars
        tiny_tail = "(2) Short."  # 10 chars
        text = f"70.99 Heading\n{sub1}\n{tiny_tail}"

        parts = _split_statute_section(text)
        # The tiny tail should be merged back, not standalone
        assert all(len(p) >= 200 or len(parts) == 1 for p in parts)

    def test_no_content_lost(self):
        subs = [f"({i}) Content block {i}. " + "x " * 200 for i in range(1, 8)]
        text = "70.11 Heading\n" + "\n".join(subs)

        parts = _split_statute_section(text)
        rejoined = "\n".join(parts)
        for i in range(1, 8):
            assert f"Content block {i}" in rejoined

    def test_handles_lettered_subsections(self):
        sub_a = "(a) " + "Lettered subsection A describes property tax rules. " * 50
        sub_b = "(b) " + "Lettered subsection B describes assessment methods. " * 50
        text = f"70.11 Heading\n{sub_a}\n{sub_b}"
        assert len(text) > CHUNK_MAX_CHARS

        parts = _split_statute_section(text)
        assert len(parts) >= 2
        # Tail merge allows up to 3000 to avoid orphan fragments
        for p in parts:
            assert len(p) <= 3000


class TestChunkDocumentStatute:
    """Integration tests for chunk_document_statute."""

    def test_no_chunk_exceeds_cap(self):
        """Statute sections that exceed the cap get split correctly."""
        lines = [(f"Rule detail sentence {i}. " * 3, 1 + i // 10) for i in range(100)]
        mapping = _statute_mapping(
            [
                ("70.05 Valuation of property", lines[:50]),
                ("70.07 Board of assessors", lines[50:]),
            ]
        )

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        statute_cap = get_chunk_cap("statute")
        for c in chunks:
            assert len(c["text"]) <= statute_cap, (
                f"Chunk exceeds cap: {len(c['text'])} > {statute_cap}"
            )

    def test_no_duplicate_ids_after_split(self):
        """Each chunk in the output has unique position (no ID collisions)."""
        lines = [(f"Content line {i}.", 1) for i in range(80)]
        mapping = _statute_mapping(
            [
                ("70.11 Exemptions", lines),
            ]
        )

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        # chunk_document_statute doesn't assign chunk_ids, but each chunk
        # should be a distinct text segment
        texts = [c["text"] for c in chunks]
        assert len(texts) == len(set(texts))

    def test_no_tiny_orphan_fragments(self):
        """No chunk should be a tiny fragment (<100 chars) from a bad split."""
        sub1 = "\n".join([f"(1) Long rule text line {i}." for i in range(40)])
        sub2 = "\n".join([f"(2) Another rule line {i}." for i in range(40)])
        mapping = [("70.05 Big section", 1)]
        for line in sub1.split("\n") + sub2.split("\n"):
            mapping.append((line, 1))

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        tiny = [c for c in chunks if len(c["text"]) < 100]
        assert len(tiny) == 0, (
            f"Found {len(tiny)} tiny orphan fragments: {[c['text'][:50] for c in tiny]}"
        )

    def test_preserves_all_content(self):
        """No text is lost during splitting."""
        markers = [f"MARKER_{i:03d}" for i in range(50)]
        lines = [(f"{m} rule content here.", 3 + i // 5) for i, m in enumerate(markers)]
        mapping = _statute_mapping(
            [
                ("70.32 Section with markers", lines),
            ]
        )

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        all_text = "\n".join(c["text"] for c in chunks)
        for m in markers:
            assert m in all_text, f"Lost content marker: {m}"

    def test_short_section_stays_intact(self):
        """A section under the cap is emitted as one chunk."""
        lines = [("Brief content.", 3), ("More content.", 3)]
        mapping = _statute_mapping([("70.01 Short section", lines)])

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        assert len(chunks) == 1
        assert "70.01" in chunks[0]["text"]

    def test_metadata_preserved_on_split(self):
        """Split chunks retain the original section's page metadata."""
        lines = [(f"Line {i} of content.", 3 + i // 20) for i in range(60)]
        mapping = _statute_mapping([("70.47 Board of review", lines)])

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        for c in chunks:
            assert c["metadata"]["start_page"] == 3
            assert c["metadata"]["heading"] == "70.47 Board of review"

    def test_admin_rule_pattern(self):
        """Administrative code (Tax XX.XX) now routes to chunk_document_admin_rule."""
        lines = [(f"Rule detail {i}. " * 4, 1) for i in range(60)]
        mapping = _statute_mapping(
            [
                ("Tax 16.01 Scope", lines[:30]),
                ("Tax 16.02 Definitions", lines[30:]),
            ]
        )

        chunks = chunk_document_admin_rule(None, "admin-rules-tax-16.pdf", "bucket", mapping)

        assert len(chunks) >= 2
        for c in chunks:
            assert len(c["text"]) <= CHUNK_MAX_CHARS

    def test_merges_multi_page_duplicates(self):
        """Same heading appearing on multiple pages gets merged."""
        # Headings carry a title: a bare "70.11" at the top of a page is
        # treated as a running header, not a section start.
        mapping = [
            ("70.11 Property exempted", 5),
            ("First page content.", 5),
            ("70.11 Property exempted", 6),
            ("Second page content.", 6),
            ("70.12 Next", 7),
            ("Next section.", 7),
        ]

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        section_11 = [c for c in chunks if "70.11" in c["text"]]
        assert len(section_11) == 1
        assert "First page content" in section_11[0]["text"]
        assert "Second page content" in section_11[0]["text"]

    def test_cross_references_not_treated_as_headings(self):
        """Cross-references to other chapters don't split sections."""
        # Pages 1-2 are treated as TOC pages, so body starts on page 3.
        mapping = [
            ("70.01 General property taxes", 3),
            ("All property subject to taxation under ch. 70.", 3),
            ("292.31 (8) (i) or 292.81, and is effective as of January 1 in the", 3),
            ("year of the assessment shall be valued as if not contaminated.", 3),
            ("70.02 Definition of general property", 4),
            ("General property is all taxable property.", 4),
        ]

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        headings = [c["metadata"]["heading"] for c in chunks]
        assert "70.01 General property taxes" in headings
        assert "70.02 Definition of general property" in headings
        # Cross-reference should NOT be its own heading
        assert all("292.31" not in h for h in headings)
        # The cross-reference text should be part of the 70.01 chunk
        chunk_70_01 = next(
            c for c in chunks if c["metadata"]["heading"] == "70.01 General property taxes"
        )
        assert "292.31" in chunk_70_01["text"]

    def test_decimal_numbers_not_treated_as_headings(self):
        """Decimal numbers like '0.0267' don't trigger heading splits."""
        mapping = [
            ("70.50 Interest rate", 3),
            ("The interest rate is", 3),
            ("0.0267 percent per day for the period of time between", 3),
            ("the due date and the payment date.", 3),
        ]

        chunks = chunk_document_statute(None, "statutes-70.pdf", "bucket", mapping)

        assert len(chunks) == 1
        assert chunks[0]["metadata"]["heading"] == "70.50 Interest rate"
        assert "0.0267" in chunks[0]["text"]


class TestChunkDocumentAdminRule:
    """Tests for admin-rule-specific chunker (Tax XX.XX pattern)."""

    def test_toc_stubs_dropped(self):
        """TOC entries with no real body are dropped."""
        mapping = [
            ("Tax 12.05", 1),
            ("Temporary assessor certification.", 1),
            ("Tax 12.06", 1),
            ("Duties of assessors.", 1),
            ("Tax 12.07", 1),
            ("Assessment districts.", 1),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-12.pdf", "bucket", mapping)
        assert len(chunks) == 0

    def test_toc_merges_with_body(self):
        """TOC stub for a rule merges into its real body section."""
        body_text = "APPROVAL.  Temporary assessor certification shall be " + "x" * 100
        mapping = [
            # TOC entry
            ("Tax 12.05", 1),
            ("Temporary assessor certification.", 1),
            # Other TOC entries in between
            ("Tax 12.06", 1),
            ("Duties of assessors.", 1),
            # Real body for 12.05 later in the document
            ("Tax 12.05 Temporary assessor certification.  (1)", 2),
            (body_text, 2),
            # Real body for 12.06
            ("Tax 12.06 Duties of assessors.  The following levels of", 3),
            ("certification are established: " + "y" * 100, 3),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-12.pdf", "bucket", mapping)

        rule_ids = [c["metadata"]["heading"] for c in chunks]
        assert "Tax 12.05" in rule_ids
        assert "Tax 12.06" in rule_ids

        ch_05 = next(c for c in chunks if c["metadata"]["heading"] == "Tax 12.05")
        assert "APPROVAL" in ch_05["text"]
        assert ch_05["metadata"]["start_page"] == 1
        assert ch_05["metadata"]["end_page"] == 2

    def test_page_continuation_merged(self):
        """Same rule appearing across pages is merged into one chunk."""
        mapping = [
            ("Tax 18.05 Valuation methods.", 1),
            ("The department shall use " + "a" * 100, 1),
            # Page break, rule restated by running header
            ("Tax 18.05", 2),
            ("the following approaches " + "b" * 100, 2),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-18.pdf", "bucket", mapping)

        assert len(chunks) == 1
        assert chunks[0]["metadata"]["heading"] == "Tax 18.05"
        assert chunks[0]["metadata"]["start_page"] == 1
        assert chunks[0]["metadata"]["end_page"] == 2
        assert "department shall use" in chunks[0]["text"]
        assert "following approaches" in chunks[0]["text"]

    def test_no_chunk_exceeds_cap(self):
        """All output chunks respect the admin_rule cap."""
        body = "Detail about assessment rules. " * 200
        mapping = [
            ("Tax 20.03 Assessment requirements.", 1),
            (body, 1),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-20.pdf", "bucket", mapping)

        cap = get_chunk_cap("admin_rule")
        for c in chunks:
            assert len(c["text"]) <= cap

    def test_boilerplate_in_body_not_treated_as_heading(self):
        """Lines like 'WISCONSIN ADMINISTRATIVE CODE' don't create new chunks
        (they should be stripped by boilerplate, but even if they survive,
        they shouldn't match the Tax XX.XX rule pattern)."""
        mapping = [
            ("Tax 12.06 Duties of assessors.", 1),
            ("Certification levels are established: " + "z" * 80, 1),
            ("WISCONSIN ADMINISTRATIVE CODE", 2),
            ("Additional content about assessor duties " + "w" * 80, 2),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-12.pdf", "bucket", mapping)

        assert len(chunks) == 1
        assert "WISCONSIN ADMINISTRATIVE CODE" in chunks[0]["text"]


# ---------------------------------------------------------------------------
# One-subsection-per-chunk splitting for dense enumerated sections
# ---------------------------------------------------------------------------

# Mirrors backend/lambdas/agentic_retrieval/agent_tools/executor.py
# _SUBSECTION_MARKER_RE — get_section's subsection mode depends on it.
_EXECUTOR_MARKER_RE = re.compile(r"(?m)^\s*\((?P<marker>\d+[a-z]*)\)\s")

_HEADING = "70.11 Property exempted from taxation."


def _sub(marker: str, title: str, chars: int) -> str:
    """One numbered subsection of roughly ``chars`` characters."""
    body = f"({marker}) {title}.  "
    filler = f"Property used for {title.lower()} purposes is exempt. "
    while len(body) < chars:
        body += filler
    return body


def _section_mapping(heading: str, body: str, page: int = 5):
    mapping = [(heading, page)]
    mapping.extend((line, page) for line in body.split("\n"))
    return mapping


class TestSubsectionSplit:
    def test_six_subsections_become_own_chunks(self):
        subs = [_sub(str(i), f"TITLE {i}", 600) for i in range(1, 7)]
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, "\n".join(subs))
        )

        # Whole section is ~3600 chars, but we split by subsection regardless
        # of the 3500 cap; two 600-char subs exceed the 1200 target so each
        # stands alone (or at most pairs up).
        assert 3 <= len(chunks) <= 6
        seen: list[str] = []
        for c in chunks:
            assert c["text"].split("\n", 1)[0] == _HEADING
            assert c["metadata"]["heading"] == _HEADING
            markers = [m.group("marker") for m in _EXECUTOR_MARKER_RE.finditer(c["text"])]
            assert markers, c["text"][:80]
            label = c["metadata"]["subheading"]
            if len(markers) == 1:
                assert label == f"70.11({markers[0]})"
            else:
                assert label == f"70.11({markers[0]})–({markers[-1]})"
            assert c["metadata"]["start_page"] == 5
            seen.extend(markers)
        assert seen == ["1", "2", "3", "4", "5", "6"]

    def test_1500_char_subsection_stands_alone(self):
        subs = [_sub("1", "FIRST", 1500), _sub("2", "SECOND", 1500), _sub("3", "THIRD", 100)]
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, "\n".join(subs))
        )
        labels = [c["metadata"]["subheading"] for c in chunks]
        assert labels[0] == "70.11(1)"
        assert labels[1] == "70.11(2)"
        assert len(chunks[0]["text"]) < 1700

    def test_tiny_subsections_merge_up_to_target(self):
        subs = [_sub(str(i), f"SMALL {i}", 100) for i in range(1, 31)]  # ~3000+ chars
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, "\n".join(subs))
        )

        assert 2 <= len(chunks) < 30
        for c in chunks:
            body = c["text"].split("\n", 1)[1]
            assert len(body) <= STATUTE_SUBSECTION_TARGET_CHARS
            assert c["text"].startswith(_HEADING + "\n")
        # Merged ranges are labeled first–last.
        first = chunks[0]["metadata"]["subheading"]
        assert re.fullmatch(r"70\.11\(1\)–\(\d+\)", first), first
        # Every marker survives, once, in order.
        all_markers = [
            m.group("marker") for c in chunks for m in _EXECUTOR_MARKER_RE.finditer(c["text"])
        ]
        assert all_markers == [str(i) for i in range(1, 31)]

    def test_single_subsection_unchanged(self):
        body = "(1) ONLY ONE.  Some short text.\nMore text."
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, body)
        )
        assert len(chunks) == 1
        assert chunks[0]["text"] == f"{_HEADING}\n{body}"
        assert "subheading" not in chunks[0]["metadata"]

    def test_no_markers_unchanged(self):
        body = "Plain prose without subsections.\nSecond line."
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, body)
        )
        assert len(chunks) == 1
        assert chunks[0]["text"] == f"{_HEADING}\n{body}"

    def test_lettered_paragraphs_stay_inside_numbered_subsection(self):
        sub1 = "(1) FIRST.  Intro.\n(a) Paragraph a " + "x" * 500 + "\n(b) Paragraph b " + "y" * 500
        sub2 = "(2) SECOND.  " + "z" * 700
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, f"{sub1}\n{sub2}")
        )
        assert [c["metadata"]["subheading"] for c in chunks] == ["70.11(1)", "70.11(2)"]
        assert "(a) Paragraph a" in chunks[0]["text"]
        assert "(b) Paragraph b" in chunks[0]["text"]

    def test_intro_text_attached_to_first_chunk(self):
        intro = "The property described in this section is exempted from general\nproperty taxes:"
        subs = [_sub("1", "FIRST", 900), _sub("2", "SECOND", 900)]
        chunks = chunk_document_statute(
            None,
            "statutes-70.pdf",
            "bucket",
            _section_mapping(_HEADING, intro + "\n" + "\n".join(subs)),
        )
        assert len(chunks) == 2
        first = chunks[0]["text"]
        assert first.startswith(_HEADING + "\n" + intro + "\n(1) FIRST.")
        assert chunks[0]["metadata"]["subheading"] == "70.11(1)"
        # No standalone intro chunk.
        assert all("subheading" in c["metadata"] for c in chunks)

    def test_inline_first_marker_is_lifted_to_line_start(self):
        """'70.32 Real estate, how valued.  (1) Real property ...' — the (1)
        sits on the heading line in the PDF; it must end up at line start so
        get_section's subsection mode can find it, and heading becomes
        title-only (still prefix-matchable by section number)."""
        heading = "70.32 Real estate, how valued.  (1) Real property shall be"
        body = "valued by the assessor " + "v" * 300 + "\n(2) In this section " + "w" * 300
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(heading, body)
        )
        assert [c["metadata"]["heading"] for c in chunks] == ["70.32 Real estate, how valued."]
        markers = [m.group("marker") for m in _EXECUTOR_MARKER_RE.finditer(chunks[0]["text"])]
        assert markers[0] == "1"
        assert chunks[0]["text"].startswith("70.32 Real estate, how valued.\n(1) Real property")

    def test_history_and_annotations_not_labeled_as_last_subsection(self):
        subs = [_sub("48", "TOWERS", 700), _sub("49", "RECREATIONAL PREFABRICATED", 700)]
        history = "History:  1971 c. 152; 1977 c. 29 ss. 745m, 1646 (3),\n(39) (g); 1979 c. 221."
        notes = "\n".join(
            "(4m) (a) ultimately turns on the facts. Covenant v. Wauwatosa, 2011 WI 80."
            for _ in range(20)
        )
        body = "\n".join(subs) + "\n" + history + "\n" + notes
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, body)
        )
        labels = [c["metadata"].get("subheading") for c in chunks]
        assert labels[:2] == ["70.11(48)", "70.11(49)"]
        assert all(lbl is None for lbl in labels[2:])
        assert "History:" not in chunks[1]["text"]
        # Descending / repeated markers inside notes never open a segment.
        assert not any(lbl and "(39)" in lbl for lbl in labels)
        assert not any(lbl and "(4m)" in lbl for lbl in labels)

    def test_wrapped_cross_reference_not_a_boundary(self):
        body = (
            "(1) FIRST.  Property that is exempt under sub.\n"
            "(2) of this section is taxable.  " + "x" * 700 + "\n"
            "(2) SECOND.  " + "y" * 700
        )
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, body)
        )
        assert [c["metadata"]["subheading"] for c in chunks] == ["70.11(1)", "70.11(2)"]
        assert chunks[1]["text"].split("\n", 1)[1].startswith("(2) SECOND.")

    def test_oversized_subsection_falls_through_with_heading_on_each_piece(self):
        big = "(1) HUGE.  " + "Sentence about assessments.  " * 200  # ~5800 chars
        small = "(2) SMALL.  Short."
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, f"{big}\n{small}")
        )
        cap = get_chunk_cap("statute")
        assert len(chunks) >= 3
        for c in chunks:
            assert len(c["text"]) <= cap
            assert c["text"].startswith(_HEADING + "\n")
        assert all(c["metadata"]["subheading"] == "70.11(1)" for c in chunks[:-1])
        assert chunks[-1]["metadata"]["subheading"] == "70.11(2)"

    def test_admin_rule_splits_by_subsection(self):
        mapping = [
            ("Tax 18.05 Valuation methods.  (1) In this section " + "a" * 700, 3),
            ("(2) The department shall " + "b" * 700, 3),
            ("(3) Nothing here " + "c" * 700, 4),
        ]
        chunks = chunk_document_admin_rule(None, "admin_rules-document-18.pdf", "bucket", mapping)
        assert [c["metadata"]["heading"] for c in chunks] == ["Tax 18.05"] * 3
        assert [c["metadata"]["subheading"] for c in chunks] == [
            "Tax 18.05(1)",
            "Tax 18.05(2)",
            "Tax 18.05(3)",
        ]
        assert chunks[0]["text"].startswith("Tax 18.05\nValuation methods.\n(1) In this section")
        for c in chunks:
            assert c["metadata"]["start_page"] == 3 and c["metadata"]["end_page"] == 4

    def test_fully_merged_section_keeps_range_label(self):
        body = "(1) A.  " + "a" * 100 + "\n(2) B.  " + "b" * 100
        chunks = chunk_document_statute(
            None, "statutes-70.pdf", "bucket", _section_mapping(_HEADING, body)
        )
        assert len(chunks) == 1
        assert chunks[0]["metadata"]["subheading"] == "70.11(1)–(2)"

    def test_split_helper_returns_text_and_labels(self):
        text = f"{_HEADING}\n(1) A.  " + "a" * 50 + "\n(2) B.  " + "b" * 50
        parts = _split_section_by_subsections(text, cap=3500, target=60)
        assert [lbl for _, lbl in parts] == ["70.11(1)", "70.11(2)"]
        assert all(t.startswith(_HEADING + "\n(") for t, _ in parts)
