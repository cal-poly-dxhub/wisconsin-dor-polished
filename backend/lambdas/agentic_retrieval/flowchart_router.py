"""Pre-loop flowchart router — semantic SEED-or-nothing gate.

Some user questions are decision-procedure questions ("does my land qualify as
agricultural?", "is my mobile home taxable?") for which the WPAM publishes a
step-by-step decision flowchart. Those charts are flattened raster images the
text pipeline cannot retrieve, so we seed the matching chart into the agent's
turn-0 context (the same way faq_search / vector_search are seeded) — but only
when a chart is clearly on point.

Routing is semantic, not keyword: we embed the user's query (Titan v2, 1024-dim,
normalized) and score cosine similarity against each flowchart's
``router_description``. This handles morphology and synonyms a regex misses
(plural "mobile homes", "camper", "trailer homes") and yields a confidence score
to threshold on.

Decision (validated offline against 502 real queries):

    route(query) -> SEED <flowchart_id>   if top >= SEED_ABS AND top >= MARGIN * second
                 -> None                   otherwise

The MARGIN gate (top must beat the runner-up by 2x) is what gives ~97% precision:
it kills near-misses that clear the absolute bar but aren't clearly about one
chart (e.g. "is native american property exempt?" scored 0.42 on mobile-home but
its second-best was 0.28, so the margin correctly rejects it). It also rescues
lower-scoring-but-unambiguous topics (agricultural queries score ~0.55, well
under a naive 0.8 bar, but clear the 2x margin).

Env overrides (read live per call, repo convention): FLOWCHART_ROUTER_ENABLED
(default "true"), FLOWCHART_SEED_ABS (default 0.40), FLOWCHART_SEED_MARGIN
(default 2.0).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

from flowcharts import FLOWCHART_REGISTRY

logger = logging.getLogger(__name__)

SEED_ABS_DEFAULT = 0.40
SEED_MARGIN_DEFAULT = 2.0

# Cache of (flowchart_id, embedding) for the registry descriptions. Embedded once
# per warm container on first route() call — the descriptions never change at
# runtime. Kept module-level so it survives across invocations of a warm Lambda.
_chart_vectors: list[tuple[str, list[float]]] | None = None


@dataclass
class FlowchartMatch:
    """Result of routing one query against the flowchart registry."""

    flowchart_id: str | None  # None => no seed
    action: str  # "SEED" | "none"
    top_score: float
    second_score: float
    top_chart: str | None  # best chart id even when not seeded (for tracing)
    scores: dict[str, float]  # every chart's score, for logging/inspection


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _ensure_chart_vectors(embed_fn) -> list[tuple[str, list[float]]]:
    global _chart_vectors
    if _chart_vectors is None:
        vectors: list[tuple[str, list[float]]] = []
        for chart in FLOWCHART_REGISTRY:
            desc = chart.get("router_description") or chart.get("summary") or chart["title"]
            vectors.append((chart["flowchart_id"], embed_fn(desc)))
        _chart_vectors = vectors
    return _chart_vectors


def route(query: str, embed_fn=None) -> FlowchartMatch:
    """Score ``query`` against the flowchart registry and decide SEED-or-nothing.

    ``embed_fn`` defaults to executor.embed_query; injectable for tests so the
    router can be exercised without Bedrock.
    """
    empty = FlowchartMatch(None, "none", 0.0, 0.0, None, {})
    if os.environ.get("FLOWCHART_ROUTER_ENABLED", "true").lower() != "true":
        return empty
    if not (query or "").strip():
        return empty

    if embed_fn is None:
        from agent_tools.executor import embed_query as embed_fn  # lazy: avoid import cycle

    try:
        qv = embed_fn(query)
        chart_vectors = _ensure_chart_vectors(embed_fn)
    except Exception:  # noqa: BLE001 — routing is best-effort; never break the loop
        logger.warning("flowchart routing failed; no seed", exc_info=True)
        return empty

    scores = {fid: _cosine(qv, cv) for fid, cv in chart_vectors}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_chart, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    seed_abs = float(os.environ.get("FLOWCHART_SEED_ABS", SEED_ABS_DEFAULT))
    margin = float(os.environ.get("FLOWCHART_SEED_MARGIN", SEED_MARGIN_DEFAULT))

    if top_score >= seed_abs and top_score >= margin * second_score:
        return FlowchartMatch(top_chart, "SEED", top_score, second_score, top_chart, scores)
    return FlowchartMatch(None, "none", top_score, second_score, top_chart, scores)
