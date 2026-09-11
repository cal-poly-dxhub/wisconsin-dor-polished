"""Tests for the pre-loop flowchart router (SEED-or-nothing margin gate).

Uses a deterministic fake embedder so the router's threshold/margin logic is
tested without Bedrock. We assign each flowchart description a distinct unit
axis, then synthesize a query vector to exercise each decision path. Cosine
similarity between the query vector and the chart axes drives the SEED decision.
"""

import flowchart_router
import pytest
from flowchart_router import route
from flowcharts import FLOWCHART_REGISTRY

# Give each registered chart its own orthogonal axis in R^N.
_CHART_IDS = [c["flowchart_id"] for c in FLOWCHART_REGISTRY]
_N = len(_CHART_IDS)
_AXIS = {fid: [1.0 if i == j else 0.0 for j in range(_N)] for i, fid in enumerate(_CHART_IDS)}
_DESC_TO_AXIS = {c["router_description"]: _AXIS[c["flowchart_id"]] for c in FLOWCHART_REGISTRY}


def _embedder(query_vec):
    """Return embed_fn: chart descriptions -> their axis; anything else -> query_vec."""

    def embed(text: str):
        if text in _DESC_TO_AXIS:
            return _DESC_TO_AXIS[text]
        return query_vec

    return embed


@pytest.fixture(autouse=True)
def _reset_vectors():
    flowchart_router._chart_vectors = None
    yield
    flowchart_router._chart_vectors = None


def _axis(fid: str):
    return list(_AXIS[fid])


def _between(fid_a: str, fid_b: str):
    a, b = _AXIS[fid_a], _AXIS[fid_b]
    return [a[i] + b[i] for i in range(_N)]


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FLOWCHART_ROUTER_ENABLED", "false")
    m = route("anything", embed_fn=_embedder(_axis("flowcharts-mobile-home")))
    assert m.action == "none" and m.flowchart_id is None


def test_empty_query():
    assert route("   ", embed_fn=_embedder(_axis("flowcharts-mobile-home"))).action == "none"


def test_clear_match_seeds():
    # Query aligns exactly with one chart's axis, orthogonal to the rest.
    m = route("is my mobile home taxable", embed_fn=_embedder(_axis("flowcharts-mobile-home")))
    assert m.action == "SEED"
    assert m.flowchart_id == "flowcharts-mobile-home"
    assert m.top_score == pytest.approx(1.0)
    assert m.second_score == pytest.approx(0.0, abs=1e-6)


def test_ambiguous_match_rejected_by_margin():
    # 45 degrees between two charts: top == second -> fails 2x margin -> no seed,
    # even though top (~0.707) clears the absolute bar.
    q = _between("flowcharts-mobile-home", "flowcharts-ag-classification")
    m = route("ambiguous", embed_fn=_embedder(q))
    assert m.action == "none"
    assert m.top_score == pytest.approx(m.second_score, abs=1e-6)
    assert m.top_score > 0.40  # cleared absolute, killed by margin


def test_low_score_rejected_by_absolute():
    # Query lives mostly on an extra dimension the charts don't occupy, with a
    # small lead on one chart axis: top is below the absolute bar -> no seed.
    q = [0.0] * _N + [1.0]  # extra dim dominates
    q[0] = 0.30  # small overlap with chart 0
    # Chart axes are length-_N; pad the embedder's chart vectors to match dims by
    # having the query only overlap via its first _N components (extra dim ignored
    # by cosine against chart axes that are zero there).
    m = route("weakly related", embed_fn=_embedder(q))
    assert m.action == "none"
    assert m.top_score < 0.40


def test_equal_scores_rejected_by_margin():
    # Equal tiny component on every axis: all cosines identical -> margin fails.
    q = [0.1] * _N
    m = route("equal across all", embed_fn=_embedder(q))
    assert m.action == "none"


def test_margin_env_override(monkeypatch):
    # Loosen margin to 1.0 so the 45-degree tie now seeds (top >= 1.0*second).
    monkeypatch.setenv("FLOWCHART_SEED_MARGIN", "1.0")
    q = _between("flowcharts-mobile-home", "flowcharts-ag-classification")
    m = route("ambiguous", embed_fn=_embedder(q))
    assert m.action == "SEED"
    # Ties break to the first-ranked; both are equal so either is acceptable.
    assert m.flowchart_id in ("flowcharts-mobile-home", "flowcharts-ag-classification")
