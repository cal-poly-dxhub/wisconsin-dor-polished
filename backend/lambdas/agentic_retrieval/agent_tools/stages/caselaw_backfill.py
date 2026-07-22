"""Stage: batch statute-stub backfill into directly citing case-law chunks."""

import logging
import os
import re
import time

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)


def _diversify_by_case(chunks: list[dict], top_k: int, initial_cap: int) -> list[dict]:
    """Prefer distinct cases, then allow one additional chunk per case."""
    selected: list[dict] = []
    selected_ids: set[str] = set()
    per_case: dict[str, int] = {}

    def fill(cap: int) -> None:
        for chunk in chunks:
            if len(selected) >= top_k:
                return
            chunk_id = chunk.get("chunk_id") or chunk.get("id") or ""
            case_id = chunk.get("case_id") or chunk.get("doc_id") or chunk_id
            if chunk_id in selected_ids or per_case.get(case_id, 0) >= cap:
                continue
            selected.append(chunk)
            selected_ids.add(chunk_id)
            per_case[case_id] = per_case.get(case_id, 0) + 1

    fill(max(1, initial_cap))
    if len(selected) < top_k:
        fill(max(1, initial_cap) + 1)
    return selected


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    top_k = int(os.environ.get("CASELAW_BACKFILL_CAP", "5"))
    configured_fetch_k = int(os.environ.get("CASELAW_CHUNK_FETCH_K", "200"))
    hard_cap = int(os.environ.get("CASELAW_CHUNK_HARD_CAP", "300"))
    max_per_case = int(os.environ.get("CASELAW_CHUNK_MAX_PER_CASE", "1"))
    fetch_k = min(max(0, configured_fetch_k), max(0, hard_cap))
    caselaw_backfill: list[dict] = []

    if top_k > 0 and fetch_k > 0:
        try:
            stub_ids: set[str] = set()
            for backfill in ctx.statute_backfill:
                stub_ids.update(stub for stub in backfill.get("cited_stubs", []) if stub)
            for chunk in ctx.chunks:
                if chunk.get("authority_level") == 2 or chunk.get("framework_id") == "FW-STATUTES":
                    heading = chunk.get("heading") or ""
                    doc_id = chunk.get("doc_id") or ""
                    match = re.match(r"^(\d+\.\d+)", heading)
                    if match and doc_id.startswith("statutes-"):
                        stub_ids.add(f"WIS-STAT-{match.group(1)}")

            searched_stubs = sorted(stub_ids)[:5]
            if searched_stubs:
                candidates = ctx.neptune.get_case_chunks_for_statutes_with_embeddings(
                    searched_stubs, limit=fetch_k
                )
                if candidates:
                    query_embedding = ctx.embedding or _executor.embed_query(ctx.refined_query)
                    rank_result = _executor._rank_chunks_by_relevance(
                        candidates,
                        query_embedding,
                        min(len(candidates), max(top_k * 5, top_k)),
                    )
                    caselaw_backfill = _diversify_by_case(
                        rank_result["chunks"], top_k, max_per_case
                    )
                    latency_ms = round((time.perf_counter() - started) * 1000)
                    ctx.caselaw_backfill_meta = {
                        "stubsSearched": searched_stubs,
                        "candidateCount": len(candidates),
                        "fetchSaturated": len(candidates) >= fetch_k,
                        "fetchK": fetch_k,
                        "latencyMs": latency_ms,
                    }
                    _executor._log_tool_event(
                        "vector_search_caselaw_backfill",
                        tool_name=ctx.tool_name,
                        stubs_searched=searched_stubs,
                        fetch_k=fetch_k,
                        candidate_chunks=len(candidates),
                        fetch_saturated=len(candidates) >= fetch_k,
                        backfilled=len(caselaw_backfill),
                        distinct_cases=len(
                            {
                                chunk.get("case_id") or chunk.get("doc_id")
                                for chunk in caselaw_backfill
                            }
                        ),
                        case_ids=[chunk.get("case_id") for chunk in caselaw_backfill],
                        latency_ms=latency_ms,
                    )
        except Exception:  # noqa: BLE001
            logger.warning("case-law backfill failed; continuing", exc_info=True)

    ctx.caselaw_backfill = caselaw_backfill
    ctx.timings["caselaw_backfill"] = (time.perf_counter() - started) * 1000
    return StageResult()
