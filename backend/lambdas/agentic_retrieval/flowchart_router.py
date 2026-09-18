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

WHAT IS SCORED (candidates)
---------------------------
The router scores the **user's own words first**, not a machine rewrite of them.
Turn-0 auto-refinement (and the disambiguation chip flow) can rewrite a question
into a keyword-ish phrase that is a good *retrieval* query but a bad *routing*
signal. Measured: "how do I apply for an exemption from property tax?" scores
0.688 on ``flowcharts-exempt-70-11`` (clean SEED), while its auto-refinement
"Wisconsin property tax exemption application" scores only 0.479 with a 0.313
runner-up — no seed. Routing the refinement lost the chart on the exact query
the chart exists for.

:func:`route` therefore takes an ordered list of candidate texts and seeds on
the FIRST one that clears the gate, cheapest signal first:

    1. ``original`` — the verbatim user message
    2. ``history``  — the question underneath a chip / one-liner follow-up
                      (e.g. the user answered "Not certain — general
                      information" to the property-type chips; their real
                      question is the prior turn)
    3. ``refined``  — the auto-refined retrieval query, scored last as a
                      fallback rather than as the primary signal

``FlowchartMatch.matched_on`` records which candidate won, and the reported
``top_score`` / ``second_score`` are that candidate's (so the trace explains
the decision that was actually made). Candidates are embedded lazily and
evaluation short-circuits on the first SEED, so the common no-history query
costs exactly one embedding, as before.

DECISION RULE
-------------
    route(...) -> SEED <flowchart_id>   if top >= SEED_ABS
                                        and (top - second) >= SEED_GAP
                                        and top >= SEED_MARGIN * second
                 -> None                otherwise

The old rule used the ratio gate alone (``top >= 2.0 * second``). It was too
strict for a chart whose vocabulary overlaps a neighbour's: "Is property in
trust in public interest exempt from taxation?" measured 0.552 on
``flowcharts-trust-public`` against a 0.295 runner-up (``mobile-home``, which
shares the whole "exempt from taxation" register) — a ratio of 1.87, so the
right chart was dropped. An ABSOLUTE GAP separates those cases better than a
ratio does at mid-range scores, because the runner-up's floor is set by shared
tax vocabulary rather than by topical relevance.

Measured on the 6 canonical chart queries + 6 adversarial decoys (Titan v2,
cosine against the registry descriptions):

    query                                                   top / 2nd    gap
    Does my land qualify as agricultural?                 0.546 / 0.168  0.378  SEED ag
    Is a cabinet shop ... manufacturing ...?               0.623 / 0.178  0.445  SEED mfg
    What exemptions can apply to a mobile home?            0.659 / 0.285  0.374  SEED mobile
    how do I apply for an exemption from property tax?     0.688 / 0.304  0.384  SEED 70.11
    is a bible camp exempt from taxation?                  0.862 / 0.359  0.504  SEED camp
    Is property in trust in public interest exempt ...?    0.552 / 0.295  0.257  SEED trust
    Is Native American property taxable or exempt?         0.417 / 0.341  0.076  none
    are all churches exempt?                               0.410 / 0.221  0.188  none
    How are my property taxes determined?                  0.323 / 0.195  0.128  none
    What is the LC-667                                     0.072 / 0.071  0.000  none
    what gives an assessor the presumption of correctness? 0.102 / 0.086  0.016  none
    Wisconsin property tax exemption application           0.479 / 0.313  0.167  none

SEED_ABS = 0.45 and SEED_GAP = 0.20 separate the two groups with every chart
query seeding and no decoy seeding. Each bar is load-bearing on a different
decoy, which is why both are kept:

  * "are all churches exempt?" (the coercion tripwire — a general church is
    exempt under sec. 70.11(4), NOT the narrow bible-camp chart) tops out at
    0.410, below SEED_ABS; its gap of 0.188 is also below SEED_GAP, but only
    just, so the absolute bar is what gives it real margin.
  * "Wisconsin property tax exemption application" (the auto-refinement above)
    clears SEED_ABS at 0.479 and is rejected by the gap (0.167).

The ratio gate is kept as a third, now non-binding guard (default lowered
2.0 -> 1.6; the weakest chart query, trust-public, ratio 1.87). It still earns
its keep at high scores, where a 0.20 gap is easy to clear: a 0.70 / 0.50 pair
is genuinely ambiguous and the ratio rejects it.

Env overrides (read live per call, repo convention): FLOWCHART_ROUTER_ENABLED
(default "true"), FLOWCHART_SEED_ABS (default 0.45), FLOWCHART_SEED_GAP
(default 0.20), FLOWCHART_SEED_MARGIN (default 1.6).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from flowcharts import FLOWCHART_REGISTRY

logger = logging.getLogger(__name__)

SEED_ABS_DEFAULT = 0.45
SEED_GAP_DEFAULT = 0.20
SEED_MARGIN_DEFAULT = 1.6

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
    # Which candidate text produced top_score/second_score: "original",
    # "history", "refined" (or "" when nothing was scored).
    matched_on: str = ""
    matched_text: str = ""
    # Per-candidate (label, top, second) for every candidate actually scored.
    candidates: list[tuple[str, float, float]] = field(default_factory=list)


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


def _thresholds() -> tuple[float, float, float]:
    """(absolute bar, absolute gap, ratio margin), read live from the env."""
    return (
        float(os.environ.get("FLOWCHART_SEED_ABS", SEED_ABS_DEFAULT)),
        float(os.environ.get("FLOWCHART_SEED_GAP", SEED_GAP_DEFAULT)),
        float(os.environ.get("FLOWCHART_SEED_MARGIN", SEED_MARGIN_DEFAULT)),
    )


def passes_gate(top_score: float, second_score: float) -> bool:
    """True when a (top, second) pair clears every seed gate. See module docstring."""
    seed_abs, seed_gap, margin = _thresholds()
    return (
        top_score >= seed_abs
        and (top_score - second_score) >= seed_gap
        and top_score >= margin * second_score
    )


def _dedupe(candidates: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop blank and repeated candidate texts, preserving priority order."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, text in candidates:
        norm = (text or "").strip()
        if not norm or norm.casefold() in seen:
            continue
        seen.add(norm.casefold())
        out.append((label, norm))
    return out


def route(
    query: str,
    embed_fn=None,
    *,
    candidates: Sequence[tuple[str, str]] | None = None,
) -> FlowchartMatch:
    """Score ``query`` against the flowchart registry and decide SEED-or-nothing.

    ``candidates`` is an ordered list of ``(label, text)`` alternatives to score,
    highest-priority first; it defaults to ``[("original", query)]``. Evaluation
    short-circuits on the first candidate that clears the gate, so scoring extra
    alternatives costs nothing when the user's own words already match.

    ``embed_fn`` defaults to executor.embed_query; injectable for tests so the
    router can be exercised without Bedrock.
    """
    empty = FlowchartMatch(None, "none", 0.0, 0.0, None, {})
    if os.environ.get("FLOWCHART_ROUTER_ENABLED", "true").lower() != "true":
        return empty

    pairs = _dedupe(candidates if candidates is not None else [("original", query)])
    if not pairs:
        return empty

    if embed_fn is None:
        from agent_tools.executor import embed_query as embed_fn  # lazy: avoid import cycle

    best: FlowchartMatch | None = None
    scored: list[tuple[str, float, float]] = []
    try:
        chart_vectors = _ensure_chart_vectors(embed_fn)
        for label, text in pairs:
            qv = embed_fn(text)
            scores = {fid: _cosine(qv, cv) for fid, cv in chart_vectors}
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            top_chart, top_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            scored.append((label, top_score, second_score))

            if passes_gate(top_score, second_score):
                return FlowchartMatch(
                    top_chart,
                    "SEED",
                    top_score,
                    second_score,
                    top_chart,
                    scores,
                    matched_on=label,
                    matched_text=text,
                    candidates=scored,
                )
            if best is None or top_score > best.top_score:
                best = FlowchartMatch(
                    None,
                    "none",
                    top_score,
                    second_score,
                    top_chart,
                    scores,
                    matched_on=label,
                    matched_text=text,
                )
    except Exception:  # noqa: BLE001 — routing is best-effort; never break the loop
        logger.warning("flowchart routing failed; no seed", exc_info=True)
        return empty

    if best is None:
        return empty
    best.candidates = scored
    return best
