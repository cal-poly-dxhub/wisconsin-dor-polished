"""Tests for the chunker's CHUNK_MAX_CHARS cap (currently 2500 chars).

The cap exists because embed.py silently truncates inputs past 8000 chars
when calling Titan Embed v2 — any chunk larger than that gets a partial
embedding that doesn't cover the chunk's tail text. The cap is set well
below that limit for retrieval quality (smaller chunks yield more precise
vector matches). The fix is lossless: text that used to produce one
oversized chunk now produces multiple in-cap chunks containing the same
characters end-to-end.

Covers both chunker strategies where the cap matters:
  - chunk_document (general) — used by WPAM PDFs, gov publications, etc.
  - chunk_document_wpam (specialized) — kept functional though currently
    nothing in S3 is routed through it (assessment-manual-XXXX doc IDs
    don't match the strategy lookup key).

The tests don't hit S3 — they drive the chunker directly with a synthetic
line_page_mapping.
"""

from __future__ import annotations

from tools.ingestion.chunking.pdfChunker import (
    CHUNK_MAX_CHARS,
    chunk_document,
    chunk_document_wpam,
)


def _make_mapping(lines_with_pages: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """line_page_mapping as the chunker expects."""
    return lines_with_pages


def _soft_cap(mapping: list[tuple[str, int]]) -> int:
    """The cap is a soft flush-threshold, not a hard limit. Two structural
    reasons a finished chunk can exceed CHUNK_MAX_CHARS, neither of which is
    a bug (chunks are never split mid-line, and both are bounded):

      1. The size check runs AFTER a line is appended, so the buffer can end
         one line past the cap before it flushes.
      2. chunk_document_wpam prepends the chapter/section heading to the chunk
         text in flush_chunk() — those heading chars aren't counted by the
         in-loop size check.

    The true upper bound is therefore CHUNK_MAX_CHARS + the longest single
    line + the heading overhead. We approximate the heading bound with the
    longest line as well (headings are themselves lines from the mapping),
    which is a safe over-estimate. Callers assert `len(text) <= _soft_cap(...)`.
    """
    longest_line = max((len(line) for line, _ in mapping), default=0)
    # + longest_line (post-append overshoot) + longest_line (prepended
    # heading) + 2 newline separators.
    return CHUNK_MAX_CHARS + 2 * longest_line + 2


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

    Uses natural sentence-shaped content where each sentence is its own line
    in the mapping (the realistic code path for WPAM PDFs, which have many
    short lines per paragraph). This ensures the chunker flushes at line
    boundaries and doesn't hard-cut mid-sentence.
    """
    sentences = [f"Sentence number {i} describes a concept." for i in range(600)]

    # Each sentence is its own mapping entry — mimics how real PDFs are
    # extracted (one line per element in the line_page_mapping).
    mapping = [("Chapter 5", 1), ("Tables Section", 1)]
    for i, sent in enumerate(sentences):
        mapping.append((sent, 5 + i // 30))

    chunks = chunk_document_wpam(None, "wpam-2023.pdf", "bucket", mapping)

    # Tally body content that made it into chunks. Heading noise ("Chapter 5",
    # "Tables Section") will appear but is small and repeated, so we count
    # occurrences of a signature substring unique to the body sentences.
    sentence_count_in_chunks = sum(c["text"].count("Sentence number") for c in chunks)
    assert sentence_count_in_chunks == 600, (
        f"Lost sentences: expected 600 occurrences, got {sentence_count_in_chunks}"
    )
    # And no chunk exceeds the soft cap (flush happens after the line is
    # appended, so a chunk may run one line past CHUNK_MAX_CHARS).
    cap = _soft_cap(mapping)
    for c in chunks:
        assert len(c["text"]) <= cap


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
    # Paragraphs of ~400 chars each separated by \n\n. With CHUNK_MAX_CHARS=2500,
    # paragraph breaks will fall within the 0.8-1.0 window (chars 2000-2500),
    # allowing the splitter to break at \n\n rather than mid-word.
    paragraphs = [("Body paragraph. " * 25) for _ in range(20)]  # each ~400 chars
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
    # And not so low that we produce hundreds of tiny chunks.
    # Current value is 2500 — tuned for retrieval quality (smaller chunks
    # yield more precise vector matches for property-tax Q&A).
    assert CHUNK_MAX_CHARS >= 2000


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
    cap = _soft_cap(mapping)
    for c in chunks:
        assert len(c["text"]) <= cap, (
            f"General chunker produced over-cap chunk: {len(c['text'])} > {cap}"
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
