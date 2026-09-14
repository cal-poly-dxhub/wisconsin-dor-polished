"""Model-aware sampling: temperature vs effort, and thinking passthrough."""

from streaming import bedrock as b


def test_sonnet_46_keeps_temperature_no_effort(monkeypatch):
    monkeypatch.delenv("AGENTIC_EFFORT", raising=False)
    body = {}
    b.apply_sampling(body, "us.anthropic.claude-sonnet-4-6", {"temperature": 0.0})
    assert body == {"temperature": 0.0}
    assert b.converse_inference_kwargs("us.anthropic.claude-sonnet-4-6", 256) == {
        "inferenceConfig": {"maxTokens": 256, "temperature": 0.0}
    }


def test_sonnet_5_drops_temperature_adds_effort(monkeypatch):
    monkeypatch.setenv("AGENTIC_EFFORT", "low")
    body = {}
    b.apply_sampling(body, "us.anthropic.claude-sonnet-5", {"temperature": 0.0})
    assert body == {"output_config": {"effort": "low"}}
    kw = b.converse_inference_kwargs("global.anthropic.claude-sonnet-5", 256)
    assert kw["inferenceConfig"] == {"maxTokens": 256}
    assert kw["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}


def test_effort_defaults_to_medium(monkeypatch):
    monkeypatch.delenv("AGENTIC_EFFORT", raising=False)
    assert b.model_sampling("us.anthropic.claude-opus-5") == (False, "medium")
    assert b.model_sampling("us.anthropic.claude-opus-4-6") == (True, None)


def test_thinking_blocks_round_trip():
    resp = [
        {"type": "thinking", "thinking": "…", "signature": "sig"},
        {"type": "tool_use", "id": "t1", "name": "vector_search", "input": {"query": "x"}},
    ]
    converse = b._convert_response_content(resp)
    assert converse[0] == {"_anthropic_raw": resp[0]}
    back = b._convert_content_to_messages(converse)
    assert back[0] == resp[0]
    assert back[1]["type"] == "tool_use"
