"""Boilerplate stripping for Wisconsin DOR PDF text.

Removes repeating headers, footers, navigation text, and other non-content
lines that pollute vector embeddings when they survive into chunks. Applied
to the (text, page_num) line-page mapping AFTER extraction but BEFORE
strategy-specific chunking.
"""

from __future__ import annotations

import re
from collections import Counter

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

# --- Admin-rule-specific patterns ---
ADMIN_RULE_PATTERNS = [
    re.compile(r"^WISCONSIN\s+ADMINISTRATIVE\s+CODE\s*$", re.IGNORECASE),
    re.compile(r"^WISCONSIN\s+DEPARTMENT\s+OF\s+REVENUE\s*$", re.IGNORECASE),
    re.compile(
        r"^Published\s+under\s+s\.\s*\d+\.\d+",
        re.IGNORECASE,
    ),
    re.compile(r"^Register\s+\w+\s+\d{4}\s+No\.\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^Chapter\s+Tax\s+\d+\s*$", re.IGNORECASE),
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
    "admin_rule": ADMIN_RULE_PATTERNS,
    "wpam": WPAM_PATTERNS,
    "general": GUIDE_PATTERNS,
}

_TAG_RE = re.compile(r"<+[^<>]*>+")
_CHAPTER_TITLE_RE = re.compile(r"^Chapter\s+\d+(?:\.\s*$|\s+[A-Z].+$)")
_LEADER_DOT_RE = re.compile(r"\.{5,}")


def strip_boilerplate(
    line_page_mapping: list[tuple[str, int]],
    strategy: str = "general",
) -> list[tuple[str, int]]:
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


def _in_toc_context(index: int, line_page_mapping: list[tuple[str, int]], window: int = 5) -> bool:
    """Return True when the line at `index` is surrounded by leader-dot lines (TOC)."""
    start = max(0, index - window)
    end = min(len(line_page_mapping), index + window + 1)
    leader_count = 0
    for i in range(start, end):
        if i == index:
            continue
        if _LEADER_DOT_RE.search(line_page_mapping[i][0]):
            leader_count += 1
    return leader_count >= 2


def _strip_wpam_running_headers(
    line_page_mapping: list[tuple[str, int]],
    threshold: int = 3,
) -> list[tuple[str, int]]:
    """Strip repeated 'Chapter N Title' running headers, keeping first non-TOC occurrence.

    Lines matching the chapter-title pattern that appear more than `threshold`
    times are running headers. The first occurrence NOT in a TOC context is
    preserved (the chunker uses it as a split signal); subsequent duplicates
    are removed. If all occurrences are in TOC context, the first is kept as
    a fallback.
    """
    counts: Counter[str] = Counter()
    for line, _ in line_page_mapping:
        stripped = line.strip()
        if _CHAPTER_TITLE_RE.match(stripped):
            counts[stripped] += 1

    running_headers = {text for text, count in counts.items() if count > threshold}

    if not running_headers:
        return line_page_mapping

    # First pass: find the first non-TOC occurrence of each running header
    kept_index: dict[str, int] = {}
    for i, (line, _pnum) in enumerate(line_page_mapping):
        stripped = line.strip()
        if stripped in running_headers and stripped not in kept_index:
            if not _in_toc_context(i, line_page_mapping):
                kept_index[stripped] = i

    # Fallback: if every occurrence was in TOC context, keep the first one
    for i, (line, _pnum) in enumerate(line_page_mapping):
        stripped = line.strip()
        if stripped in running_headers and stripped not in kept_index:
            kept_index[stripped] = i

    # Second pass: emit only the kept occurrence, drop all other duplicates
    result: list[tuple[str, int]] = []
    for i, (line, pnum) in enumerate(line_page_mapping):
        stripped = line.strip()
        if stripped in running_headers:
            if i == kept_index[stripped]:
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
