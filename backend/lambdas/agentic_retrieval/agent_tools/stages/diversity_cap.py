"""Stage: cap chunks-per-document, then truncate to top_k.

Also snapshots the top-N chunks (by true relevance order, pre authority
tiebreak) for the statute_backfill stage — the backfill gate is tuned on
relevance rank, not on the authority-display order the next stage produces.
"""

import os
import time

from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    max_per_doc = int(os.environ.get("DIVERSITY_CAP_PER_DOC", "5"))
    ctx.max_per_doc = max_per_doc
    if max_per_doc > 0:
        doc_counts: dict[str, int] = {}
        diverse_chunks: list[dict] = []
        for chunk in ctx.chunks:
            doc_id = chunk.get("doc_id", "unknown")
            if doc_counts.get(doc_id, 0) >= max_per_doc:
                continue
            doc_counts[doc_id] = doc_counts.get(doc_id, 0) + 1
            diverse_chunks.append(chunk)
        ctx.chunks = diverse_chunks
    ctx.chunks = ctx.chunks[: ctx.top_k]

    _backfill_source_gate = int(os.environ.get("STATUTE_BACKFILL_SOURCE_GATE", "3"))
    ctx.backfill_source_chunks = list(ctx.chunks[:_backfill_source_gate])

    ctx.timings["diversity_cap"] = (time.perf_counter() - started) * 1000
    return StageResult()
