"""Regression tests for the system prompt.

These tests pin down load-bearing phrases. They fail if someone deletes the
anti-hallucination framing or the applicability matrix, and they keep the
bundled fallback byte-identical to config/model_configs.toml.
"""

import tomllib
from pathlib import Path

import pytest

_TOML_PATH = Path(__file__).resolve().parents[4] / "config" / "model_configs.toml"

# config id -> the _prompt_fallback constant that mirrors it. The persona
# suffixes are deliberately not mirrored (prompt.py falls back to "").
_MIRRORED = {
    "agenticRetrieval": "SYSTEM_PROMPT_FALLBACK",
    "answerStream": "ANSWER_STREAM_PROMPT_FALLBACK",
    "adequacyJudge": "ADEQUACY_JUDGE_FALLBACK",
}


@pytest.mark.parametrize("config_id", sorted(_MIRRORED))
def test_fallback_is_byte_identical_to_the_toml(config_id):
    import _prompt_fallback

    toml_prompt = tomllib.loads(_TOML_PATH.read_text())[config_id]["prompt"]
    assert getattr(_prompt_fallback, _MIRRORED[config_id]) == toml_prompt


def test_every_toml_prompt_is_loaded_or_a_persona():
    toml_ids = set(tomllib.loads(_TOML_PATH.read_text()))
    assert toml_ids == set(_MIRRORED) | {"personaGovernment", "personaCitizen"}


def test_prompt_exists_and_is_nonempty():
    from prompt import SYSTEM_PROMPT

    assert SYSTEM_PROMPT
    assert len(SYSTEM_PROMPT) > 500


def test_prompt_requires_tool_sourced_citations():
    from prompt import SYSTEM_PROMPT

    assert "ONLY cite documents" in SYSTEM_PROMPT


def test_prompt_requires_graph_traversal():
    from prompt import SYSTEM_PROMPT

    # Graph traversal is scoped: get_neighbors only for case-law holdings on a
    # specific statute, never a blind walk. (The fallback mirror was re-synced
    # to the live TOML on 2026-09-18; the old "PREFER graph traversal" phrase
    # had already been gone from the live prompt for months.)
    assert "get_neighbors only when" in SYSTEM_PROMPT


def test_prompt_includes_framework_applicability():
    from prompt import SYSTEM_PROMPT

    # Applicability matrix for each framework
    assert "IAAO" in SYSTEM_PROMPT
    assert "USPAP" in SYSTEM_PROMPT
    # Each authority framework needs a "does NOT" clause or equivalent
    assert "does NOT" in SYSTEM_PROMPT or "is not binding" in SYSTEM_PROMPT


def test_prompt_distinguishes_requires_vs_recommends():
    from prompt import SYSTEM_PROMPT

    assert "REQUIRES" in SYSTEM_PROMPT
    assert "RECOMMENDS" in SYSTEM_PROMPT


def test_prompt_lists_out_of_scope_topics():
    from prompt import SYSTEM_PROMPT

    # Wisconsin DOR is property-tax-focused; federal income tax is out of scope
    assert "federal income tax" in SYSTEM_PROMPT.lower() or "NOT in the graph" in SYSTEM_PROMPT


def test_prompt_mandates_fetch_case_opinion_discretion():
    from prompt import SYSTEM_PROMPT

    # The agent should NOT fetch full opinions for simple questions
    assert "fetch_case_opinion" in SYSTEM_PROMPT


def test_prompt_forbids_case_law_as_starting_point():
    from prompt import SYSTEM_PROMPT

    # Case law must not be the entry point of a traversal — statutes first.
    assert "SECONDARY source" in SYSTEM_PROMPT
    assert "does not create rules" in SYSTEM_PROMPT


def test_prompt_reads_case_chunks_before_the_opinion():
    from prompt import SYSTEM_PROMPT

    # Case-law chunks (holding summary + majority analysis) are consulted
    # before the full opinion is fetched.
    assert "holding summary and the majority's analysis" in SYSTEM_PROMPT
    # fetch_case_opinion must be called with the node's citation verbatim.
    assert "fetch_case_opinion, pass the `citation`" in SYSTEM_PROMPT
    assert "VERBATIM" in SYSTEM_PROMPT


def test_prompt_handles_followups():
    from prompt import SYSTEM_PROMPT

    # Short follow-ups are rewritten against history by the auto_refine
    # pipeline stage (refine_query was removed); the prompt must still tell
    # the agent how to treat them.
    lower = SYSTEM_PROMPT.lower()
    assert "follow-up" in lower or "follow up" in lower


def test_prompt_discusses_conversation_history():
    from prompt import SYSTEM_PROMPT

    # Prior turns are injected as user/assistant messages — the prompt must
    # tell the agent how to use them.
    assert "FOLLOW-UP" in SYSTEM_PROMPT or "prior conversation" in SYSTEM_PROMPT.lower()
    # And must forbid citing the prior answer as a source.
    assert "cited_doc_ids" in SYSTEM_PROMPT
