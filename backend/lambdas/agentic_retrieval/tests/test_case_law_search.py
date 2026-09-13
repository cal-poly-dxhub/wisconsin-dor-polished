"""find_case_law resolution: in-Lambda matcher over the cached CaseLaw index.

Covers the three match kinds (reporter | neutral | name), the neutral-cite
prefix-boundary bug ("2018 WI 45" must never hit `case-law-2018-wi-4`), the
5-result cap, ranking, statute scoping, and the per-container cache.
"""

import case_law
import pytest
from case_law import (
    FIND_CASE_LAW_MAX_RESULTS,
    get_case_law_index,
    parse_case_search,
    reset_case_law_index_cache,
    search_case_law,
)


def _node(node_id: str, title: str, citation: str) -> dict:
    return {
        "id": node_id,
        "title": title,
        "citation": citation,
        "doc_type": "case_law",
        "authority_level": 3,
        "source_url": f"https://example.test/{node_id}",
        "labels": ["CaseLaw"],
    }


# Mirrors real graph rows (post 2026-09-11 reload): most titles are
# "Case Name, N Wis. 2d M"; Scholar-path nodes are still bare cites.
NODES = [
    _node(
        "case-law-381-wis-2d-311",
        "Donald J Thoma v. Village of Slinger, 381 Wis. 2d 311",
        "381 Wis. 2d 311",
    ),
    _node(
        "case-law-401-wis-2d-27",
        "Nudo Holdings LLC v. Board of Review for the City of Kenosha, 401 Wis. 2d 27",
        "401 Wis. 2d 27",
    ),
    _node(
        "case-law-407-wis-2d-678",
        "Thomas G Miller v. Zoning Board of Appeals of the Village of Lyndon, 407 Wis. 2d 678",
        "407 Wis. 2d 678",
    ),
    _node(
        "case-law-45-wis-2d-683",
        "State ex rel Markarian v. City of Cudahy, 45 Wis. 2d 683",
        "45 Wis. 2d 683",
    ),
    _node(
        "case-law-212-wis-2d-332",
        "Town of Delafield v. Sharpley, 212 Wis. 2d 332",
        "212 Wis. 2d 332",
    ),
    _node("case-law-2018-wi-4", "2018 WI 4", "2018 WI 4"),
    _node("case-law-2018-wi-11", "2018 WI 11", "2018 WI 11"),
    _node("case-law-2018-wi-app-4", "2018 WI App 4", "2018 WI App 4"),
    _node(
        "case-law-2022-wi-57",
        "Friends of Frame Park Ua v. City of Waukesha, 2022 WI 57",
        "2022 WI 57",
    ),
    _node("case-law-2022-wi-app-15", "2022 WI App 15", "2022 WI App 15"),
    _node(
        "case-law-985-n-w-2d-69",
        "Lowes Home Centers v. City of Delavan, 985 N.W.2d 69",
        "985 N.W.2d 69",
    ),
]


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_case_law_index_cache()
    yield
    reset_case_law_index_cache()


# --- parsing ---------------------------------------------------------------


def test_parse_splits_reporter_neutral_and_name():
    parsed = parse_case_search("Thoma v. Village of Slinger, 2018 WI 45, 381 Wis. 2d 311")
    assert parsed["reporter"] == ["case-law-381-wis-2d-311"]
    assert parsed["neutral"] == ["case-law-2018-wi-45"]
    assert parsed["name_tokens"] == ["thoma", "slinger"]


def test_parse_drops_party_type_words_and_numbers():
    parsed = parse_case_search("Nudo Holdings LLC v. City of Kenosha")
    assert parsed["name_tokens"] == ["nudo", "holdings", "kenosha"]
    parsed = parse_case_search("Ogden Family Trust Delafield 2019")
    assert parsed["name_tokens"] == ["ogden", "family", "trust", "delafield"]
    assert parsed["neutral"] == [] and parsed["reporter"] == []


def test_parse_neutral_app_and_nw_reporter():
    assert parse_case_search("2025 WI App 43")["neutral"] == ["case-law-2025-wi-app-43"]
    assert parse_case_search("985 N.W.2d 69")["reporter"] == ["case-law-985-n-w-2d-69"]
    assert parse_case_search("173 N.W. 2d 627")["reporter"] == ["case-law-173-n-w-2d-627"]


# --- reporter ----------------------------------------------------------------


def test_reporter_cite_exact_hit():
    hits = search_case_law("381 Wis. 2d 311", NODES)
    assert [h["id"] for h in hits] == ["case-law-381-wis-2d-311"]
    assert hits[0]["match_kind"] == "reporter"
    assert hits[0]["match_score"] == 1.0


def test_reporter_cite_variant_spacing():
    hits = search_case_law("381 Wis.2d 311", NODES)
    assert [h["id"] for h in hits] == ["case-law-381-wis-2d-311"]


# --- neutral: strict boundary --------------------------------------------------


def test_neutral_cite_missing_returns_zero_not_prefix_garbage():
    """The original bug: '2018 WI 45' returned 2018-wi-4 and 2018-wi-11."""
    assert search_case_law("2018 WI 45", NODES) == []
    assert search_case_law("2022 WI 17", NODES) == []
    assert search_case_law("2019 WI 23", NODES) == []


def test_neutral_cite_exact_hit_when_present():
    nodes = NODES + [
        _node("case-law-2018-wi-45", "Thoma v. Village of Slinger, 2018 WI 45", "2018 WI 45")
    ]
    hits = search_case_law("2018 WI 45", nodes)
    assert [h["id"] for h in hits] == ["case-law-2018-wi-45"]
    assert hits[0]["match_kind"] == "neutral"


def test_neutral_cite_short_number_is_not_prefix_of_longer():
    hits = search_case_law("2018 WI 4", NODES)
    assert [h["id"] for h in hits] == ["case-law-2018-wi-4"]


def test_neutral_wi_vs_wi_app_are_distinct():
    assert [h["id"] for h in search_case_law("2018 WI App 4", NODES)] == ["case-law-2018-wi-app-4"]
    assert [h["id"] for h in search_case_law("2018 WI 4", NODES)] == ["case-law-2018-wi-4"]


def test_neutral_cite_matches_titled_node():
    hits = search_case_law("2022 WI 57", NODES)
    assert [h["id"] for h in hits] == ["case-law-2022-wi-57"]
    assert hits[0]["match_kind"] == "neutral"


# --- name --------------------------------------------------------------------


def test_name_full_style_match_ignores_leading_first_name():
    """Title carries 'Donald J' the query lacks: still the single hit, scored as partial."""
    hits = search_case_law("Thoma v. Village of Slinger", NODES)
    assert [h["id"] for h in hits] == ["case-law-381-wis-2d-311"]
    assert hits[0]["match_kind"] == "name"
    assert 0.8 < hits[0]["match_score"] < 1.0


def test_name_ignores_llc_city_of_board_of_review():
    hits = search_case_law("Nudo Holdings LLC v. City of Kenosha", NODES)
    assert [h["id"] for h in hits] == ["case-law-401-wis-2d-27"]
    assert hits[0]["match_score"] == 1.0


def test_name_single_distinctive_token():
    hits = search_case_law("Nudo Holdings", NODES)
    assert [h["id"] for h in hits] == ["case-law-401-wis-2d-27"]
    assert 0 < hits[0]["match_score"] < 1.0


def test_name_token_equality_not_substring():
    """'Thoma' must not hit 'Thomas G Miller' (the CONTAINS failure mode)."""
    hits = search_case_law("Thoma", NODES)
    assert [h["id"] for h in hits] == ["case-law-381-wis-2d-311"]


def test_name_not_in_graph_returns_zero():
    assert search_case_law("Ogden Family Trust Delafield 2019", NODES) == []
    assert search_case_law("Ogden Family Trust", NODES) == []


def test_name_state_ex_rel_prefix_optional():
    hits = search_case_law("Markarian v. City of Cudahy", NODES)
    assert [h["id"] for h in hits] == ["case-law-45-wis-2d-683"]
    assert hits[0]["match_score"] == 1.0


def test_name_single_generic_token_alone_does_not_match():
    # "Delafield" (< 2 tokens, but >= 4 chars) matches on the long-token rule.
    assert [h["id"] for h in search_case_law("Delafield", NODES)] == ["case-law-212-wis-2d-332"]
    # Party-type words alone carry nothing.
    assert search_case_law("City of", NODES) == []
    assert search_case_law("", NODES) == []


# --- ranking, cap, dedup ----------------------------------------------------------


def test_cite_hit_outranks_name_hit_and_dedups():
    hits = search_case_law("Thoma v. Village of Slinger, 381 Wis. 2d 311", NODES)
    assert [h["id"] for h in hits] == ["case-law-381-wis-2d-311"]
    assert hits[0]["match_kind"] == "reporter"


def test_exact_name_outranks_partial_name():
    nodes = NODES + [
        _node("case-law-1-wis-2d-1", "Thoma v. Slinger Board of Review, 1 Wis. 2d 1", "1 Wis. 2d 1")
    ]
    hits = search_case_law("Thoma v. Slinger", nodes)
    # {thoma, slinger} == {thoma, slinger} is exact; {donald, thoma, slinger} is partial.
    assert [h["id"] for h in hits] == ["case-law-1-wis-2d-1", "case-law-381-wis-2d-311"]
    assert hits[0]["match_score"] == 1.0
    assert hits[1]["match_score"] < 1.0


def test_typo_in_source_title_still_resolves_on_remaining_tokens():
    """Real graph row: CourtListener spells Ogden as 'Odgen'. Two distinctive
    tokens (family, trust) still resolve it, at a visibly low score."""
    nodes = NODES + [
        _node(
            "case-law-385-wis-2d-676",
            "State ex rel Peter Odgen Family Trust of 2008 v. Bd of Review for the "
            "Town of Delafield, 385 Wis. 2d 676",
            "385 Wis. 2d 676",
        )
    ]
    hits = search_case_law("Ogden Family Trust Delafield 2019", nodes)
    assert [h["id"] for h in hits] == ["case-law-385-wis-2d-676"]
    assert hits[0]["match_score"] < 0.75


def test_result_cap():
    nodes = [
        _node(
            f"case-law-{i}-wis-2d-1",
            f"Acme Widgets v. Town of Foo {i}, {i} Wis. 2d 1",
            f"{i} Wis. 2d 1",
        )
        for i in range(1, 12)
    ]
    hits = search_case_law("Acme Widgets", nodes)
    assert len(hits) == FIND_CASE_LAW_MAX_RESULTS == 5
    assert len(search_case_law("Acme Widgets", nodes, limit=2)) == 2


def test_allowed_ids_scoping():
    allowed = {"case-law-407-wis-2d-678"}
    assert search_case_law("Thoma", NODES, allowed_ids=allowed) == []
    assert search_case_law("Thoma", NODES, allowed_ids=set()) == []
    assert [
        h["id"] for h in search_case_law("Thoma", NODES, allowed_ids={"case-law-381-wis-2d-311"})
    ] == ["case-law-381-wis-2d-311"]


def test_result_keeps_node_fields():
    hit = search_case_law("381 Wis. 2d 311", NODES)[0]
    assert hit["source_url"] == "https://example.test/case-law-381-wis-2d-311"
    assert hit["labels"] == ["CaseLaw"]
    assert hit["authority_level"] == 3


# --- cache -----------------------------------------------------------------------


def test_index_cached_per_container_with_ttl():
    calls = []

    def loader():
        calls.append(1)
        return NODES

    assert get_case_law_index(loader, now=0.0) is NODES
    assert get_case_law_index(loader, now=10.0) is NODES
    assert len(calls) == 1
    get_case_law_index(loader, now=case_law.CASE_INDEX_TTL_SECONDS + 1.0)
    assert len(calls) == 2


def test_index_serves_stale_copy_when_refresh_fails():
    get_case_law_index(lambda: NODES, now=0.0)

    def boom():
        raise RuntimeError("neptune down")

    assert get_case_law_index(boom, now=case_law.CASE_INDEX_TTL_SECONDS + 1.0) is NODES


def test_index_raises_when_first_load_fails():
    def boom():
        raise RuntimeError("neptune down")

    with pytest.raises(RuntimeError):
        get_case_law_index(boom, now=0.0)
