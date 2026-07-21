"""Stage: authority-aware tiebreak among similarly-scored chunks.

Chunks are bucketed by relevance score — within the same bucket, higher
authority (lower authority_level int) wins; between buckets, relevance wins.
Emits the ``vector_search_neptune_complete`` trace event, matching the point
in the original monolith where this log line was recorded (immediately after
the tiebreak sort, before auto-enrichment).
"""

import time

from agent_tools import executor as _executor
from agent_tools.stages.base import StageContext, StageResult

_AUTH_TIE_THRESHOLD = 0.03


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    chunks = ctx.chunks
    if chunks:
        for i, chunk in enumerate(chunks):
            score = chunk.get("score", 0)
            bucket = int(score / _AUTH_TIE_THRESHOLD)
            auth = chunk.get("authority_level") or 9
            chunks[i]["_sort_key"] = (-bucket, auth)
        chunks.sort(key=lambda c: c.pop("_sort_key"))
    ctx.chunks = chunks
    ctx.timings["authority_tiebreak"] = (time.perf_counter() - started) * 1000

    trace = (
        "vector_search_neptune_complete",
        {
            "tool_name": ctx.tool_name,
            "top_k": ctx.top_k,
            "chunk_count": len(ctx.chunks),
            "pre_dedup_count": ctx.pre_dedup_count,
            "target_wpam_year": ctx.target_wpam_year,
            "latency_ms": round(
                sum(
                    ctx.timings.get(k, 0.0)
                    for k in ("neptune_search", "wpam_dedup", "diversity_cap", "authority_tiebreak")
                )
            ),
            "top_doc_ids": [chunk.get("doc_id") for chunk in ctx.chunks[:5]],
            **_executor._query_fields(ctx.refined_query),
        },
    )
    return StageResult(trace=trace)
