"""WPAM cross-edition chunk deduplication.

The WPAM is republished annually. Without dedup, vector_search returns
near-identical chunks from many editions. This helper collapses them
to one chunk per (framework, normalized_section_heading), preferring
either the user-specified target year or the most recent edition.

Singletons (chunks unique to one edition) survive — we only collapse
groups with multiple peers. WPAM chunks missing edition_year (e.g.,
loaded before this feature shipped) pass through unchanged because
we can't compare them.
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


def _is_wpam(chunk: dict) -> bool:
    return chunk.get("framework_id") == "FW-WPAM"


def _can_dedup(chunk: dict) -> bool:
    """A WPAM chunk is dedup-eligible iff it has both an edition_year
    (so we can pick a survivor) and a non-empty heading (so we can
    group it with peers)."""
    return (
        _is_wpam(chunk)
        and chunk.get("edition_year") is not None
        and bool(_normalize_heading(chunk.get("heading")))
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
) -> list[dict]:
    """Collapse near-duplicate WPAM chunks across editions.

    Args:
        chunks: List of chunk dicts. Each chunk must have framework_id,
            edition_year, and heading for dedup eligibility.
        target_year: If set, prefer chunks from this year over max year.

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
            survivors_by_id[chunk["chunk_id"]] = chunk
            continue
        survivor = _pick_survivor(group, target_year)
        survivors_by_id[survivor["chunk_id"]] = survivor
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
        if chunk["chunk_id"] in survivors_by_id:
            result.append(chunk)
            del survivors_by_id[chunk["chunk_id"]]
    return result
