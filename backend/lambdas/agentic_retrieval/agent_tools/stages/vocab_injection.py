"""Stage: trigger-gated vocabulary-injection search arm.

Some queries use informal, everyday vocabulary that never embeds close to the
statute/manual language that actually governs the answer. The canonical case:
"What exemptions can apply to a camping trailer?" — the governing exemption is
§70.11(49) "recreational prefabricated homes", and the assessor news page that
names it (news_pages-cotvc-news-2026-04-29) scores only ~0.30 cosine against
"camping trailer" and never surfaces. The refined query doesn't help; the agent
navigates to §70.111 (personal property) and never reaches §70.11(49).

This stage closes that specific, KNOWN vocabulary gap. When the user's query
contains a mapped trigger term, it runs an ADDITIONAL vector-search arm with
the query augmented by the statute vocabulary, and keeps only docs the main +
broad arms missed. It is NOT a query rewrite: appending vocabulary to the main
query shifts the whole embedding and drops the statute hits the main arm found
(measured), so this runs as a separate additive arm — mirroring broad_discovery.

Hard gate: if NO trigger term matches, the stage is a strict no-op (no embed,
no Neptune call). Runs last, so it can dedupe against both the main and broad
results. The map is a small, tightly-scoped synonym table (query expansion),
deliberately NOT a topic->answer map: it only injects vocabulary, retrieval
still decides relevance.
"""

import logging
import os
import re
import time

from wpam_dedup import dedupe_wpam_chunks

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)

# Each rule: a set of trigger terms and the vocabulary to inject when any of
# them appears in the user's query. Keep this tightly scoped — one bounded
# topical family per rule. Multi-word terms are matched as phrases; every term
# is matched on word boundaries (so "rv" never fires inside "harvest").
VOCAB_RULES: list[dict] = [
    {
        # Recreational-vehicle / mobile-home family -> §70.11(49) exemption for
        # "recreational prefabricated homes" (covers camping trailers, RVs,
        # park models, mobile/manufactured homes).
        "terms": [
            "camping trailer",
            "travel trailer",
            "recreational vehicle",
            "motor home",
            "motorhome",
            "park model",
            "mobile home",
            "manufactured home",
            "recreational prefabricated",
            "rv",
        ],
        "inject": "recreational prefabricated home mobile home exemption Wis. Stat. 70.11(49)",
    },
]


def _matched_rules(query: str) -> list[dict]:
    """Return the rules whose trigger terms appear in ``query`` (word-boundary,
    case-insensitive). Multi-word terms are matched as phrases."""
    q = query or ""
    matched: list[dict] = []
    for rule in VOCAB_RULES:
        hits = [t for t in rule["terms"] if re.search(rf"\b{re.escape(t)}\b", q, re.IGNORECASE)]
        if hits:
            matched.append({"rule": rule, "hits": hits})
    return matched


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    vocab_discovery: list[dict] = []
    ctx.vocab_discovery = vocab_discovery
    ctx.vocab_injected_terms = []
    ctx.vocab_query_used = ""

    if os.environ.get("VOCAB_INJECTION_ENABLED", "true").lower() != "true":
        ctx.timings["vocab_injection"] = (time.perf_counter() - started) * 1000
        return StageResult()

    source_query = (ctx.original_user_query or ctx.query or "").strip()
    matched = _matched_rules(source_query)
    if not matched:
        # Strict no-op: no trigger term -> no embed, no Neptune call.
        ctx.timings["vocab_injection"] = (time.perf_counter() - started) * 1000
        return StageResult()

    cap = int(os.environ.get("VOCAB_INJECTION_CAP", "5"))
    matched_terms = sorted({h for m in matched for h in m["hits"]})
    injections = " ".join(m["rule"]["inject"] for m in matched)
    injected_query = f"{source_query} {injections}".strip()
    ctx.vocab_injected_terms = matched_terms
    ctx.vocab_query_used = injected_query

    try:
        embedding = _executor.embed_query(injected_query)
        raw = ctx.neptune.vector_search(embedding, top_k=ctx.fetch_k)
        pre_dedup_count = len(raw)
        raw = dedupe_wpam_chunks(
            raw,
            target_year=ctx.target_wpam_year,
            current_wpam_year=ctx.neptune.current_wpam_year,
        )
        # Per-doc diversity cap, then truncate to top_k — mirror broad_discovery.
        dc: dict[str, int] = {}
        diverse: list[dict] = []
        for c in raw:
            did = c.get("doc_id", "unknown")
            if dc.get(did, 0) >= ctx.max_per_doc:
                continue
            dc[did] = dc.get(did, 0) + 1
            diverse.append(c)
        diverse = diverse[: ctx.top_k]
        # Additive only: docs not already surfaced by the main OR broad arms.
        have_doc_ids = {c.get("doc_id") for c in ctx.chunks}
        have_doc_ids |= {c.get("doc_id") for c in ctx.broad_discovery}
        vocab_discovery = [c for c in diverse if c.get("doc_id") not in have_doc_ids][:cap]

        _executor._log_tool_event(
            "vector_search_vocab_injection",
            tool_name=ctx.tool_name,
            matched_terms=matched_terms,
            injected_query=injected_query[:200],
            cap=cap,
            pre_dedup_count=pre_dedup_count,
            post_dedup_count=len(raw),
            arm_total=len(diverse),
            additive=len(vocab_discovery),
            additive_doc_ids=[c.get("doc_id") for c in vocab_discovery],
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception:  # noqa: BLE001 — best-effort enrichment
        logger.warning("vocab injection failed; continuing", exc_info=True)
        vocab_discovery = []

    ctx.vocab_discovery = vocab_discovery
    ctx.timings["vocab_injection"] = (time.perf_counter() - started) * 1000
    return StageResult()
