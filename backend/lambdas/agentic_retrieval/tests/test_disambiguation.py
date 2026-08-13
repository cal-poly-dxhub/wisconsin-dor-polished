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
    def test_followup_short_circuits_to_proceed(self, monkeypatch):
        disambiguation, converse = _load(monkeypatch)
        verdict = disambiguation.classify_query(
            "What about commercial?", chat_history=[{"query": "q", "answer": "a"}]
        )
        assert verdict == disambiguation.VERDICT_PROCEED
        # No LLM call — history means the loop already has context.
        converse.assert_not_called()

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
