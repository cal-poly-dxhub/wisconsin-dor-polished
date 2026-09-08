"""Tests for the trigger-gated vocab-injection search arm.

Covers the hard gate (no trigger -> strict no-op), the additive-arm behavior
(injected vocabulary + additive-only filtering), word-boundary matching for
short trigger terms like "rv", and the additive cap.
"""

from unittest.mock import MagicMock, patch

import pytest


def _ctx(query, *, chunks=None, broad=None, fetch_k=50, top_k=15, max_per_doc=5):
    from agent_tools.stages.base import StageContext

    neptune = MagicMock()
    neptune.current_wpam_year = 2026
    ctx = StageContext(
        query=query,
        neptune=neptune,
        original_user_query=query,
        top_k=top_k,
        fetch_k=fetch_k,
    )
    ctx.max_per_doc = max_per_doc
    ctx.chunks = chunks or []
    ctx.broad_discovery = broad or []
    return ctx


def _chunk(doc_id, i=0, score=0.9):
    return {"chunk_id": f"{doc_id}-{i}", "text": "t", "score": score, "doc_id": doc_id}


def test_no_trigger_term_is_strict_noop():
    from agent_tools.stages import vocab_injection

    ctx = _ctx("How do I appeal my property assessment?")
    with patch("agent_tools.executor.embed_query") as embed:
        result = vocab_injection.run(ctx)

    assert ctx.vocab_discovery == []
    assert ctx.vocab_injected_terms == []
    embed.assert_not_called()  # no embed
    ctx.neptune.vector_search.assert_not_called()  # no Neptune call
    assert result.trace is None


def test_trigger_injects_vocabulary_and_returns_additive_docs():
    from agent_tools.stages import vocab_injection

    ctx = _ctx(
        "What exemptions can apply to a camping trailer?",
        chunks=[_chunk("statutes-70")],  # main arm already has statutes-70
    )
    ctx.neptune.vector_search.return_value = [
        _chunk("statutes-70"),  # already in main arm -> filtered out
        _chunk("news_pages-cotvc-news-2026-04-29"),  # additive
    ]

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        vocab_injection.run(ctx)

    # Injected query carries the mapped statute vocabulary.
    assert "70.11(49)" in ctx.vocab_query_used
    assert "camping trailer" in ctx.vocab_injected_terms
    # Only the additive doc survives.
    assert [c["doc_id"] for c in ctx.vocab_discovery] == ["news_pages-cotvc-news-2026-04-29"]


@pytest.mark.parametrize(
    "query,should_fire",
    [
        ("Is buying an RV taxable?", True),  # whole word
        ("What about harvest equipment on a farm?", False),  # "rv" inside "harvest"
        ("Tell me about serving size rules", False),  # "rv" inside "serving"
    ],
)
def test_rv_matches_only_as_whole_word(query, should_fire):
    from agent_tools.stages import vocab_injection

    ctx = _ctx(query)
    ctx.neptune.vector_search.return_value = [_chunk("news-x")]
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        vocab_injection.run(ctx)

    if should_fire:
        assert ctx.vocab_injected_terms  # fired
    else:
        assert ctx.vocab_injected_terms == []
        ctx.neptune.vector_search.assert_not_called()


def test_additive_filter_excludes_main_and_broad_docs():
    from agent_tools.stages import vocab_injection

    ctx = _ctx(
        "exemptions for a mobile home",
        chunks=[_chunk("statutes-70")],
        broad=[_chunk("gov_publications-pb060")],
    )
    ctx.neptune.vector_search.return_value = [
        _chunk("statutes-70"),  # in main -> excluded
        _chunk("gov_publications-pb060"),  # in broad -> excluded
        _chunk("news_pages-cotvc-news-2026-04-29"),  # additive
    ]
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        vocab_injection.run(ctx)

    assert [c["doc_id"] for c in ctx.vocab_discovery] == ["news_pages-cotvc-news-2026-04-29"]


def test_additive_cap_respected(monkeypatch):
    from agent_tools.stages import vocab_injection

    monkeypatch.setenv("VOCAB_INJECTION_CAP", "2")
    ctx = _ctx("mobile home exemptions", max_per_doc=10)
    ctx.neptune.vector_search.return_value = [_chunk(f"news-{i}", i) for i in range(6)]
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        vocab_injection.run(ctx)

    assert len(ctx.vocab_discovery) == 2


def test_disabled_flag_is_noop(monkeypatch):
    from agent_tools.stages import vocab_injection

    monkeypatch.setenv("VOCAB_INJECTION_ENABLED", "false")
    ctx = _ctx("What exemptions apply to a camping trailer?")
    with patch("agent_tools.executor.embed_query") as embed:
        vocab_injection.run(ctx)

    assert ctx.vocab_discovery == []
    embed.assert_not_called()
    ctx.neptune.vector_search.assert_not_called()
