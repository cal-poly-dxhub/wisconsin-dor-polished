"""Post-chunking quality filters for WPAM documents.

Two independent fixes applied after chunk_document_wpam() returns:

1. filter_wpam_chunks — discards chunks whose body is too short, garbled
   (column-interleaved pipe text), or composed of single-character lines
   (vertical table headers like C-O-D-E).

2. repair_wpam_subheadings — clears subheadings that leaked across many
   chunks (e.g. "1. The record requested does not exist." appearing on
   40+ unrelated chunks because the chunker never encountered a new
   subheading to reset it).
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _body_text(chunk: dict[str, Any]) -> str:
    """Extract the body portion of a chunk, excluding heading/subheading lines."""
    text = chunk.get("text", "")
    lines = text.split("\n")
    # The chunk text starts with heading (line 0) and optionally subheading (line 1).
    # Skip blank lines at the top too.
    body_start = 0
    heading = chunk.get("metadata", {}).get("heading", "")
    subheading = chunk.get("metadata", {}).get("subheading") or ""

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == heading.strip() or stripped == subheading.strip() or stripped == "":
            body_start = i + 1
        else:
            break

    return "\n".join(lines[body_start:])


def _is_garbled(body: str) -> bool:
    """Detect column-interleaved garbled text from multi-column PDF tables.

    Key signals:
    - High pipe density relative to text length
    - Many short lines or lines with truncated word fragments
    - Pipes appearing mid-word (e.g., "boa | rd" instead of "board")
    """
    lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not lines or len(lines) < 3:
        return False

    pipe_count = body.count("|")
    total_chars = len(body)

    # High pipe density: >1 pipe per 30 chars of text, with at least 8 pipes
    if pipe_count >= 8 and total_chars / max(pipe_count, 1) < 30:
        # Also check for short/fragmented lines (< 40 chars)
        short_lines = sum(1 for line in lines if len(line) < 40)
        if short_lines > len(lines) * 0.4:
            return True

    return False


_BULLET_CHARS = {"•", "·", "–", "—", "-", "▪", "►", "○", "●", "÷", "=", "x", "+"}


def _is_table_cells(body: str) -> bool:
    """Detect chunks that are just single-character or single-word table cells.

    Bullet points and math operators on their own line are NOT counted as
    table cells — they appear in legitimate bulleted lists and formulas.
    """
    lines = [line.strip() for line in body.split("\n") if line.strip()]
    if len(lines) < 3:
        return False

    # Count truly meaningless single-char lines (letters, numbers) but
    # exclude common bullet/symbol characters that appear in real content
    single_char_lines = sum(1 for line in lines if len(line) <= 2 and line not in _BULLET_CHARS)
    if single_char_lines > len(lines) * 0.3:
        return True

    # Count very short lines, excluding bullets
    very_short_lines = sum(1 for line in lines if len(line) < 10 and line not in _BULLET_CHARS)
    if very_short_lines > len(lines) * 0.7 and len(lines) > 5:
        return True

    return False


def filter_wpam_chunks(
    chunks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter out useless/garbled WPAM chunks.

    Returns (kept, removed) where removed includes the reason for filtering.
    """
    kept = []
    removed = []

    for chunk in chunks:
        body = _body_text(chunk)
        body_stripped = body.strip()

        if len(body_stripped) < 60:
            removed.append({**chunk, "_filter_reason": "body_too_short"})
            continue

        if _is_garbled(body_stripped):
            removed.append({**chunk, "_filter_reason": "garbled_columns"})
            continue

        if _is_table_cells(body_stripped):
            removed.append({**chunk, "_filter_reason": "table_cells"})
            continue

        kept.append(chunk)

    return kept, removed


def merge_short_chunks(
    chunks: list[dict[str, Any]],
    min_chars: int = 200,
    max_merged_chars: int = 3000,
) -> list[dict[str, Any]]:
    """Merge very short chunks into their predecessor.

    Short chunks produce concentrated embeddings that can artificially
    outscore longer, more substantive chunks in vector search. These
    fragments are almost always the tail of the previous chunk's thought,
    so merging backward preserves context without creating new artifacts.

    Only merges when the predecessor shares the same heading (chapter)
    and the result stays under max_merged_chars.
    """
    if not chunks:
        return chunks

    result = [chunks[0]]

    for chunk in chunks[1:]:
        text = chunk.get("text", "")
        prev = result[-1]
        prev_text = prev.get("text", "")
        same_heading = chunk.get("metadata", {}).get("heading") == prev.get("metadata", {}).get(
            "heading"
        )

        if (
            len(text) < min_chars
            and same_heading
            and len(prev_text) + len(text) + 1 <= max_merged_chars
        ):
            merged_text = prev_text + "\n" + text
            merged_meta = {
                **prev.get("metadata", {}),
                "end_page": chunk.get("metadata", {}).get("end_page")
                or prev.get("metadata", {}).get("end_page"),
            }
            result[-1] = {**prev, "text": merged_text, "metadata": merged_meta}
        else:
            result.append(chunk)

    return result


def repair_wpam_subheadings(
    chunks: list[dict[str, Any]], max_occurrences: int = 5
) -> list[dict[str, Any]]:
    """Clear subheadings that appear on more than max_occurrences chunks.

    Real WPAM subsection titles are unique — they appear on 1–2 chunks max.
    Anything appearing on >5 chunks is a leaked numbered-list item or table
    label that the chunker carried forward without reset.
    """
    sub_counts: Counter[str] = Counter()
    for chunk in chunks:
        sub = chunk.get("metadata", {}).get("subheading") or ""
        if sub:
            sub_counts[sub] += 1

    leaked = {s for s, count in sub_counts.items() if count > max_occurrences}

    if not leaked:
        return chunks

    repaired = []
    for chunk in chunks:
        sub = chunk.get("metadata", {}).get("subheading") or ""
        if sub in leaked:
            chunk = {
                **chunk,
                "metadata": {**chunk["metadata"], "subheading": ""},
            }
        repaired.append(chunk)

    return repaired
