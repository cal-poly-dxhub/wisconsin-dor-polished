from scripts.graphrag.faq_url_map import normalize_question, build_url_map, lookup_url


def test_normalize_question_collapses_and_strips():
    assert normalize_question("  Is  THIS a Test?  ") == "is this a test"
    assert normalize_question("Already clean.") == "already clean"
    # nbsp / zero-width / bom noise collapses to a single space
    assert normalize_question("a ​b") == "a b"


def test_build_url_map_exact_match():
    records = [
        {"Q": "What is X?", "A": "X is a thing.", "source_url": "https://example.gov/x"},
    ]
    url_map = build_url_map(records)
    assert lookup_url("what is x", "anything", url_map) == "https://example.gov/x"


def test_lookup_recovers_by_answer_then_prefix():
    records = [
        {"Q": "What is the exact original question?", "A": "Unique answer body.",
         "source_url": "https://example.gov/a"},
        {"Q": "A very long question that differs only after fifty characters of text here",
         "A": "Other.", "source_url": "https://example.gov/b"},
    ]
    url_map = build_url_map(records)
    # Answer match: question text drifted but answer is identical.
    assert lookup_url("totally different wording", "Unique answer body.", url_map) == "https://example.gov/a"
    # Prefix match: first 50 normalized chars line up, tail differs.
    drifted = "A very long question that differs only after fifty CHARACTERS differ now"
    assert lookup_url(drifted, "nope", url_map) == "https://example.gov/b"


def test_lookup_orphan_returns_none():
    url_map = build_url_map([{"Q": "Known?", "A": "Yes.", "source_url": "https://example.gov/k"}])
    assert lookup_url("completely unknown question", "and answer", url_map) is None
