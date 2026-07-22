"""Regression tests for the selective case-law chunking experiment."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ingestion.chunking import case_law as selective

SAMPLES = Path(__file__).resolve().parent.parent / "chunking_test" / "samples"

EXPECTED_TRANSITIONS = {
    "101-wis-2d-472.txt": "sustained legal-density",
    "103-wis-2d-368.txt": "substantive heading: WARRANTLESS SEARCH",
    "104-wis-2d-26.txt": "issue-framing transition",
    "104-wis-2d-552.txt": "sustained legal-density",
    "105-wis-2d-690.txt": "substantive heading: CUSTOMER ORDER FORMS",
    "2000-wi-57.txt": "strong legal-density",
    "2000-wi-93.txt": "opinion roadmap to analysis",
    "2000-wi-app-133.txt": "strong legal-density",
    "2000-wi-app-138.txt": "explicit heading: Analysis",
    "2001-wi-27.txt": "strong legal-density",
}


def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(errors="replace")


@pytest.mark.parametrize(("name", "reason"), EXPECTED_TRANSITIONS.items())
def test_diverse_corpus_uses_confirmed_analysis_transition(name: str, reason: str) -> None:
    result = selective.select_and_chunk(_sample(name))

    assert not result.fallback_used
    assert reason in result.analysis_reason
    assert result.omitted_high_signal == []


@pytest.mark.parametrize("name", EXPECTED_TRANSITIONS)
def test_chunks_are_retrieval_sized_and_include_disposition(name: str) -> None:
    result = selective.select_and_chunk(_sample(name))
    sizes = [chunk.char_count for chunk in result.chunks]

    assert sizes
    assert min(sizes) >= selective.MIN_TRAILING_CHUNK
    assert max(sizes) <= selective.HARD_CAP
    assert selective._BY_COURT_RE.search(result.chunks[-1].text)


def test_corpus_materially_reduces_whole_opinion_chunks() -> None:
    baseline = 0
    selected = 0
    retained = 0
    source = 0

    for name in EXPECTED_TRANSITIONS:
        result = selective.select_and_chunk(_sample(name))
        baseline += selective._baseline_chunk_count(result.normalized_text)
        selected += len(result.chunks)
        retained += result.retained_source_chars
        source += len(result.normalized_text)

    assert selected <= baseline * 0.55
    assert retained <= source * 0.50


def test_ocr_paragraph_markers_are_normalized_and_large_case_stays_selective() -> None:
    result = selective.select_and_chunk(_sample("2000-wi-93.txt"))
    combined = "\n".join(chunk.text for chunk in result.chunks)

    assert "ś 27" not in combined
    assert "¶ 27" in combined
    assert result.retained_ratio < 0.35
    assert len(result.chunks) == 15


def test_opening_exact_holding_and_all_three_issues_survive() -> None:
    result = selective.select_and_chunk(_sample("2000-wi-app-133.txt"))
    combined = "\n".join(chunk.text for chunk in result.chunks)

    assert "We affirm that part of the circuit court's decision" in combined
    assert "This case presents three questions of law" in combined
    assert "We now turn to the second issue" in combined
    assert "what is the effect of a deed" in combined


def test_separate_dissent_and_notes_are_not_selected() -> None:
    result = selective.select_and_chunk(_sample("2001-wi-27.txt"))
    combined = "\n".join(chunk.text for chunk in result.chunks)

    assert "DAVID T. PROSSER, J. (dissenting)" not in combined
    assert "FACTS ¶ 46" not in combined
    assert "¶ 44" in combined


def test_unreliable_unstructured_text_uses_majority_fallback() -> None:
    text = (
        "This document has no court headings or reliable legal transition. "
        "It contains a long narrative about events and people. " * 80
    )
    result = selective.select_and_chunk(text)

    assert result.fallback_used
    assert result.analysis_reason == "no reliable analysis transition"
    assert result.regions[0].role == "fallback_majority"
