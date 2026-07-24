"""Stage: authority-aware quota selection for top_k.

Replaces the naive chunks[:top_k] slice with quota-aware selection that
guarantees minimum representation from primary sources (statutes, WPAM,
guides) before filling remaining slots with case law.

This prevents case-law chunks from crowding out authoritative sources in
vector_search results, which would force the model to spend turns
manually fetching statutes/WPAM via list_sections + get_section.
"""

import os
import time

from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()

    quota_statute = int(os.environ.get("AUTHORITY_QUOTA_STATUTE", "3"))
    quota_wpam = int(os.environ.get("AUTHORITY_QUOTA_WPAM", "2"))
    quota_guides = int(os.environ.get("AUTHORITY_QUOTA_GUIDES", "2"))

    chunks = ctx.chunks  # already diversity-capped, NOT yet top_k sliced
    top_k = ctx.top_k

    # Fill quotas from the ranked list (preserving relevance order within tier)
    reserved: list[dict] = []
    remaining: list[dict] = []
    stat_remaining = quota_statute
    wpam_remaining = quota_wpam
    guide_remaining = quota_guides

    for chunk in chunks:
        auth = chunk.get("authority_level")
        if auth is not None and auth <= 2 and stat_remaining > 0:
            reserved.append(chunk)
            stat_remaining -= 1
        elif auth == 5 and wpam_remaining > 0:
            reserved.append(chunk)
            wpam_remaining -= 1
        elif auth in (6, 7) and guide_remaining > 0:
            reserved.append(chunk)
            guide_remaining -= 1
        else:
            remaining.append(chunk)

    # Combine: reserved slots + remaining fill to top_k
    ctx.chunks = (reserved + remaining)[: top_k]

    # Snapshot backfill source chunks from the FULL quota-selected set
    # (pre authority-tiebreak, same as before)
    _backfill_source_gate = int(os.environ.get("STATUTE_BACKFILL_SOURCE_GATE", "3"))
    ctx.backfill_source_chunks = list(ctx.chunks[:_backfill_source_gate])

    ctx.timings["authority_quota"] = (time.perf_counter() - started) * 1000
    return StageResult()
