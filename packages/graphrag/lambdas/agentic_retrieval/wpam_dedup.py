"""WPAM cross-edition chunk deduplication.

The WPAM is republished annually. Without dedup, vector_search returns
near-identical chunks from many editions. This module applies two passes:

1. Heading-based collapse: groups chunks by normalized heading and keeps
   one per group (target_year or max edition_year).
2. Edition-year filter (when target_year is None): drops ALL WPAM chunks
   from older editions, keeping only the latest. This catches singletons
   whose headings differ across editions but whose content is equivalent.

When target_year IS set, the edition filter is skipped — the user
explicitly wants a specific edition.

WPAM chunks missing edition_year pass through unchanged (we can't
compare them).
"""

import logging
import re

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_heading(heading: str | None) -> str:
    """Collapse whitespace and lowercase. Stable across cosmetic edits."""
    if not heading:
        return ""
    return _WHITESPACE_RE.sub(" ", heading).strip().lower()


def _chunk_key(chunk: dict) -> str:
    """Some callers (vector_search) use 'chunk_id'; others (get_neighbors)
    use 'id'. Either is unique within a single tool result."""
    return chunk.get("chunk_id") or chunk.get("id") or ""


def _is_wpam(chunk: dict) -> bool:
    return chunk.get("framework_id") == "FW-WPAM"


def _can_dedup(chunk: dict) -> bool:
    """A WPAM chunk is dedup-eligible iff it has both an edition_year
    (so we can pick a survivor), a non-empty heading (so we can
    group it with peers), and a unique key."""
    return (
        _is_wpam(chunk)
        and chunk.get("edition_year") is not None
        and bool(_normalize_heading(chunk.get("heading")))
        and bool(_chunk_key(chunk))
    )


def _pick_survivor(group: list[dict], target_year: int | None) -> dict:
    """Pick one chunk from a group sharing the same heading.
    Prefer target_year if present; otherwise max(edition_year)."""
    if target_year is not None:
        for chunk in group:
            if chunk.get("edition_year") == target_year:
                return chunk
    return max(group, key=lambda c: c.get("edition_year", 0))


def dedupe_wpam_chunks(
    chunks: list[dict],
    target_year: int | None = None,
    current_wpam_year: int | None = None,
) -> list[dict]:
    """Collapse near-duplicate WPAM chunks across editions.

    Args:
        chunks: List of chunk dicts. Each chunk must have framework_id,
            edition_year, and heading for dedup eligibility.
        target_year: If set, prefer chunks from this year over max year.
            Also skips the edition filter entirely (user wants a specific edition).
        current_wpam_year: The authoritative current WPAM year from Neptune.
            When set and target_year is None, the edition filter uses this
            instead of max(edition_year) from the result set.

    Returns:
        A new list with dedup applied to WPAM chunks. Non-WPAM chunks
        and dedup-ineligible WPAM chunks pass through in their original
        positions.
    """
    if not chunks:
        return []

    # Group dedup-eligible WPAM chunks by normalized heading.
    groups: dict[str, list[dict]] = {}
    eligible_indexes: set[int] = set()
    for idx, chunk in enumerate(chunks):
        if _can_dedup(chunk):
            key = _normalize_heading(chunk["heading"])
            groups.setdefault(key, []).append(chunk)
            eligible_indexes.add(idx)

    # Pick survivors per group.
    survivors_by_id: dict[str, dict] = {}
    drops = 0
    for key, group in groups.items():
        if len(group) == 1:
            # Singleton — passes through unchanged.
            chunk = group[0]
            survivors_by_id[_chunk_key(chunk)] = chunk
            continue
        survivor = _pick_survivor(group, target_year)
        survivors_by_id[_chunk_key(survivor)] = survivor
        drops += len(group) - 1

    if drops:
        logger.info(
            "wpam_dedup: collapsed %d duplicate WPAM chunks across %d heading groups "
            "(target_year=%s)",
            drops,
            sum(1 for g in groups.values() if len(g) > 1),
            target_year,
        )

    # Walk the original list and emit survivors + ineligible chunks.
    result: list[dict] = []
    for idx, chunk in enumerate(chunks):
        if idx not in eligible_indexes:
            result.append(chunk)
            continue
        if _chunk_key(chunk) in survivors_by_id:
            result.append(chunk)
            del survivors_by_id[_chunk_key(chunk)]

    # Pass 2: edition-year filter. Only allow WPAM chunks from permitted
    # years. When target_year is None, only the current edition is allowed.
    # When target_year is set, allow both target_year and current edition
    # (user wants historical context but current is still relevant).
    if current_wpam_year is not None:
        allowed_years = {current_wpam_year}
    else:
        wpam_years = [
            c.get("edition_year")
            for c in result
            if _is_wpam(c) and c.get("edition_year") is not None
        ]
        allowed_years = {max(wpam_years)} if wpam_years else set()

    if target_year is not None:
        allowed_years.add(target_year)

    if allowed_years:
        pre_filter = len(result)
        result = [
            c for c in result
            if not _is_wpam(c)
            or c.get("edition_year") is None
            or c.get("edition_year") in allowed_years
        ]
        edition_drops = pre_filter - len(result)
        if edition_drops:
            logger.info(
                "wpam_dedup: edition filter dropped %d chunks "
                "(allowed_years=%s)",
                edition_drops,
                sorted(allowed_years),
            )

    return result
