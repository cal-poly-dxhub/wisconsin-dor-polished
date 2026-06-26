"""Boilerplate stripping for Wisconsin DOR PDF text.

Removes repeating headers, footers, navigation text, and other non-content
lines that pollute vector embeddings when they survive into chunks. Applied
to the (text, page_num) line-page mapping AFTER extraction but BEFORE
strategy-specific chunking.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List, Tuple

# --- General patterns (all doc types) ---
GENERAL_PATTERNS = [
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^Wisconsin\s+Department\s+of\s+Revenue\s*$", re.IGNORECASE),
    re.compile(r"^Back\s+to\s+table\s+of\s+contents\s*$", re.IGNORECASE),
    re.compile(
        r"^(?:Revised|Published|Effective|Updated)\s+"
        r"(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/]\d{4})\s*$",
        re.IGNORECASE,
    ),
]

# --- Statute-specific patterns ---
STATUTE_PATTERNS = [
    re.compile(r"Updated\s+\d{4}.*Wisconsin\s+Statutes", re.IGNORECASE),
    re.compile(r"^Chapter\s+\d+\s*$"),
    re.compile(r"^\d+\.\d+[A-Za-z\-]*\s+[A-Z][A-Z\s,;:&\-]+$"),
]

# --- WPAM-specific patterns ---
WPAM_PATTERNS = [
    re.compile(r"^Wisconsin\s+Property\s+Assessment\s+Manual\s*$", re.IGNORECASE),
    re.compile(r"^Vol\.?\s*\d+,?\s*page\s+\d+-\d+\s*$", re.IGNORECASE),
    re.compile(r"^Chapter\s+\d+\s*$"),
]

# --- Guide-specific patterns ---
GUIDE_PATTERNS: list[re.Pattern] = []

_STRATEGY_PATTERNS: dict[str, list[re.Pattern]] = {
    "statute": STATUTE_PATTERNS,
    "wpam": WPAM_PATTERNS,
    "general": GUIDE_PATTERNS,
}

_TAG_RE = re.compile(r"<+[^<>]*>+")
_CHAPTER_TITLE_RE = re.compile(r"^Chapter\s+\d+(?:\.\s*$|\s+[A-Z].+$)")


def strip_boilerplate(
    line_page_mapping: List[Tuple[str, int]],
    strategy: str = "general",
) -> List[Tuple[str, int]]:
    """Remove boilerplate lines from line-page mapping.

    Applies GENERAL_PATTERNS (always) plus strategy-specific patterns.
    For WPAM docs, also strips repeated "Chapter N Title" running headers
    while preserving the first occurrence (needed as a chunker split point).
    """
    if strategy == "wpam":
        line_page_mapping = _strip_wpam_running_headers(line_page_mapping)

    active_patterns = GENERAL_PATTERNS + _STRATEGY_PATTERNS.get(strategy, [])

    return [
        (line, pnum)
        for line, pnum in line_page_mapping
        if not _is_boilerplate(line, active_patterns)
    ]


def _strip_wpam_running_headers(
    line_page_mapping: List[Tuple[str, int]],
    threshold: int = 3,
) -> List[Tuple[str, int]]:
    """Strip repeated 'Chapter N Title' running headers, keeping first occurrence.

    Lines matching the chapter-title pattern that appear more than `threshold`
    times are running headers. The first occurrence of each is preserved (the
    chunker uses it as a split signal); subsequent duplicates are removed.
    """
    counts: Counter[str] = Counter()
    for line, _ in line_page_mapping:
        stripped = line.strip()
        if _CHAPTER_TITLE_RE.match(stripped):
            counts[stripped] += 1

    running_headers = {text for text, count in counts.items() if count > threshold}

    if not running_headers:
        return line_page_mapping

    seen: set[str] = set()
    result: List[Tuple[str, int]] = []
    for line, pnum in line_page_mapping:
        stripped = line.strip()
        if stripped in running_headers:
            if stripped not in seen:
                seen.add(stripped)
                result.append((line, pnum))
        else:
            result.append((line, pnum))
    return result


def _is_boilerplate(line: str, patterns: list[re.Pattern]) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    text_to_test = _TAG_RE.sub("", stripped).strip()
    if not text_to_test:
        return False
    return any(p.match(text_to_test) for p in patterns)
