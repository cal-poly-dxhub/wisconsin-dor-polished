"""How a flowchart reaches the answer — router seed vs. agent-fetched.

Covers the two halves of the loop-side contract:

* which texts the pre-loop router is asked to score (the user's own words
  first, the auto-refined rewrite last, and the question underneath a chip
  reply), and
* that a chart the agent fetches itself mid-loop is registered exactly like a
  router-seeded one: discoverable (so it gets a citation card), and carried out
  on ``seeded_flowchart`` so the "Walk the flowchart" payload is delivered and
  persisted.
"""

import itertools
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pydantic")

SIDECAR = {
    "flowchart_id": "flowcharts-trust-public",
    "title": "Property Held in Trust in Public Interest",
    "summary": "Decision tree for sec. 70.11(20).",
    "disclaimer": "... a thorough review of each property is still required.",
    "start_node": "start",
    "nodes": [{"id": "start", "type": "start", "label": "START"}],
    "edges": [],
    "source": {
        "doc_id": "wpam-wisconsin-property-assessment-manual-2026",
        "wpam_page": "19-27",
        "pdf_page": 691,
        "source_url": "https://www.revenue.wi.gov/documents/wpam26.pdf#page=691",
    },
}


def _match(action="none", flowchart_id=None, top=0.0, second=0.0, matched_on="original"):
    import flowchart_router

    return flowchart_router.FlowchartMatch(
        flowchart_id=flowchart_id,
        action=action,
        top_score=top,
        second_score=second,
        top_chart=flowchart_id,
        scores={},
        matched_on=matched_on,
    )


def _converse(*tool_calls):
    """Bedrock responses: one toolUse per turn, in order."""
    return [
        {
            "output": {"message": {"content": [{"toolUse": tc}]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        for tc in tool_calls
    ]


@pytest.fixture
def loop_env(fresh_modules, monkeypatch):
    """phase_a wired to fakes, with the router stubbed and its calls recorded."""
    # fresh_modules purges the package's modules, so import flowchart_router
    # only afterwards — otherwise the loop re-imports a different module object
    # than the one this fixture patches.
    (phase_a,) = fresh_modules("loop.phase_a")
    import flowchart_router

    monkeypatch.setattr(
        phase_a, "faq_search_direct", lambda q, n, e: {"faqs": [], "count": 0}, raising=True
    )
    monkeypatch.setattr(phase_a, "build_faq_resource", lambda results: None)
    phase_a.neptune.get_document = MagicMock(return_value=None)

    route_calls: list[dict] = []

    def make_router(result):
        def _route(query, embed_fn=None, *, candidates=None):
            route_calls.append({"query": query, "candidates": candidates})
            return result

        monkeypatch.setattr(flowchart_router, "route", _route)

    return phase_a, make_router, route_calls


class TestRouterCandidates:
    """Defect 1: the router must score the user's words, not only the rewrite."""

    def test_original_query_is_the_first_candidate(self, loop_env, monkeypatch):
        phase_a, make_router, route_calls = loop_env
        make_router(_match())
        monkeypatch.setattr(
            phase_a,
            "converse_with_cache",
            MagicMock(
                side_effect=_converse(
                    {"toolUseId": "t1", "name": "prepare_answer", "input": {"cited_doc_ids": []}}
                )
            ),
        )
        monkeypatch.setattr(
            phase_a,
            "execute_tool",
            lambda name, i, n, **kw: (
                {"cited_doc_ids": [], "answer_plan": ""}
                if name == "prepare_answer"
                else {"chunks": []}
            ),
        )

        phase_a.run_agentic_loop("how do I apply for an exemption from property tax?")

        assert route_calls[0]["candidates"][0] == (
            "original",
            "how do I apply for an exemption from property tax?",
        )

    def test_chip_reply_adds_history_then_refined(self, loop_env, monkeypatch):
        phase_a, make_router, route_calls = loop_env
        make_router(_match())
        import agent_tools.executor as executor

        monkeypatch.setattr(
            executor,
            "_auto_refine",
            lambda q, h: ("Wisconsin property tax exemption application", None),
        )
        monkeypatch.setattr(
            phase_a,
            "converse_with_cache",
            MagicMock(
                side_effect=_converse(
                    {"toolUseId": "t1", "name": "prepare_answer", "input": {"cited_doc_ids": []}}
                )
            ),
        )
        monkeypatch.setattr(
            phase_a,
            "execute_tool",
            lambda name, i, n, **kw: (
                {"cited_doc_ids": [], "answer_plan": ""}
                if name == "prepare_answer"
                else {"chunks": []}
            ),
        )

        phase_a.run_agentic_loop(
            "Not certain — general information",
            chat_history=[
                {
                    "query": "how do I apply for an exemption from property tax?",
                    "answer": "What type of property?",
                }
            ],
        )

        assert [label for label, _ in route_calls[0]["candidates"]] == [
            "original",
            "history",
            "refined",
        ]
        assert route_calls[0]["candidates"][1][1] == (
            "how do I apply for an exemption from property tax?"
        )
        assert route_calls[0]["candidates"][2][1] == "Wisconsin property tax exemption application"

    @pytest.mark.parametrize(
        ("query", "history", "expected"),
        [
            (
                "Not certain — general information",
                [{"query": "is my barn exempt?"}],
                "is my barn exempt?",
            ),
            ("Agricultural", [{"query": "how is my land valued?"}], "how is my land valued?"),
            # A self-contained question carries its own topic — don't drag the
            # previous one back in.
            (
                "What exemptions can apply to a mobile home in a licensed park?",
                [{"query": "is my barn exempt?"}],
                None,
            ),
            ("anything", [], None),
            ("anything", [{"query": "   "}], None),
        ],
    )
    def test_prior_user_question(self, loop_env, query, history, expected):
        phase_a, _, _ = loop_env
        assert phase_a._prior_user_question(query, history) == expected


class TestFlowchartRegistration:
    """Defect 3: an agent-fetched chart registers like a router-seeded one."""

    def _run(self, phase_a, monkeypatch, *, tool_flowchart_id=None):
        calls = [
            {
                "toolUseId": "t1",
                "name": "get_flowchart",
                "input": {"flowchart_id": tool_flowchart_id},
            },
            {
                "toolUseId": "t2",
                "name": "prepare_answer",
                "input": {
                    "cited_doc_ids": ["flowcharts-trust-public"],
                    "answer_plan": "walk the chart",
                },
            },
        ]
        if tool_flowchart_id is None:
            calls = calls[1:]
        monkeypatch.setattr(
            phase_a, "converse_with_cache", MagicMock(side_effect=_converse(*calls))
        )

        def fake_execute(name, tool_input, neptune_client, **kwargs):
            if name == "get_flowchart":
                return dict(SIDECAR)
            if name == "prepare_answer":
                return {
                    "cited_doc_ids": tool_input.get("cited_doc_ids", []),
                    "answer_plan": tool_input.get("answer_plan", ""),
                }
            return {"chunks": []}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)
        return phase_a.run_agentic_loop(
            "is property in trust in public interest exempt?",
            query_id="q-1",
            trace_seq=itertools.count(1).__next__,
        )

    def test_seeded_chart_registers(self, loop_env, monkeypatch):
        phase_a, make_router, _ = loop_env
        make_router(_match("SEED", "flowcharts-trust-public", 0.552, 0.295))
        import flowcharts

        monkeypatch.setattr(flowcharts, "get_flowchart", lambda fid, **kw: dict(SIDECAR))

        result = self._run(phase_a, monkeypatch)

        assert result.discovery["flowcharts-trust-public"] == "flowchart-seed"
        assert "flowcharts-trust-public" in result.all_doc_ids
        assert result.seeded_flowchart["flowchart_id"] == "flowcharts-trust-public"
        assert result.seeded_flowchart_score == pytest.approx(0.552)

    def test_agent_fetched_chart_registers_the_same_way(self, loop_env, monkeypatch):
        phase_a, make_router, _ = loop_env
        make_router(_match())  # router declined — the agent found it itself

        result = self._run(
            phase_a, monkeypatch, tool_flowchart_id="flowcharts-trust-public-interest"
        )

        # Same three registrations as a seed: discoverable, citable, delivered.
        assert result.discovery["flowcharts-trust-public"] == "flowchart-tool"
        assert "flowcharts-trust-public" in result.all_doc_ids
        assert result.seeded_flowchart["flowchart_id"] == "flowcharts-trust-public"
        # No router score: nothing was routed.
        assert result.seeded_flowchart_score is None
        assert result.cited_doc_ids == ["flowcharts-trust-public"]

    def test_seed_wins_over_a_later_tool_fetch(self, loop_env, monkeypatch):
        phase_a, make_router, _ = loop_env
        make_router(_match("SEED", "flowcharts-trust-public", 0.60, 0.20))
        import flowcharts

        monkeypatch.setattr(flowcharts, "get_flowchart", lambda fid, **kw: dict(SIDECAR))

        other = {**SIDECAR, "flowchart_id": "flowcharts-bible-camp", "title": "Bible Camps"}
        monkeypatch.setattr(
            phase_a,
            "converse_with_cache",
            MagicMock(
                side_effect=_converse(
                    {
                        "toolUseId": "t1",
                        "name": "get_flowchart",
                        "input": {"flowchart_id": "flowcharts-bible-camp"},
                    },
                    {
                        "toolUseId": "t2",
                        "name": "prepare_answer",
                        "input": {"cited_doc_ids": [], "answer_plan": ""},
                    },
                )
            ),
        )

        def fake_execute(name, tool_input, neptune_client, **kwargs):
            if name == "get_flowchart":
                return dict(other)
            if name == "prepare_answer":
                return {"cited_doc_ids": [], "answer_plan": ""}
            return {"chunks": []}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)
        result = phase_a.run_agentic_loop("is a bible camp exempt?")

        # The seeded chart is the one the answer was structured around.
        assert result.seeded_flowchart["flowchart_id"] == "flowcharts-trust-public"
        # ...but the fetched one is still discoverable/citable.
        assert result.discovery["flowcharts-bible-camp"] == "flowchart-tool"

    def test_failed_fetch_registers_nothing(self, loop_env, monkeypatch):
        phase_a, make_router, _ = loop_env
        make_router(_match())
        monkeypatch.setattr(
            phase_a,
            "converse_with_cache",
            MagicMock(
                side_effect=_converse(
                    {
                        "toolUseId": "t1",
                        "name": "get_flowchart",
                        "input": {"flowchart_id": "flowcharts-nope"},
                    },
                    {
                        "toolUseId": "t2",
                        "name": "prepare_answer",
                        "input": {"cited_doc_ids": [], "answer_plan": ""},
                    },
                )
            ),
        )

        def fake_execute(name, tool_input, neptune_client, **kwargs):
            if name == "get_flowchart":
                return {"error": "Unknown flowchart 'flowcharts-nope'", "available": []}
            if name == "prepare_answer":
                return {"cited_doc_ids": [], "answer_plan": ""}
            return {"chunks": []}

        monkeypatch.setattr(phase_a, "execute_tool", fake_execute)
        result = phase_a.run_agentic_loop("something unrelated")

        assert result.seeded_flowchart is None
        assert not [d for d in result.all_doc_ids if d.startswith("flowcharts-")]
