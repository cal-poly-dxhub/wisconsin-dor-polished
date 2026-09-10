"""Tests for the vocab_swap stage (primary vocabulary mechanism).

Covers: the trigger gate (no term -> strict no-op), the deterministic suffix
append, refine-then-swap ordering, the VOCAB_SWAP_ENABLED toggle, and
whole-word matching for short trigger terms like "rv".
"""

from unittest.mock import MagicMock

import pytest

SUFFIX = "recreational prefabricated home mobile home exemption Wis. Stat. 70.11(49)"


def _ctx(original_query, refined):
    from agent_tools.stages.base import StageContext

    ctx = StageContext(
        query=original_query,
        neptune=MagicMock(),
        original_user_query=original_query,
    )
    ctx.refined_query = refined
    return ctx


@pytest.fixture(autouse=True)
def _swap_on(monkeypatch):
    # Default to ON for tests; individual tests override as needed.
    monkeypatch.setenv("VOCAB_SWAP_ENABLED", "true")


def test_trigger_appends_suffix_to_refined_query():
    from agent_tools.stages import vocab_swap

    ctx = _ctx(
        "What exemptions can apply to a mobile home?",
        "Wisconsin property tax exemptions mobile home",
    )
    vocab_swap.run(ctx)
    assert ctx.refined_query.endswith(SUFFIX)
    # refine-then-swap: the pre-existing refined text is preserved as the prefix.
    assert ctx.refined_query.startswith("Wisconsin property tax exemptions mobile home")


def test_refine_then_swap_order_appends_to_whatever_refine_produced():
    from agent_tools.stages import vocab_swap

    ctx = _ctx("camping trailer question", "SOME LLM-REFINED TEXT")
    vocab_swap.run(ctx)
    assert ctx.refined_query == f"SOME LLM-REFINED TEXT {SUFFIX}"


def test_no_trigger_term_is_strict_noop():
    from agent_tools.stages import vocab_swap

    ctx = _ctx(
        "How do I appeal my property assessment?",
        "Wisconsin board of review objection process",
    )
    before = ctx.refined_query
    vocab_swap.run(ctx)
    assert ctx.refined_query == before  # unchanged


def test_disabled_flag_is_noop(monkeypatch):
    from agent_tools.stages import vocab_swap

    monkeypatch.setenv("VOCAB_SWAP_ENABLED", "false")
    ctx = _ctx(
        "What exemptions can apply to a mobile home?",
        "Wisconsin property tax exemptions mobile home",
    )
    before = ctx.refined_query
    vocab_swap.run(ctx)
    assert ctx.refined_query == before  # gated off -> no swap


def test_rv_matches_whole_word_only():
    from agent_tools.stages import vocab_swap

    # "rv" inside "harvest" must NOT trigger.
    ctx = _ctx("questions about harvest and preserves", "refined harvest query")
    before = ctx.refined_query
    vocab_swap.run(ctx)
    assert ctx.refined_query == before

    # standalone "RV" DOES trigger.
    ctx2 = _ctx("is my RV exempt?", "refined rv query")
    vocab_swap.run(ctx2)
    assert ctx2.refined_query.endswith(SUFFIX)
