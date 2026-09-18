"""Grading tests for the graph-regression harness's adequacy-verdict checks.

These exercise the pure grading path only — no AWS, no Bedrock, no Neptune.
`grade()` is called with run_judge=False so the rubric LLM judge never fires;
what is under test is the deterministic verdict/axis logic layered on top.
"""

from dataclasses import dataclass, field

from tools.ingestion.ops.run_graph_regression import (
    filter_entries,
    finding_to_dict,
    grade,
    grade_verdict,
    history_from_entry,
)


def make_run(finding: dict | None = None, **overrides) -> dict:
    """A minimal run record of the shape run_one_query() produces."""
    run = {
        "queryId": "gq-fake",
        "stratum": "B",
        "query": "is my property exempt?",
        "cited_doc_ids": ["statutes-70"],
        "answer": "Exemption under §70.11 turns on ownership and use.",
        "finding": finding,
    }
    run.update(overrides)
    return run


def make_finding(verdict: str = "ANSWER", clarification: dict | None = None) -> dict:
    return {
        "verdict": verdict,
        "supported": "",
        "unsupported": "",
        "clarification": clarification,
        "rationale": "",
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "latency_ms": 900,
    }


# ── grade_verdict: no expectations ───────────────────────────────────────────


def test_no_expectations_is_not_checked_and_passes():
    g = grade_verdict({}, make_run(make_finding("DECLINE")))
    assert g["verdict_checked"] is False
    assert g["verdict_pass"] is True
    # The actual verdict is still surfaced for the compare report.
    assert g["actual_verdict"] == "DECLINE"


# ── grade_verdict: verdict matching ──────────────────────────────────────────


def test_matching_verdict_passes():
    g = grade_verdict({"expected_verdict": "DECLINE"}, make_run(make_finding("DECLINE")))
    assert g["verdict_checked"] is True
    assert g["verdict_pass"] is True
    assert g["verdict_reason"] == ""


def test_verdict_comparison_is_case_insensitive():
    g = grade_verdict({"expected_verdict": "answer"}, make_run(make_finding("ANSWER")))
    assert g["verdict_pass"] is True


def test_mismatched_verdict_fails_with_a_reason():
    g = grade_verdict({"expected_verdict": "ANSWER"}, make_run(make_finding("DECLINE")))
    assert g["verdict_pass"] is False
    assert "expected verdict ANSWER" in g["verdict_reason"]
    assert g["actual_verdict"] == "DECLINE"


def test_unknown_expected_verdict_fails_closed():
    g = grade_verdict({"expected_verdict": "MAYBE"}, make_run(make_finding("ANSWER")))
    assert g["verdict_checked"] is True
    assert g["verdict_pass"] is False
    assert "not one of" in g["verdict_reason"]


# ── grade_verdict: CLARIFY needs a real choice ───────────────────────────────


def test_clarify_with_two_options_passes():
    finding = make_finding(
        "CLARIFY",
        {"axis": "classification", "question": "Which?", "options": ["Agricultural", "Commercial"]},
    )
    g = grade_verdict({"expected_verdict": "CLARIFY"}, make_run(finding))
    assert g["verdict_pass"] is True


def test_clarify_with_one_option_fails():
    finding = make_finding(
        "CLARIFY", {"axis": "classification", "question": "Which?", "options": ["Agricultural"]}
    )
    g = grade_verdict({"expected_verdict": "CLARIFY"}, make_run(finding))
    assert g["verdict_pass"] is False
    assert ">=2 clarification options" in g["verdict_reason"]


def test_clarify_with_no_clarification_block_fails():
    g = grade_verdict({"expected_verdict": "CLARIFY"}, make_run(make_finding("CLARIFY")))
    assert g["verdict_pass"] is False


# ── grade_verdict: clarification axis (case-insensitive substring) ───────────


def test_axis_substring_matches_case_insensitively():
    finding = make_finding(
        "CLARIFY",
        {"axis": "Property Classification", "question": "Which?", "options": ["Ag", "Commercial"]},
    )
    entry = {"expected_verdict": "CLARIFY", "expected_clarification_axis": "classification"}
    assert grade_verdict(entry, make_run(finding))["verdict_pass"] is True


def test_axis_mismatch_fails():
    finding = make_finding(
        "CLARIFY", {"axis": "filing deadline", "question": "Which?", "options": ["A", "B"]}
    )
    entry = {"expected_verdict": "CLARIFY", "expected_clarification_axis": "classification"}
    g = grade_verdict(entry, make_run(finding))
    assert g["verdict_pass"] is False
    assert "clarification axis" in g["verdict_reason"]


def test_axis_alone_is_enough_to_trigger_the_check():
    finding = make_finding(
        "CLARIFY", {"axis": "filing deadline", "question": "Which?", "options": ["A", "B"]}
    )
    g = grade_verdict({"expected_clarification_axis": "classification"}, make_run(finding))
    assert g["verdict_checked"] is True
    assert g["verdict_pass"] is False


# ── grade_verdict: judge disabled / unavailable ──────────────────────────────


def test_missing_finding_is_unchecked_not_failed():
    """A branch without adequacy_judge must not turn every scope case red."""
    g = grade_verdict({"expected_verdict": "DECLINE"}, make_run(None))
    assert g["verdict_checked"] is False
    assert g["verdict_pass"] is True
    assert "disabled/unavailable" in g["verdict_reason"]


# ── grade(): the verdict check joins the overall gate ────────────────────────


def test_grade_overall_pass_requires_the_verdict():
    entry = {
        "queryId": "gq-fake",
        "query": "the sky is blue",
        "must_cite": [],
        "expected_verdict": "DECLINE",
    }
    run = make_run(make_finding("ANSWER"), cited_doc_ids=[])
    g = grade(entry, run, {}, run_judge=False)
    assert g["cite_pass"] is True
    assert g["verdict_pass"] is False
    assert g["overall_pass"] is False
    assert g["expected_verdict"] == "DECLINE"
    assert g["actual_verdict"] == "ANSWER"


def test_grade_overall_pass_when_everything_agrees():
    entry = {
        "queryId": "gq-fake",
        "query": "the sky is blue",
        "must_cite": [],
        "expected_verdict": "DECLINE",
    }
    run = make_run(make_finding("DECLINE"), cited_doc_ids=[])
    g = grade(entry, run, {}, run_judge=False)
    assert g["overall_pass"] is True
    assert g["verdict_checked"] is True


def test_grade_still_fails_on_must_cite_even_with_a_right_verdict():
    entry = {
        "queryId": "gq-fake",
        "query": "What is the Markarian case?",
        "must_cite": ["case-law-45-wis-2d-683"],
        "expected_verdict": "ANSWER",
    }
    g = grade(entry, make_run(make_finding("ANSWER")), {}, run_judge=False)
    assert g["verdict_pass"] is True
    assert g["cite_pass"] is False
    assert g["overall_pass"] is False


def test_grade_without_expectations_is_unaffected():
    entry = {"queryId": "gq-fake", "query": "q", "must_cite": ["statutes-70"]}
    g = grade(entry, make_run(None), {}, run_judge=False)
    assert g["overall_pass"] is True
    assert g["expected_verdict"] is None
    assert g["verdict_checked"] is False


# ── finding_to_dict: dataclass → JSON-serializable dict ──────────────────────


@dataclass
class _FakeClarification:
    axis: str = "classification"
    question: str = "Which classification?"
    options: list[str] = field(default_factory=lambda: ["Agricultural", "Manufacturing"])


@dataclass
class _FakeFinding:
    verdict: str = "CLARIFY"
    supported: str = "general appeal path"
    unsupported: str = "the specific classification"
    clarification: _FakeClarification | None = field(default_factory=_FakeClarification)
    rationale: str = "needs the classification"
    model_id: str = "us.anthropic.claude-sonnet-4-6"
    latency_ms: int = 1200


def test_finding_to_dict_flattens_a_dataclass():
    d = finding_to_dict(_FakeFinding())
    assert d["verdict"] == "CLARIFY"
    assert d["clarification"]["axis"] == "classification"
    assert d["clarification"]["options"] == ["Agricultural", "Manufacturing"]
    # And the flattened dict grades identically to a hand-built one.
    entry = {"expected_verdict": "CLARIFY", "expected_clarification_axis": "classification"}
    assert grade_verdict(entry, make_run(d))["verdict_pass"] is True


def test_finding_to_dict_passes_none_through():
    assert finding_to_dict(None) is None


# ── history_from_entry: two-turn cases ───────────────────────────────────────


def test_history_is_empty_for_a_single_turn_case():
    assert history_from_entry({"query": "q"}) == []


def test_history_folds_the_clarification_into_the_prior_answer():
    entry = {
        "query": "Agricultural",
        "history": [
            {
                "query": "How is my property assessed?",
                "answer": "Generally at full market value under §70.32.",
                "clarification": "What kind of property is it?",
            }
        ],
    }
    history = history_from_entry(entry)
    assert len(history) == 1
    assert history[0]["query"] == "How is my property assessed?"
    assert history[0]["answer"].startswith("Generally at full market value")
    assert history[0]["answer"].endswith("What kind of property is it?")
    # Production shape: only query/answer reach run_agentic_loop.
    assert set(history[0]) == {"query", "answer"}


# ── filter_entries: --tags / --ids ───────────────────────────────────────────

_ENTRIES = [
    {"queryId": "a", "tags": ["scope", "clarify"]},
    {"queryId": "b", "tags": ["guard"]},
    {"queryId": "c"},
]


def test_filter_by_tag_is_case_insensitive():
    assert [e["queryId"] for e in filter_entries(_ENTRIES, tags=["SCOPE"])] == ["a"]


def test_filter_by_multiple_tags_is_a_union():
    assert [e["queryId"] for e in filter_entries(_ENTRIES, tags=["scope", "guard"])] == ["a", "b"]


def test_untagged_entries_are_excluded_by_a_tag_filter():
    assert [e["queryId"] for e in filter_entries(_ENTRIES, tags=["scope"])] != ["c"]


def test_ids_and_tags_are_anded():
    assert filter_entries(_ENTRIES, ids=["b"], tags=["scope"]) == []


def test_no_filters_returns_everything():
    assert filter_entries(_ENTRIES) == _ENTRIES
