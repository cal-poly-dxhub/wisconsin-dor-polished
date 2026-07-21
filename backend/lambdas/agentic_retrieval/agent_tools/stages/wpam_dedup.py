"""Stage: collapse cross-edition WPAM chunk duplicates.

Thin wrapper around ``wpam_dedup.dedupe_wpam_chunks`` (unchanged) so it
participates in the pipeline's stage list / timing.
"""

import time

from wpam_dedup import dedupe_wpam_chunks

from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    ctx.chunks = dedupe_wpam_chunks(
        ctx.chunks,
        target_year=ctx.target_wpam_year,
        current_wpam_year=ctx.neptune.current_wpam_year,
    )
    ctx.timings["wpam_dedup"] = (time.perf_counter() - started) * 1000
    return StageResult()
