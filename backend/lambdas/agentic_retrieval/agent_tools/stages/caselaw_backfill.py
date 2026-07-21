"""Stage: extend the citation chain one hop from statute stubs to case law.

For statute stubs discovered via statute_backfill (or present directly in
chunks), follows outgoing CITES to CaseLaw, ranks their summary embeddings
against the refined query, and surfaces the most relevant holdings.
"""

import logging
import os
import re
import time

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    caselaw_backfill_cap = int(os.environ.get("CASELAW_BACKFILL_CAP", "3"))
    caselaw_backfill: list[dict] = []

    if caselaw_backfill_cap > 0:
        try:
            # Collect statute stubs: from backfill cited_stubs + any statute
            # chunks directly in the top results.
            stub_ids: set[str] = set()
            for bf in ctx.statute_backfill:
                for stub in bf.get("cited_stubs", []):
                    if stub:
                        stub_ids.add(stub)
            for chunk in ctx.chunks:
                if chunk.get("authority_level") == 2 or chunk.get("framework_id") == "FW-STATUTES":
                    heading = chunk.get("heading") or ""
                    doc_id_str = chunk.get("doc_id") or ""
                    m = re.match(r"^(\d+\.\d+)", heading)
                    if m and doc_id_str.startswith("statutes-"):
                        stub_ids.add(f"WIS-STAT-{m.group(1)}")

            if stub_ids:
                all_case_summaries: list[dict] = []
                for stub_id in sorted(stub_ids)[:5]:  # cap stubs to avoid N*M explosion
                    try:
                        summaries = ctx.neptune.get_neighbor_case_summaries_with_embeddings(
                            stub_id, direction="outgoing"
                        )
                        all_case_summaries.extend(summaries)
                    except Exception:  # noqa: BLE001
                        continue

                if all_case_summaries:
                    # Dedup by case_id (a case may cite multiple stubs)
                    seen_cases: dict[str, dict] = {}
                    for cs in all_case_summaries:
                        cid = cs.get("case_id")
                        if cid and cid not in seen_cases:
                            seen_cases[cid] = cs
                    unique_summaries = list(seen_cases.values())

                    # Rank by cosine similarity to the refined query
                    query_embedding = _executor.embed_query(ctx.refined_query)
                    rank_result = _executor._rank_chunks_by_relevance(
                        [
                            {
                                "chunk_id": cs.get("case_id"),
                                "text": cs.get("summary", ""),
                                "doc_id": cs.get("case_id"),
                                "heading": cs.get("title", ""),
                                "subheading": cs.get("citation", ""),
                                "source_url": cs.get("source_url", ""),
                                "start_page": None,
                                "end_page": None,
                                "embedding": cs.get("embedding"),
                                "authority_level": 3,
                            }
                            for cs in unique_summaries
                        ],
                        query_embedding,
                        caselaw_backfill_cap,
                    )
                    caselaw_backfill = rank_result["chunks"]
                    _executor._log_tool_event(
                        "vector_search_caselaw_backfill",
                        tool_name=ctx.tool_name,
                        stubs_searched=sorted(stub_ids)[:5],
                        total_cases=len(unique_summaries),
                        backfilled=len(caselaw_backfill),
                        case_ids=[c.get("doc_id") for c in caselaw_backfill],
                        latency_ms=round((time.perf_counter() - started) * 1000),
                    )
        except Exception:  # noqa: BLE001
            logger.warning("case-law backfill failed; continuing", exc_info=True)

    ctx.caselaw_backfill = caselaw_backfill
    ctx.timings["caselaw_backfill"] = (time.perf_counter() - started) * 1000
    return StageResult()
