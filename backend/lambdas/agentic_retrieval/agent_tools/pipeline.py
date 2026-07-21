"""Pipeline runner for vector_search's composable stages.

Wires the stage modules in ``agent_tools/stages/`` together in the exact
order the original monolithic ``vector_search`` branch of executor.py ran
them, and assembles the same result dict shape. See
docs/spec-retrieval-pipeline-refactor.md for the design.
"""

import time
from typing import Any

from agent_tools.stages import (
    authority_tiebreak,
    auto_enrichment,
    auto_refine,
    broad_discovery,
    caselaw_backfill,
    citation_extraction,
    diversity_cap,
    neptune_search,
    statute_backfill,
    wpam_dedup,
)
from agent_tools.stages.base import StageContext

VECTOR_SEARCH_STAGES = [
    auto_refine,
    neptune_search,
    wpam_dedup,
    diversity_cap,
    authority_tiebreak,
    auto_enrichment,
    citation_extraction,
    statute_backfill,
    caselaw_backfill,
    broad_discovery,
]


def run_vector_search(
    query: str,
    neptune: Any,
    chat_history: list[dict[str, str]] | None,
    original_user_query: str | None,
    top_k: int,
    tool_name: str = "vector_search",
) -> dict:
    """Run the vector_search stage pipeline and return the model-facing result.

    Mirrors the original monolithic branch of ``execute_tool`` exactly:
    same stage order, same env var reads (read live, per call), same trace
    events, same result dict shape.
    """
    from agent_tools import executor as _executor

    started = time.perf_counter()
    ctx = StageContext(
        query=query,
        neptune=neptune,
        chat_history=chat_history,
        original_user_query=original_user_query,
        top_k=top_k,
        tool_name=tool_name,
        started=started,
    )

    for stage in VECTOR_SEARCH_STAGES:
        result = stage.run(ctx)
        if result.trace:
            event, fields = result.trace
            _executor._log_tool_event(event, **fields)

    _executor._log_tool_event(
        "vector_search_complete",
        tool_name=tool_name,
        top_k=top_k,
        chunk_count=len(ctx.chunks),
        graph_context_doc_count=len(ctx.graph_context),
        graph_context_neighbor_count=sum(len(v) for v in ctx.graph_context.values()),
        related_case_law_count=len(ctx.related_case_law),
        statute_backfill_count=len(ctx.statute_backfill),
        caselaw_backfill_count=len(ctx.caselaw_backfill),
        broad_discovery_count=len(ctx.broad_discovery),
        latency_ms=round((time.perf_counter() - started) * 1000),
        **_executor._query_fields(ctx.refined_query),
    )

    # graph_context is intentionally NOT included in the model-facing result
    # (Direction 1, Option A). It was consumed by auto_enrichment /
    # citation_extraction / caselaw_backfill above. Surfacing it to the model
    # just floods the tool result with low-cite-rate neighbor stubs.
    result: dict[str, Any] = {
        "chunks": ctx.chunks,
        "pre_dedup_count": ctx.pre_dedup_count,
        "refined_query": ctx.refined_query,
        "top_k": top_k,
        "diversity_cap_per_doc": ctx.max_per_doc,
    }
    if original_user_query:
        result["broad_query"] = ctx.broad_query_used
        result["broad_skipped"] = ctx.broad_skipped
    if ctx.target_wpam_year is not None:
        result["target_wpam_year"] = ctx.target_wpam_year
    if ctx.related_case_law:
        result["related_case_law"] = ctx.related_case_law
    if ctx.statute_backfill:
        result["statute_backfill"] = ctx.statute_backfill
    if ctx.caselaw_backfill:
        result["caselaw_backfill"] = ctx.caselaw_backfill
    if ctx.broad_discovery:
        result["broad_discovery"] = ctx.broad_discovery
    if ctx.broad_trace:
        result.update(ctx.broad_trace)
    return result
