"""Stage: broad discovery arm using the user's original natural-language query.

Catches practitioner-oriented docs (news, guides) that the model's
keyword-heavy refined query misses. Only fires on the FIRST vector_search
call (when original_user_query differs from the refined query). Runs the
same pipeline as the narrow arm (full top_k, dedup, diversity cap) but only
keeps chunks from docs NOT already in the narrow results.
"""

import logging
import time
from typing import Any

from wpam_dedup import dedupe_wpam_chunks

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    broad_discovery: list[dict] = []
    broad_trace: dict[str, Any] | None = None

    if ctx.original_user_query:
        broad_query = ctx.original_user_query.strip()
        if broad_query.lower().rstrip("?.") != ctx.refined_query.lower().rstrip("?."):
            try:
                broad_embedding = _executor.embed_query(broad_query)
                broad_raw = ctx.neptune.vector_search(broad_embedding, top_k=ctx.fetch_k)
                broad_pre_dedup_count = len(broad_raw)
                broad_raw = dedupe_wpam_chunks(
                    broad_raw,
                    target_year=ctx.target_wpam_year,
                    current_wpam_year=ctx.neptune.current_wpam_year,
                )
                broad_dc: dict[str, int] = {}
                broad_diverse: list[dict] = []
                for c in broad_raw:
                    did = c.get("doc_id", "unknown")
                    if broad_dc.get(did, 0) >= ctx.max_per_doc:
                        continue
                    broad_dc[did] = broad_dc.get(did, 0) + 1
                    broad_diverse.append(c)
                broad_diverse = broad_diverse[: ctx.top_k]
                narrow_doc_ids = {c.get("doc_id") for c in ctx.chunks}
                broad_discovery = [
                    c for c in broad_diverse if c.get("doc_id") not in narrow_doc_ids
                ]
                # Full granularity logging for broad arm
                broad_authority: dict[str, int] = {}
                for c in broad_diverse:
                    level = c.get("authority_level")
                    if level is not None:
                        k = str(int(level))
                        broad_authority[k] = broad_authority.get(k, 0) + 1
                broad_scores = [float(c.get("score", 0)) for c in broad_diverse]
                broad_score_buckets: dict[str, int] = {}
                for s in broad_scores:
                    if s >= 0.9:
                        broad_score_buckets["0.9+"] = broad_score_buckets.get("0.9+", 0) + 1
                    elif s >= 0.8:
                        broad_score_buckets["0.8-0.9"] = broad_score_buckets.get("0.8-0.9", 0) + 1
                    elif s >= 0.7:
                        broad_score_buckets["0.7-0.8"] = broad_score_buckets.get("0.7-0.8", 0) + 1
                    else:
                        broad_score_buckets["<0.7"] = broad_score_buckets.get("<0.7", 0) + 1
                broad_doc_chunks: dict[str, int] = {}
                for c in broad_diverse:
                    did = c.get("doc_id", "unknown")
                    broad_doc_chunks[did] = broad_doc_chunks.get(did, 0) + 1
                broad_additive_doc_chunks: dict[str, int] = {}
                for c in broad_discovery:
                    did = c.get("doc_id", "unknown")
                    broad_additive_doc_chunks[did] = broad_additive_doc_chunks.get(did, 0) + 1
                broad_trace = {
                    "broad_pre_dedup_count": broad_pre_dedup_count,
                    "broad_kept_count": len(broad_diverse),
                    "broad_authority_breakdown": broad_authority,
                    "broad_score_buckets": {k: v for k, v in broad_score_buckets.items() if v > 0},
                    "broad_full_doc_chunks": broad_doc_chunks,
                    "broad_additive_doc_chunks": broad_additive_doc_chunks,
                    "broad_top_score": round(broad_scores[0], 4) if broad_scores else 0.0,
                }
                _executor._log_tool_event(
                    "vector_search_broad_discovery",
                    tool_name=ctx.tool_name,
                    broad_query=broad_query[:200],
                    pre_dedup_count=broad_pre_dedup_count,
                    post_dedup_count=len(broad_raw),
                    broad_total=len(broad_diverse),
                    additive=len(broad_discovery),
                    broad_doc_ids=[c.get("doc_id") for c in broad_discovery],
                    authority_breakdown=broad_authority,
                    score_buckets=broad_score_buckets,
                    doc_chunks=broad_doc_chunks,
                    top_score=round(broad_scores[0], 4) if broad_scores else 0,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            except Exception:  # noqa: BLE001
                logger.warning("broad discovery failed; continuing", exc_info=True)

    ctx.broad_discovery = broad_discovery
    ctx.broad_trace = broad_trace

    broad_query_used = ""
    broad_skipped = True
    if ctx.original_user_query:
        broad_query_used = ctx.original_user_query.strip()
        broad_skipped = broad_query_used.lower().rstrip("?.") == ctx.refined_query.lower().rstrip(
            "?."
        )
    ctx.broad_query_used = broad_query_used
    ctx.broad_skipped = broad_skipped

    ctx.timings["broad_discovery"] = (time.perf_counter() - started) * 1000
    return StageResult()
