"""Tests for the pre-loop query classifier (disambiguation.classify_query)."""

from unittest.mock import MagicMock


def _load(monkeypatch):
    """Import the disambiguation module with a stubbed Bedrock client.

    Returns (module, converse_mock) so tests can drive the classifier verdict
    by setting the mock's return value.
    """
    import disambiguation

    converse = MagicMock()
    monkeypatch.setattr(disambiguation, "_bedrock", MagicMock(converse=converse))
    return disambiguation, converse


def _bedrock_reply(text: str) -> dict:
    return {"output": {"message": {"content": [{"text": text}]}}}


class TestClassifyQuery:
    def test_followup_is_classified_not_short_circuited(self, monkeypatch):
        # A follow-up naming a property type still short-circuits on the local
        # keyword check (no LLM call needed).
        disambiguation, converse = _load(monkeypatch)
        verdict = disambiguation.classify_query(
            "What about commercial?", chat_history=[{"query": "q", "answer": "a"}]
        )
        assert verdict == disambiguation.VERDICT_PROCEED
        converse.assert_not_called()

    def test_generic_followup_reaches_classifier(self, monkeypatch):
        # A generic follow-up with no property-type keyword must reach the LLM
        # classifier (history is passed along), not blanket-PROCEED.
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("DISAMBIGUATE")
        verdict = disambiguation.classify_query(
            "How is my property assessed?",
            chat_history=[{"query": "When is the BOR?", "answer": "The Board of Review meets..."}],
        )
        assert verdict == disambiguation.VERDICT_DISAMBIGUATE
        converse.assert_called_once()
        # Prior conversation is included in the classifier's user message.
        sent = converse.call_args.kwargs["messages"][0]["content"][0]["text"]
        assert "PRIOR CONVERSATION" in sent
        assert "When is the BOR?" in sent
        assert "CURRENT QUESTION" in sent

    def test_established_property_type_followup_proceeds(self, monkeypatch):
        # When the model sees an established property type in history it returns
        # PROCEED — the drill-down case that must NOT re-ask for a type.
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("PROCEED")
        verdict = disambiguation.classify_query(
            "How is the value calculated?",
            chat_history=[
                {"query": "commercial property", "answer": "For commercial property, the income..."}
            ],
        )
        # 'commercial' keyword in the prior answer doesn't short-circuit — the
        # keyword check only looks at the current query, which is generic — so
        # the classifier decides.
        assert verdict == disambiguation.VERDICT_PROCEED
        converse.assert_called_once()

    def test_named_property_type_short_circuits_to_proceed(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        verdict = disambiguation.classify_query(
            "How is agricultural land assessed?", chat_history=[]
        )
        assert verdict == disambiguation.VERDICT_PROCEED
        converse.assert_not_called()

    def test_ownership_class_short_circuits_to_proceed(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        verdict = disambiguation.classify_query("Are church properties exempt?", chat_history=[])
        assert verdict == disambiguation.VERDICT_PROCEED
        converse.assert_not_called()

    def test_out_of_scope_verdict(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("OUT_OF_SCOPE")
        verdict = disambiguation.classify_query("What color is the sky?", chat_history=[])
        assert verdict == disambiguation.VERDICT_OUT_OF_SCOPE

    def test_out_of_scope_with_spaces_variant(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("out of scope")
        verdict = disambiguation.classify_query("Write me a poem", chat_history=[])
        assert verdict == disambiguation.VERDICT_OUT_OF_SCOPE

    def test_disambiguate_verdict(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("DISAMBIGUATE")
        verdict = disambiguation.classify_query("How is my property assessed?", chat_history=[])
        assert verdict == disambiguation.VERDICT_DISAMBIGUATE

    def test_proceed_verdict(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("PROCEED")
        verdict = disambiguation.classify_query(
            "When does the Board of Review meet?", chat_history=[]
        )
        assert verdict == disambiguation.VERDICT_PROCEED

    def test_unrecognized_output_defaults_to_proceed(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("MAYBE?")
        verdict = disambiguation.classify_query("Ambiguous", chat_history=[])
        assert verdict == disambiguation.VERDICT_PROCEED

    def test_out_of_scope_wins_over_disambiguate_substring(self, monkeypatch):
        # A garbled reply containing both tokens must resolve to OUT_OF_SCOPE,
        # the more specific verdict, rather than the first-listed one.
        disambiguation, converse = _load(monkeypatch)
        converse.return_value = _bedrock_reply("OUT_OF_SCOPE (not DISAMBIGUATE)")
        verdict = disambiguation.classify_query("q", chat_history=[])
        assert verdict == disambiguation.VERDICT_OUT_OF_SCOPE

    def test_bedrock_error_fails_open_to_proceed(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        converse.side_effect = RuntimeError("bedrock down")
        verdict = disambiguation.classify_query("q", chat_history=[])
        assert verdict == disambiguation.VERDICT_PROCEED
