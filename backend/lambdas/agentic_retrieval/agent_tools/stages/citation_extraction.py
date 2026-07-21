"""Stage: extract case citations from retrieved chunk text and resolve them.

Surfaces cases mentioned in WPAM/guide text (e.g., Markarian) that would
otherwise be buried in a statute's 1500+ CITES neighbors.
"""

import logging
import time

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    related_case_law: list[dict] = []
    try:
        all_chunk_text = " ".join(c.get("text", "") for c in ctx.chunks)
        citations = _executor.extract_citations(all_chunk_text)
        if citations:
            related_case_law = ctx.neptune.resolve_case_citations(citations)
            _executor._log_tool_event(
                "vector_search_case_law_resolved",
                tool_name=ctx.tool_name,
                citations_extracted=len(citations),
                cases_resolved=len(related_case_law),
                case_ids=[c.get("id") for c in related_case_law[:10]],
            )
    except Exception:  # noqa: BLE001
        logger.warning("case law citation resolution failed", exc_info=True)

    ctx.related_case_law = related_case_law
    ctx.timings["citation_extraction"] = (time.perf_counter() - started) * 1000
    return StageResult()
