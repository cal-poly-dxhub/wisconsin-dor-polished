"""Stage: refine the raw model/user query before embedding + search.

Delegates to ``agent_tools.executor._auto_refine`` (kept there, not here, so
existing tests that ``monkeypatch.setattr("agent_tools.executor._auto_refine",
...)`` continue to intercept the call unchanged).
"""

import time

from agent_tools import executor as _executor
from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    refined, target_year = _executor._auto_refine(ctx.query, ctx.chat_history)
    ctx.refined_query = refined
    ctx.target_wpam_year = target_year
    ctx.timings["auto_refine"] = (time.perf_counter() - started) * 1000
    return StageResult()
