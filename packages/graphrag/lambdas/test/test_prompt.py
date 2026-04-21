"""Regression tests for the system prompt.

These tests pin down specific phrases that docs/graphrag.md calls out as
load-bearing. They fail if someone deletes the anti-hallucination framing
or the applicability matrix.
"""


def test_prompt_exists_and_is_nonempty():
    from prompt import SYSTEM_PROMPT

    assert SYSTEM_PROMPT
    assert len(SYSTEM_PROMPT) > 500


def test_prompt_requires_tool_sourced_citations():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: "ONLY cite documents retrieved via tools"
    assert "ONLY cite documents" in SYSTEM_PROMPT


def test_prompt_requires_graph_traversal():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §1: "PREFER graph traversal over get_document with guessed IDs"
    assert "PREFER graph traversal" in SYSTEM_PROMPT


def test_prompt_includes_framework_applicability():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §2: applicability matrix for each framework
    assert "IAAO" in SYSTEM_PROMPT
    assert "USPAP" in SYSTEM_PROMPT
    # Each authority framework needs a "does NOT" clause or equivalent
    assert "does NOT" in SYSTEM_PROMPT or "is not binding" in SYSTEM_PROMPT


def test_prompt_distinguishes_requires_vs_recommends():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: distinguish REQUIRES vs RECOMMENDS
    assert "REQUIRES" in SYSTEM_PROMPT
    assert "RECOMMENDS" in SYSTEM_PROMPT


def test_prompt_lists_out_of_scope_topics():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: out-of-scope awareness
    # Wisconsin DOR is property-tax-focused; federal income tax is out of scope
    assert "federal income tax" in SYSTEM_PROMPT.lower() or "NOT in the graph" in SYSTEM_PROMPT


def test_prompt_mandates_fetch_case_opinion_discretion():
    from prompt import SYSTEM_PROMPT

    # The agent should NOT fetch full opinions for simple questions
    assert "fetch_case_opinion" in SYSTEM_PROMPT
