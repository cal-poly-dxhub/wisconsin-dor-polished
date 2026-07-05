"""Unit tests for WPAM cross-edition chunk deduplication."""

from wpam_dedup import dedupe_wpam_chunks


def _wpam_chunk(year: int, heading: str, chunk_id: str = None, doc_id: str = None) -> dict:
    """Helper: build a WPAM chunk dict with the fields dedup needs."""
    return {
        "chunk_id": chunk_id or f"wpam-{year}-c{heading[:5]}",
        "doc_id": doc_id or f"wpam-wisconsin-property-assessment-manual-{year}",
        "framework_id": "FW-WPAM",
        "edition_year": year,
        "heading": heading,
        "text": f"text from {year} {heading}",
    }


def test_collapses_same_heading_across_editions_to_max_year():
    chunks = [
        _wpam_chunk(2020, "Manufactured Homes"),
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
        _wpam_chunk(2022, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025


def test_target_year_overrides_max_year():
    chunks = [
        _wpam_chunk(2020, "Manufactured Homes"),
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2018)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2018


def test_target_year_not_present_falls_back_to_max():
    """User asked about 2017 but only 2018-2025 exist — give them the latest."""
    chunks = [
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2017)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025


def test_singleton_from_old_edition_dropped_when_no_target_year():
    """Content unique to an old edition is dropped by the edition filter
    when no target_year is set — users expect current guidance only."""
    chunks = [
        _wpam_chunk(2018, "Deprecated Topic Removed In 2019"),
        _wpam_chunk(2025, "Modern Section"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["heading"] == "Modern Section"


def test_singleton_from_old_edition_survives_with_target_year():
    """When target_year is set, the edition filter is skipped — old singletons
    survive so the user can see what that edition contained."""
    chunks = [
        _wpam_chunk(2018, "Deprecated Topic Removed In 2019"),
        _wpam_chunk(2025, "Modern Section"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2018)
    assert len(result) == 2
    assert {c["heading"] for c in result} == {
        "Deprecated Topic Removed In 2019",
        "Modern Section",
    }


def test_current_wpam_year_overrides_max_from_results():
    """When current_wpam_year is provided, the edition filter uses it
    instead of max(edition_year) from the result set. This handles the case
    where Neptune returns only old-edition chunks."""
    chunks = [
        _wpam_chunk(2018, "Section A"),
        _wpam_chunk(2022, "Section B"),
    ]
    # Without current_wpam_year, max from results is 2022
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2022

    # With current_wpam_year=2026, both are old and get dropped
    result = dedupe_wpam_chunks(chunks, target_year=None, current_wpam_year=2026)
    assert len(result) == 0


def test_current_wpam_year_keeps_matching_chunks():
    """Chunks matching the current_wpam_year survive the filter."""
    chunks = [
        _wpam_chunk(2018, "Old Section"),
        _wpam_chunk(2026, "Current Section"),
        _wpam_chunk(2022, "Middle Section"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None, current_wpam_year=2026)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2026


def test_target_year_allows_only_target_and_current():
    """When target_year is set, allow target + current but drop everything else."""
    chunks = [
        _wpam_chunk(2018, "Section from 2018"),
        _wpam_chunk(2020, "Section from 2020"),
        _wpam_chunk(2022, "Section from 2022"),
        _wpam_chunk(2026, "Section from 2026"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2018, current_wpam_year=2026)
    years = {c["edition_year"] for c in result}
    assert years == {2018, 2026}
    assert len(result) == 2


def test_non_wpam_chunks_pass_through_unchanged():
    chunks = [
        {
            "chunk_id": "stat-1",
            "framework_id": "FW-STATUTES",
            "heading": "70.32",
            "doc_id": "statutes-70-32",
            "text": "statute text",
        },
        _wpam_chunk(2018, "WPAM section"),
        _wpam_chunk(2025, "WPAM section"),
        {
            "chunk_id": "stat-2",
            "framework_id": "FW-STATUTES",
            "heading": "70.33",
            "doc_id": "statutes-70-33",
            "text": "another statute",
        },
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 3
    framework_ids = [c["framework_id"] for c in result]
    assert framework_ids.count("FW-STATUTES") == 2
    assert framework_ids.count("FW-WPAM") == 1


def test_wpam_chunk_missing_edition_year_passes_through():
    """An old WPAM chunk loaded before this feature has no edition_year.
    It must NOT be deduped against newer chunks (we can't tell which is newer)."""
    chunks = [
        {
            "chunk_id": "old",
            "doc_id": "wpam-...",
            "framework_id": "FW-WPAM",
            "heading": "Manufactured Homes",
            "text": "old text",
        },
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 2


def test_empty_input_returns_empty():
    assert dedupe_wpam_chunks([], target_year=None) == []
    assert dedupe_wpam_chunks([], target_year=2020) == []


def test_normalizes_heading_whitespace_and_case():
    """Same section, slightly different heading whitespace/case across editions."""
    chunks = [
        {
            "chunk_id": "c1",
            "doc_id": "wpam-2018",
            "framework_id": "FW-WPAM",
            "edition_year": 2018,
            "heading": "Manufactured  Homes",
            "text": "...",
        },
        {
            "chunk_id": "c2",
            "doc_id": "wpam-2025",
            "framework_id": "FW-WPAM",
            "edition_year": 2025,
            "heading": "manufactured homes",
            "text": "...",
        },
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025
