"""Stage: embed the refined query and run the Neptune narrow-arm vector search.

Over-fetches (``top_k * 6``) so downstream dedup/diversity-cap/authority
stages have enough candidates to work with before truncating to ``top_k``.
"""

import time

from agent_tools import executor as _executor
from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    ctx.embedding = _executor.embed_query(ctx.refined_query)
    ctx.fetch_k = ctx.top_k * 6
    ctx.chunks = ctx.neptune.vector_search(ctx.embedding, top_k=ctx.fetch_k)
    ctx.pre_dedup_count = len(ctx.chunks)
    ctx.timings["neptune_search"] = (time.perf_counter() - started) * 1000
    return StageResult()
