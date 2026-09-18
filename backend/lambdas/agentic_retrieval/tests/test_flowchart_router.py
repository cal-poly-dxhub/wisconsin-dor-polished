"""Tests for the pre-loop flowchart router (SEED-or-nothing gate).

Two layers:

1. Deterministic fake embedder — each flowchart description gets its own
   orthogonal axis, so a synthesized query vector can hit any (top, second)
   pair we like and every branch of the gate is exercised without Bedrock.
2. A frozen table of REAL Titan v2 cosine scores (measured against the live
   registry descriptions; see the flowchart_router module docstring) asserted
   through :func:`passes_gate`. That table is the tuning evidence for
   SEED_ABS / SEED_GAP / SEED_MARGIN — it fails loudly if someone retunes a
   threshold and silently drops a chart query or admits a decoy.
"""

import math

import flowchart_router
import pytest
from flowchart_router import passes_gate, route
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


def _embedder_by_text(vec_by_text: dict[str, list[float]], default=None):
    """Return embed_fn mapping specific query texts to specific vectors."""
    calls: list[str] = []

    def embed(text: str):
        calls.append(text)
        if text in _DESC_TO_AXIS:
            return _DESC_TO_AXIS[text]
        return vec_by_text.get(text, default or [0.0] * _N)

    embed.calls = calls  # type: ignore[attr-defined]
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


def _scores(top: float, second: float, top_idx: int = 0, second_idx: int = 1):
    """A query vector whose cosine is exactly ``top`` on one chart, ``second`` on another.

    The trailing filler dimension is not shared with any chart axis, so it only
    contributes to the vector's norm — which is what lets the two cosines be set
    independently.
    """
    filler = math.sqrt(max(0.0, 1.0 - top * top - second * second))
    vec = [0.0] * _N
    vec[top_idx] = top
    vec[second_idx] = second
    return [*vec, filler]


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
    assert m.matched_on == "original"


def test_ambiguous_match_rejected():
    # 45 degrees between two charts: top == second -> zero gap -> no seed,
    # even though top (~0.707) clears the absolute bar.
    q = _between("flowcharts-mobile-home", "flowcharts-ag-classification")
    m = route("ambiguous", embed_fn=_embedder(q))
    assert m.action == "none"
    assert m.top_score == pytest.approx(m.second_score, abs=1e-6)
    assert m.top_score > 0.45  # cleared absolute, killed by the gap


def test_low_score_rejected_by_absolute():
    # Clear winner (ratio 3.0, and nothing else near it) but far below the
    # absolute bar: a weak best match is not a match.
    m = route("weakly related", embed_fn=_embedder(_scores(0.30, 0.10)))
    assert m.action == "none"
    assert m.top_score == pytest.approx(0.30)


def test_gap_gate_rejects_clear_ratio_with_thin_gap():
    # abs ok (0.48), ratio ok (exactly 1.6) — but the absolute gap is 0.18.
    # This is the "Wisconsin property tax exemption application" shape.
    m = route("thin gap", embed_fn=_embedder(_scores(0.48, 0.30)))
    assert m.action == "none"
    assert m.top_score - m.second_score == pytest.approx(0.18)


def test_margin_gate_rejects_high_scoring_tie():
    # abs ok, gap ok (0.22) — but at high scores a 0.22 gap is not decisive;
    # the ratio gate (1.46 < 1.6) still rejects it.
    m = route("high but ambiguous", embed_fn=_embedder(_scores(0.70, 0.48)))
    assert m.action == "none"


def test_gap_rescues_mid_range_winner():
    # The trust-public shape the old 2x margin dropped: 0.552 / 0.295.
    m = route("property in trust", embed_fn=_embedder(_scores(0.552, 0.295)))
    assert m.action == "SEED"
    assert m.flowchart_id == _CHART_IDS[0]


def test_equal_scores_rejected():
    # Equal tiny component on every axis: all cosines identical -> gap 0.
    q = [0.1] * _N
    m = route("equal across all", embed_fn=_embedder(q))
    assert m.action == "none"


def test_env_overrides(monkeypatch):
    # Loosen every gate so the 45-degree tie now seeds.
    monkeypatch.setenv("FLOWCHART_SEED_MARGIN", "1.0")
    monkeypatch.setenv("FLOWCHART_SEED_GAP", "0.0")
    monkeypatch.setenv("FLOWCHART_SEED_ABS", "0.40")
    q = _between("flowcharts-mobile-home", "flowcharts-ag-classification")
    m = route("ambiguous", embed_fn=_embedder(q))
    assert m.action == "SEED"
    # Ties break to the first-ranked; both are equal so either is acceptable.
    assert m.flowchart_id in ("flowcharts-mobile-home", "flowcharts-ag-classification")


def test_abs_env_override_blocks(monkeypatch):
    monkeypatch.setenv("FLOWCHART_SEED_ABS", "0.99")
    m = route("trust", embed_fn=_embedder(_scores(0.552, 0.295)))
    assert m.action == "none"


class TestCandidates:
    """Defect 1: route the user's words, not the auto-refined rewrite."""

    def test_original_wins_when_refinement_would_miss(self):
        # Exactly the tester flow: the original question seeds (0.688 / 0.304),
        # the auto-refinement does not (0.479 / 0.313).
        embed = _embedder_by_text(
            {
                "how do I apply for an exemption from property tax?": _scores(0.688, 0.304),
                "Wisconsin property tax exemption application": _scores(0.479, 0.313),
            }
        )
        m = route(
            "how do I apply for an exemption from property tax?",
            embed_fn=embed,
            candidates=[
                ("original", "how do I apply for an exemption from property tax?"),
                ("refined", "Wisconsin property tax exemption application"),
            ],
        )
        assert m.action == "SEED"
        assert m.matched_on == "original"
        assert m.top_score == pytest.approx(0.688)
        assert m.second_score == pytest.approx(0.304)
        # Short-circuited: the refinement was never embedded.
        assert "Wisconsin property tax exemption application" not in embed.calls

    def test_refined_is_a_fallback_not_the_primary_signal(self):
        embed = _embedder_by_text(
            {
                "what about that": _scores(0.10, 0.05),
                "does my land qualify as agricultural": _scores(0.546, 0.168),
            }
        )
        m = route(
            "what about that",
            embed_fn=embed,
            candidates=[
                ("original", "what about that"),
                ("refined", "does my land qualify as agricultural"),
            ],
        )
        assert m.action == "SEED"
        assert m.matched_on == "refined"
        assert m.top_score == pytest.approx(0.546)

    def test_chip_reply_routes_the_question_underneath(self):
        # The chip text itself is meaningless to the router; the prior turn's
        # question is what the user actually asked.
        embed = _embedder_by_text(
            {
                "Not certain — general information": _scores(0.12, 0.09),
                "how do I apply for an exemption from property tax?": _scores(0.688, 0.304),
            }
        )
        m = route(
            "Not certain — general information",
            embed_fn=embed,
            candidates=[
                ("original", "Not certain — general information"),
                ("history", "how do I apply for an exemption from property tax?"),
            ],
        )
        assert m.action == "SEED"
        assert m.matched_on == "history"

    def test_no_candidate_passes_reports_the_best_one(self):
        embed = _embedder_by_text(
            {
                "a": _scores(0.30, 0.10),
                "b": _scores(0.41, 0.34),
            }
        )
        m = route("a", embed_fn=embed, candidates=[("original", "a"), ("refined", "b")])
        assert m.action == "none"
        assert m.matched_on == "refined"
        assert m.top_score == pytest.approx(0.41)
        assert [label for label, _, _ in m.candidates] == ["original", "refined"]

    def test_duplicate_and_blank_candidates_are_not_re_embedded(self):
        embed = _embedder_by_text({"same question": _scores(0.30, 0.10)})
        route(
            "same question",
            embed_fn=embed,
            candidates=[
                ("original", "same question"),
                ("history", "   "),
                ("refined", "Same Question"),
            ],
        )
        assert embed.calls.count("same question") == 1


# (query, top score, runner-up score, expected chart or None) — REAL Titan v2
# cosines against the live registry descriptions, measured 2026-09-18.
MEASURED = [
    ("Does my land qualify as agricultural?", 0.546, 0.168, "flowcharts-ag-classification"),
    (
        "Is a cabinet shop classified as manufacturing and assessed by the state?",
        0.623,
        0.178,
        "flowcharts-manufacturing",
    ),
    ("What exemptions can apply to a mobile home?", 0.659, 0.285, "flowcharts-mobile-home"),
    ("how do I apply for an exemption from property tax?", 0.688, 0.304, "flowcharts-exempt-70-11"),
    ("is a bible camp exempt from taxation?", 0.862, 0.359, "flowcharts-bible-camp"),
    (
        "Is property in trust in public interest exempt from taxation?",
        0.552,
        0.295,
        "flowcharts-trust-public",
    ),
    # Decoys — none of these may seed.
    ("Is Native American property taxable or exempt?", 0.417, 0.341, None),
    ("are all churches exempt?", 0.410, 0.221, None),  # coercion tripwire vs bible-camp
    ("How are my property taxes determined?", 0.323, 0.195, None),
    ("What is the LC-667", 0.072, 0.071, None),
    ("what gives an assessor the presumption of correctness?", 0.102, 0.086, None),
    ("Wisconsin property tax exemption application", 0.479, 0.313, None),  # auto-refinement
]


@pytest.mark.parametrize(("query", "top", "second", "expected"), MEASURED)
def test_measured_titan_scores_gate_as_designed(query, top, second, expected):
    assert passes_gate(top, second) is (expected is not None), query


def test_every_chart_has_one_seeding_query():
    """All six charts are reachable — no chart is stranded by the thresholds."""
    seeded = {chart for _q, top, second, chart in MEASURED if chart and passes_gate(top, second)}
    assert seeded == set(_CHART_IDS)
