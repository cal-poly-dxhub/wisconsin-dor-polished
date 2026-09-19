"""phase_b.statute_section_pages: chapter -> {section -> first page} index."""

from unittest.mock import MagicMock

import loop.phase_b as phase_b


def _client(sections_by_doc):
    client = MagicMock()
    client.list_document_sections.side_effect = lambda doc_id: sections_by_doc.get(doc_id, [])
    return client


def setup_function():
    phase_b._SECTIONS_CACHE.clear()


def test_index_covers_cited_and_referenced_known_chapters():
    client = _client(
        {
            "statutes-70": [{"heading": "70.47 Board of review proceedings.", "first_page": 38}],
            "statutes-74": [
                {"heading": "74.35 Recovery of unlawful taxes.", "first_page": 8},
                {"heading": "74.37 Claim on excessive assessment.", "first_page": 9},
                {
                    "heading": "74.37 (3) (a) cross reference inside another section",
                    "first_page": 40,
                },
            ],
        }
    )
    chunks = [
        {
            "doc_id": "gov_publications-pb056",
            "text": "See s. 74.37 for excessive assessment claims.",
        }
    ]
    idx = phase_b.statute_section_pages(chunks, {"statutes-70"}, client)
    assert idx == {"70": {"70.47": 38}, "74": {"74.35": 8, "74.37": 9}}


def test_out_of_corpus_chapter_and_decimal_noise_are_ignored():
    client = _client({})
    chunks = [{"doc_id": "x", "text": "340.01 applies; the parcel is 2.5 acres worth $1.50."}]
    assert phase_b.statute_section_pages(chunks, set(), client) == {}
    client.list_document_sections.assert_not_called()


def test_plan_text_counts_as_a_reference():
    client = _client(
        {"statutes-74": [{"heading": "74.37 Claim on excessive assessment.", "first_page": 9}]}
    )
    idx = phase_b.statute_section_pages([], set(), client, answer_plan="Option C: s. 74.37 claim")
    assert idx == {"74": {"74.37": 9}}


def test_section_listing_is_cached_per_container():
    client = _client({"statutes-74": [{"heading": "74.37 X.", "first_page": 9}]})
    chunks = [{"doc_id": "x", "text": "74.37"}]
    phase_b.statute_section_pages(chunks, set(), client)
    phase_b.statute_section_pages(chunks, set(), client)
    assert client.list_document_sections.call_count == 1


def test_no_client_means_empty_index():
    assert phase_b.statute_section_pages([{"doc_id": "x", "text": "74.37"}], set(), None) == {}
