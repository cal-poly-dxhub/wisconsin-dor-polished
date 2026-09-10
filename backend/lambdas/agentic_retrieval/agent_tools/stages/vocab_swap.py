"""Stage: deterministic vocabulary swap on the NARROW arm's query.

Some queries use everyday vocabulary that never embeds close to the statute
language that actually governs the answer — the canonical case is "mobile
home" / "camping trailer", which sits far from "recreational prefabricated
home" / §70.11(49) in embedding space, so the narrow vector search lands on
§70.111 (personal property) and never surfaces the controlling exemption.

This stage closes that KNOWN vocabulary gap. It runs AFTER auto_refine
(refine-then-swap, so the deterministic swap has the last word over the LLM
refine) and, when the user's query contains a mapped trigger term, appends the
mapped statute vocabulary to ``ctx.refined_query``. Only the narrow arm embeds
``refined_query``; broad_discovery still uses the original user query — so
§70.11(49)/news get PROMOTED into the narrow top-k while §70.111 is preserved
via the untouched broad arm (the answer covers both). Validated to reliably
cite §70.11(49) + the advisory + §70.111, and to reduce case-law backfill
bloat vs. leaving the query unchanged.

Trigger-gated: a strict no-op unless a mapped term matches. The vocabulary map
(``VOCAB_RULES``) is shared with vocab_injection. This is the primary
mechanism; the additive vocab_injection arm is kept dormant as a fallback.
Toggle with VOCAB_SWAP_ENABLED (default on).
"""

import os
import re
import time

from agent_tools.stages.base import StageContext, StageResult
from agent_tools.stages.vocab_injection import VOCAB_RULES


def _matched(query: str) -> list[dict]:
    q = query or ""
    return [
        r
        for r in VOCAB_RULES
        if any(re.search(rf"\b{re.escape(t)}\b", q, re.IGNORECASE) for t in r["terms"])
    ]


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    if os.environ.get("VOCAB_SWAP_ENABLED", "true").lower() == "true":
        # Match on the ORIGINAL user query (trigger term reliably present there),
        # apply the swap to the post-refine narrow query.
        source = (ctx.original_user_query or ctx.query or "").strip()
        rules = _matched(source)
        if rules:
            matched_terms = sorted(
                {
                    t
                    for r in rules
                    for t in r["terms"]
                    if re.search(rf"\b{re.escape(t)}\b", source, re.IGNORECASE)
                }
            )
            injection = " ".join(r["inject"] for r in rules)
            before = ctx.refined_query
            ctx.refined_query = f"{ctx.refined_query} {injection}".strip()
            _executor._log_tool_event(
                "vocab_swap_applied",
                tool_name=ctx.tool_name,
                matched_terms=matched_terms,
                before=before[:120],
                after=ctx.refined_query[:200],
            )
    ctx.timings["vocab_swap"] = (time.perf_counter() - started) * 1000
    return StageResult()
