"""Tests for the chunker's 7500-char cap.

The cap exists because embed.py silently truncates inputs past 8000 chars
when calling Titan Embed v2 — any chunk larger than that gets a partial
embedding that doesn't cover the chunk's tail text. The fix is lossless:
text that used to produce one oversized chunk now produces multiple
in-cap chunks containing the same characters end-to-end.

Covers both chunker strategies where the cap matters:
  - chunk_document (general) — used by WPAM PDFs, gov publications, etc.
  - chunk_document_wpam (specialized) — kept functional though currently
    nothing in S3 is routed through it (assessment-manual-XXXX doc IDs
    don't match the strategy lookup key).

The tests don't hit S3 — they drive the chunker directly with a synthetic
line_page_mapping.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Pre-stub the AWS-touching imports so loading pdfChunker doesn't call
# head_bucket at module import time.
sys.modules.setdefault("boto3", MagicMock())
sys.modules.setdefault("botocore", MagicMock())
sys.modules.setdefault("botocore.config", MagicMock())
sys.modules.setdefault("botocore.exceptions", MagicMock())

from pdf_chunking.pdfChunker import (  # noqa: E402
    CHUNK_MAX_CHARS,
    chunk_document,
    chunk_document_wpam,
)


def _make_mapping(lines_with_pages: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """line_page_mapping as the chunker expects."""
    return lines_with_pages


def test_no_chunk_exceeds_cap_for_long_paragraphs() -> None:
    """Dense paragraphs that would produce a 15KB chunk get split correctly."""
    # 30 paragraphs of 500 chars each under one chapter+section = 15KB total.
    paragraphs = [
        f"Paragraph {i}. " + ("Lorem ipsum dolor sit amet. " * 17) for i in range(30)
    ]
    mapping = [("Chapter 1", 1), ("Assessment Methods", 1)]
    for i, p in enumerate(paragraphs):
        mapping.append((p, 1 + i // 3))

    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    assert len(chunks) >= 2, "Long content should produce multiple chunks"
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS, (
            f"Chunk exceeds cap: {len(c['text'])} > {CHUNK_MAX_CHARS}"
        )


def test_total_chars_conserved_across_splits() -> None:
    """The cap splits oversized content into multiple chunks WITHOUT losing characters.

    Uses natural sentence-shaped content so the line-boundary flush triggers
    fire (the realistic code path for WPAM PDFs, which have many short lines
    per paragraph, not one giant blob line).
    """
    sentences = [f"Sentence number {i} describes a concept. " for i in range(600)]
    # Concatenate into ~20 lines, each ~1500 chars — mimics a dense paragraph.
    lines = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) > 1500:
            lines.append(buf.strip())
            buf = s
        else:
            buf += s
    if buf.strip():
        lines.append(buf.strip())

    mapping = [("Chapter 5", 1), ("Tables Section", 1)]
    for i, line in enumerate(lines):
        mapping.append((line, 5 + i))

    input_content_chars = sum(len(line) for line in lines)

    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    # Tally body content that made it into chunks. Heading noise ("Chapter 5",
    # "Tables Section") will appear but is small and repeated, so we count
    # occurrences of a signature substring unique to the body sentences.
    sentence_count_in_chunks = sum(c["text"].count("Sentence number") for c in chunks)
    assert sentence_count_in_chunks == 600, (
        f"Lost sentences: expected 600 occurrences, got {sentence_count_in_chunks}"
    )
    # And no chunk exceeds the cap
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS


def test_small_chunks_merge_but_not_past_cap() -> None:
    """Merge-small-chunks pass honors the char cap."""
    # Two mid-sized chunks under the same heading; combined they'd be 8-10KB.
    # Old merge pass only checked word count (<= 500); new pass rejects
    # because combined len > CHUNK_MAX_CHARS.
    mid_paragraph = "Mid chunk content. " * 250  # ~5000 chars
    # Two successive small flushes under one section
    mapping = [("Chapter 3", 1), ("Market Analysis", 1)]
    mapping.append((mid_paragraph, 5))
    # Section break to flush, then another same-section chunk
    mapping.append(("Market Analysis", 5))
    mapping.append((mid_paragraph, 6))

    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    # Whatever shape the chunker produces, no chunk may be over cap.
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS, (
            f"Merge pass produced over-cap chunk: {len(c['text'])}"
        )


def test_split_prefers_paragraph_breaks() -> None:
    """When text has paragraph breaks, splits land on them, not mid-word."""
    # 10KB of text with clear paragraph breaks every 1000 chars
    paragraphs = [("Body paragraph. " * 62) for _ in range(10)]  # each ~1000 chars
    mapping = [("Chapter 2", 1), ("Section A", 1)]
    # Emit as a single huge line so the defense-in-depth splitter is what handles it
    combined = "\n\n".join(paragraphs)
    mapping.append((combined, 1))

    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    # For the chunks produced by the splitter, check their endings don't
    # dangle mid-word. (Chunk boundaries should land at paragraph or line
    # break when possible; it's OK if the last chunk ends at the content end.)
    for c in chunks[:-1]:  # all but the last
        ends = c["text"].rstrip().endswith((".", "!", "?", ":", ";"))
        # Heading-only chunks are an exception — allow anything ending in a
        # section-header-like token
        assert ends or c["text"].count("\n") <= 2, (
            f"Mid-content chunk doesn't end cleanly: ...{c['text'][-60:]}"
        )


def test_chunker_still_produces_chunks_for_small_input() -> None:
    """Small WPAM sections that fit in one chunk still work normally."""
    mapping = [
        ("Chapter 4", 1),
        ("Brief Section", 1),
        ("This is a short section with only a few sentences.", 1),
        ("It describes a tax concept.", 1),
        ("It fits in a single chunk well under the cap.", 1),
    ]
    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    assert len(chunks) >= 1
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS
        assert "short section" in c["text"] or "Brief Section" in c["text"]


def test_cap_constant_below_titan_limit() -> None:
    """Sanity: the cap must leave a margin below Titan's 8000-char limit."""
    assert CHUNK_MAX_CHARS < 8000
    # And not so low that we produce hundreds of tiny chunks
    assert CHUNK_MAX_CHARS >= 5000


# ---------------------------------------------------------------------------
# chunk_document (general) tests — this is what runs on WPAM PDFs today,
# since source_id "wpam-wisconsin-property-assessment-manual-2023" doesn't
# match any key in CHUNKER_BY_SOURCE and falls through to "general".
# ---------------------------------------------------------------------------


def test_general_chunker_caps_oversized_content() -> None:
    """chunk_document enforces the char cap on accumulated line buffers."""
    # Dense prose with no headings — the chunker must flush on char cap alone
    # since there's no roman or capital section marker to trigger an early
    # flush.
    sentences = [f"Tax assessment principle {i}. " * 4 for i in range(400)]
    mapping = [(sent, 1 + i // 20) for i, sent in enumerate(sentences)]

    chunks = chunk_document(None, "wpam-2023.pdf", "bucket", mapping)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS, (
            f"General chunker produced over-cap chunk: {len(c['text'])}"
        )


def test_general_chunker_preserves_full_content() -> None:
    """Lossless: every sentence from input must appear in the chunked output."""
    sentences = [f"Unique marker SENT-{i:04d} content line. " for i in range(500)]
    mapping = [(sent, 1 + i // 25) for i, sent in enumerate(sentences)]

    chunks = chunk_document(None, "wpam-2023.pdf", "bucket", mapping)

    all_text = "\n".join(c["text"] for c in chunks)
    lost = [i for i in range(500) if f"SENT-{i:04d}" not in all_text]
    assert not lost, f"Lost {len(lost)} sentences in chunking: first 5 = {lost[:5]}"


def test_general_chunker_hard_splits_single_long_line() -> None:
    """_enforce_chunk_cap splits a chunk whose body is one long line."""
    # Single line with paragraph breaks inside it — tests the defensive splitter
    long_line = "\n\n".join([f"Paragraph {i}. " + ("filler word " * 80) for i in range(15)])
    mapping = [("I. Overview", 1), (long_line, 1)]

    chunks = chunk_document(None, "wpam-2023.pdf", "bucket", mapping)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c["text"]) <= CHUNK_MAX_CHARS
    # All paragraph markers must survive
    all_text = "\n".join(c["text"] for c in chunks)
    for i in range(15):
        assert f"Paragraph {i}." in all_text, f"Lost paragraph {i}"
