"""Stage: internal-only graph neighbor enrichment for the top parent docs.

Fetches graph neighbors for the top-3 distinct parent doc_ids among the
current chunks and stores them in ``ctx.graph_context``. This output is
INTERNAL-ONLY: it is NOT returned to the model (surfacing it floods the tool
result with low-cite-rate neighbor stubs).

NOTE: ``ctx.graph_context`` is currently consumed only for log counts in
pipeline.py — the downstream case-law stages (citation_extraction,
caselaw_backfill) read ``ctx.chunks`` / ``ctx.statute_backfill``, not the
enrichment output. See docs/auto-backfill.md.
"""

import logging
import os
import time

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)

_EDGE_PRIORITY = {
    "CITES": 0,
    "IMPLEMENTS": 0,
    "DERIVED_FROM": 1,
    "BELONGS_TO": 2,
    "COVERS_TOPIC": 3,
}


def _rank_neighbors(neighbors: list[dict], enrich_cap: int, enrich_cap_per_type: int) -> list[dict]:
    """Rank by edge priority then authority, diversity-cap per doc_type."""
    for n in neighbors:
        n["_edge_pri"] = _EDGE_PRIORITY.get(n.get("relationship", ""), 5)
        n["_auth"] = n.get("authority_level") or 9
    neighbors.sort(key=lambda n: (n["_edge_pri"], n["_auth"]))
    result = []
    type_counts: dict[str, int] = {}
    for n in neighbors:
        dt = n.get("doc_type") or "unknown"
        if type_counts.get(dt, 0) >= enrich_cap_per_type:
            continue
        type_counts[dt] = type_counts.get(dt, 0) + 1
        n.pop("_edge_pri", None)
        n.pop("_auth", None)
        result.append(n)
        if len(result) >= enrich_cap:
            break
    return result


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    graph_context: dict[str, list[dict]] = {}
    seen: list[str] = []
    for chunk in ctx.chunks:
        doc_id = chunk.get("doc_id", "")
        if doc_id and doc_id not in seen:
            seen.append(doc_id)
            if len(seen) >= 3:
                break

    enrich_cap = int(os.environ.get("ENRICH_CAP_PER_DOC", "5"))
    enrich_cap_per_type = int(os.environ.get("ENRICH_CAP_PER_TYPE", "4"))

    for doc_id in seen:
        try:
            enrich_started = time.perf_counter()
            neighbors = ctx.neptune.get_neighbors(doc_id)
            neighbors = [n for n in neighbors if "Chunk" not in (n.get("labels") or [])]
            neighbors = _rank_neighbors(neighbors, enrich_cap, enrich_cap_per_type)
            if neighbors:
                graph_context[doc_id] = neighbors
            _executor._log_tool_event(
                "vector_search_auto_enrichment_complete",
                tool_name=ctx.tool_name,
                doc_id=doc_id,
                neighbor_count=len(neighbors),
                latency_ms=round((time.perf_counter() - enrich_started) * 1000),
            )
        except Exception:  # noqa: BLE001 — best-effort enrichment
            _executor._log_tool_event(
                "vector_search_auto_enrichment_error",
                logging.WARNING,
                tool_name=ctx.tool_name,
                doc_id=doc_id,
                error="auto-enrichment failed; continuing without neighbors",
            )
            logger.warning(
                f"auto-enrichment failed for {doc_id}; continuing without neighbors",
                exc_info=True,
            )

    ctx.graph_context = graph_context
    ctx.timings["auto_enrichment"] = (time.perf_counter() - started) * 1000
    return StageResult()
