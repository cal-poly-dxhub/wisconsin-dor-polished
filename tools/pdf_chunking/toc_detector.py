"""Detect Table-of-Contents chunks so they don't pollute retrieval.

TOC pages match the query intent ("contact information") lexically without
carrying the answer. Short-query retrieval surfaces them above real content.
We drop them at ingestion rather than re-ranking at retrieval time so the
graph stays clean.

Detection is conservative — we only flag text that looks unambiguously like
a leader-dot TOC. A chunk with many citations or a dotted list of bullet
points should NOT match.
"""

from __future__ import annotations

import re

# Leader sequences: ". . . . ." or "........" — 5+ consecutive dots where
# each dot may be followed by whitespace. PDF extractors emit both forms
# depending on whether they collapsed the tracked kerning.
_LEADER_PATTERN = re.compile(r"(?:\.[ \t\xa0]*){5,}\.")

# Pure roman-numeral heading like "V." or "XIV." (with or without trailing
# period/whitespace). A content section would have an actual title.
_PURE_ROMAN_HEADING = re.compile(r"^[IVXLCDM]+\.\s*$")


def _leader_coverage(text: str) -> float:
    """Fraction of text taken up by dot-leader runs."""
    if not text:
        return 0.0
    total = len(text)
    leader_chars = sum(len(m.group(0)) for m in _LEADER_PATTERN.finditer(text))
    return leader_chars / total


def is_toc_chunk(
    text: str,
    heading: str | None = None,
    *,
    min_leader_coverage: float = 0.2,
    min_leader_matches: int = 2,
    max_coverage_chunk_len: int = 1500,
) -> bool:
    """Return True when the chunk is a Table-of-Contents fragment.

    Deliberately conservative — err toward keeping content. A chunk is
    classified as TOC only when EITHER:
      - Its heading is a pure roman numeral ("X.", "XIV.") AND the text
        contains at least one leader-dot sequence. Pure-roman headings on
        real content sections are vanishingly rare; when we see one with
        leader dots it's always a TOC entry.
      - Its text is short (≤ max_coverage_chunk_len) AND has at least
        `min_leader_matches` leader sequences covering ≥`min_leader_coverage`
        of its length. Long chunks (front-matter, full standards pages) can
        contain embedded TOC text mixed with real content; the coverage
        heuristic over-flags there, so we skip it for them.
    """
    if not text:
        return False

    matches = list(_LEADER_PATTERN.finditer(text))
    if not matches:
        return False

    if heading and _PURE_ROMAN_HEADING.match(heading.strip()):
        return True

    if len(text) <= max_coverage_chunk_len and len(matches) >= min_leader_matches:
        leader_chars = sum(len(m.group(0)) for m in matches)
        if leader_chars / len(text) >= min_leader_coverage:
            return True

    return False
