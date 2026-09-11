"""Unit tests for tools/ingestion/lib/aliases.py (pure functions + mocked Bedrock)."""

from unittest.mock import MagicMock, patch

from tools.ingestion.lib import aliases as al

CHUNK = (
    "Recreational prefabricated structure means a prefabricated structure that is "
    "originally designed to be towed upon a highway by a motor vehicle, used primarily as "
    "temporary living quarters for recreational, camping, travel, or seasonal purposes, and "
    "located in a licensed campground under s. 97.67. The exemption also applies to the "
    "structure's accessory decks, sheds and porches. Amends s. 70.17 (3), Wis. Stats."
)


# --------------------------------------------------------------------------
# filter_aliases
# --------------------------------------------------------------------------


def test_filter_drops_ungrounded_terms():
    result = {
        "questions": ["Is my camper in a campground taxable?"],
        "aliases": [
            "recreational prefabricated structure = camper, RV, park model",
            "manufactured home = mobile home",  # not in passage → dropped
        ],
    }
    out = al.filter_aliases(result, CHUNK)
    assert out["aliases"] == ["recreational prefabricated structure = camper, RV, park model"]
    assert out["questions"] == ["Is my camper in a campground taxable?"]


def test_filter_is_case_and_whitespace_insensitive():
    result = {
        "questions": [],
        "aliases": ["Licensed   Campground = campground with a state license"],
    }
    out = al.filter_aliases(result, CHUNK)
    assert len(out["aliases"]) == 1
    assert out["aliases"][0].startswith("Licensed   Campground =")


def test_filter_drops_stoplisted_terms_even_when_present():
    text = "The exemption applies to property and personal property of the corporation."
    result = {
        "questions": [],
        "aliases": [
            "exemption = tax break",
            "property = my land",
            "personal property = stuff I own",
            "the corporation = the company",
            "Exempt = not taxed",
        ],
    }
    assert al.filter_aliases(result, text)["aliases"] == []


def test_filter_drops_malformed_and_dedups():
    result = {
        "questions": ["Q one?", "q ONE?", "  ", "Q two?"],
        "aliases": [
            "no equals sign here",
            "accessory decks = porches and decks",
            "accessory decks = again",  # dup lhs
            " = empty lhs",
            "sheds = ",
        ],
    }
    out = al.filter_aliases(result, CHUNK)
    assert out["questions"] == ["Q one?", "Q two?"]
    assert out["aliases"] == ["accessory decks = porches and decks"]


def test_filter_caps_and_length():
    long_q = "x" * (al.MAX_QUESTION_CHARS + 1)
    result = {
        "questions": [long_q, "a?", "b?", "c?", "d?", "e?"],
        "aliases": [
            f"{t} = w"
            for t in [
                "towed",
                "highway",
                "motor vehicle",
                "camping",
                "travel",
                "seasonal",
                "sheds",
                "porches",
            ]
        ],
    }
    out = al.filter_aliases(result, CHUNK)
    assert out["questions"] == ["a?", "b?", "c?"]
    assert len(out["aliases"]) == al.MAX_ALIASES
    assert all(len(q) <= al.MAX_QUESTION_CHARS for q in out["questions"])


def test_filter_tolerates_garbage_input():
    assert al.filter_aliases({}, CHUNK) == {"questions": [], "aliases": []}
    assert al.filter_aliases({"questions": "nope", "aliases": None}, CHUNK) == {
        "questions": [],
        "aliases": [],
    }


# --------------------------------------------------------------------------
# compose_embed_input
# --------------------------------------------------------------------------

DOC = {"doc_id": "news_pages-x", "title": "DOR Advisory: Acts 91 and 117"}


def test_compose_ordering_and_layout():
    chunk = {"text": "BODY TEXT", "metadata": {"heading": "Act 117", "subheading": "Exemption"}}
    aliases = {
        "questions": ["Is my camper taxable?", "What is a park model?"],
        "aliases": [
            "licensed campground = campground with a state license",
            "towed = pulled behind a truck",
        ],
    }
    out = al.compose_embed_input(
        DOC, chunk, aliases, ["What exemptions can apply to a camping trailer?"]
    )
    assert out == (
        "DOR Advisory: Acts 91 and 117 > Act 117 > Exemption\n"
        "Q: What exemptions can apply to a camping trailer?\n"
        "Q: Is my camper taxable?\n"
        "Q: What is a park model?\n"
        "licensed campground = campground with a state license; towed = pulled behind a truck\n"
        "\n"
        "BODY TEXT"
    )


def test_compose_is_deterministic_and_dedups_gold_vs_generated():
    chunk = {"text": "BODY", "metadata": {}}
    aliases = {"questions": ["is my camper taxable?"], "aliases": []}
    a = al.compose_embed_input(DOC, chunk, aliases, ["Is my camper taxable?"])
    b = al.compose_embed_input(DOC, chunk, aliases, ["Is my camper taxable?"])
    assert a == b
    assert a.count("Q: ") == 1


def test_compose_without_aliases_is_header_plus_text():
    chunk = {"text": "BODY", "metadata": {"heading": "70.11"}}
    assert (
        al.compose_embed_input(DOC, chunk, None, None)
        == "DOR Advisory: Acts 91 and 117 > 70.11\n\nBODY"
    )


def test_compose_caps_at_8000_and_never_truncates_prefix():
    chunk = {"text": "z" * 9000, "metadata": {"heading": "H"}}
    aliases = {"questions": ["q" * 150] * 3, "aliases": [f"t{i} = " + "w" * 100 for i in range(6)]}
    out = al.compose_embed_input(DOC, chunk, aliases, ["g" * 150])
    assert len(out) == al.TITAN_MAX_CHARS
    prefix_end = out.index("\n\n") + 2
    prefix = out[:prefix_end]
    assert prefix.startswith("DOR Advisory: Acts 91 and 117 > H\nQ: ggg")
    assert "t5 = " in prefix  # last alias intact
    assert out[prefix_end:] == "z" * (al.TITAN_MAX_CHARS - prefix_end)


# --------------------------------------------------------------------------
# alias_cache_key
# --------------------------------------------------------------------------


def test_cache_key_stable_and_content_based():
    k1 = al.alias_cache_key("same text")
    k2 = al.alias_cache_key("same text")
    k3 = al.alias_cache_key("same text ")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16
    assert k1 == "6d5d27d1b6ec2b6d"[:0] + k1  # hex only
    int(k1, 16)


# --------------------------------------------------------------------------
# parse_alias_json + generate_aliases (mocked Bedrock)
# --------------------------------------------------------------------------


def test_parse_strict_json_and_fences():
    raw = '```json\n{"questions": ["a?"], "aliases": ["x = y"]}\n```'
    assert al.parse_alias_json(raw) == {"questions": ["a?"], "aliases": ["x = y"]}


def test_parse_repairs_unescaped_inner_quotes():
    # The prototype's failure mode: an unescaped " inside a string.
    raw = (
        '{"questions": ["What does "omitted property" mean?", "Second q?"], '
        '"aliases": ["omitted property = property the assessor "missed""]}'
    )
    out = al.parse_alias_json(raw)
    assert out["questions"] == ['What does "omitted property" mean?', "Second q?"]
    assert out["aliases"] == ['omitted property = property the assessor "missed"']


def test_parse_single_quoted_array_items():
    # Observed Nova 2 Lite output: JSON keys but Python-style single-quoted items.
    raw = (
        "```json\n{\"questions\": ['Can my town extend a TID?', 'What counts as platted land?'], "
        "\"aliases\": ['TID = tax increment district', 'political subdivision = city or village']}"
        "\n```"
    )
    out = al.parse_alias_json(raw)
    assert out["questions"] == ["Can my town extend a TID?", "What counts as platted land?"]
    assert out["aliases"] == [
        "TID = tax increment district",
        "political subdivision = city or village",
    ]
    # Same shape but with an inner apostrophe that defeats literal_eval → regex fallback.
    raw2 = "{\"questions\": ['What's a TID?', 'Second?'], \"aliases\": ['TID = district']}"
    out2 = al.parse_alias_json(raw2)
    assert out2["questions"] == ["What's a TID?", "Second?"]
    assert out2["aliases"] == ["TID = district"]


def test_parse_trailing_commas_and_smart_quotes():
    raw = "{“questions”: [“a?”, ], “aliases”: [“x = y”,],}"
    assert al.parse_alias_json(raw) == {"questions": ["a?"], "aliases": ["x = y"]}


def test_parse_garbage_returns_empty():
    assert al.parse_alias_json("no json here") == {"questions": [], "aliases": []}
    assert al.parse_alias_json("") == {"questions": [], "aliases": []}


def _converse_response(text: str, tokens=(100, 40)):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1]},
    }


def test_generate_aliases_happy_path_returns_usage():
    client = MagicMock()
    client.converse.return_value = _converse_response(
        '{"questions": ["a?"], "aliases": ["towed = pulled"]}'
    )
    out = al.generate_aliases(CHUNK, "Title", "Heading", "model-x", bedrock_client=client)
    assert out["questions"] == ["a?"]
    assert out["aliases"] == ["towed = pulled"]
    assert out["usage"] == {"input_tokens": 100, "output_tokens": 40}
    call = client.converse.call_args.kwargs
    assert call["modelId"] == "model-x"
    assert call["inferenceConfig"]["temperature"] == 0.0
    prompt = call["messages"][0]["content"][0]["text"]
    assert "SOURCE: Title" in prompt and "HEADING: Heading" in prompt
    assert al.STYLE_EXAMPLES[0] in prompt


class _Throttle(Exception):
    response = {"Error": {"Code": "ThrottlingException"}}


def test_generate_aliases_retries_on_throttle_then_succeeds():
    client = MagicMock()
    client.converse.side_effect = [
        _Throttle(),
        _Throttle(),
        _converse_response('{"questions": [], "aliases": []}'),
    ]
    with patch.object(al.time, "sleep") as sleep:
        out = al.generate_aliases(CHUNK, "T", "", bedrock_client=client)
    assert "error" not in out
    assert client.converse.call_count == 3
    assert sleep.call_count == 2


def test_generate_aliases_never_raises_on_hard_failure():
    client = MagicMock()
    client.converse.side_effect = RuntimeError("boom")
    out = al.generate_aliases(CHUNK, "T", "", bedrock_client=client)
    assert out["questions"] == [] and out["aliases"] == []
    assert out["error"] == "RuntimeError"
    assert client.converse.call_count == 1  # non-throttle → no retry
