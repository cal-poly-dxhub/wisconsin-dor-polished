"""Tests for WPAM edition_year extraction."""

from scripts.graphrag.wpam_year import (
    extract_wpam_year_from_doc_id,
    extract_wpam_year_from_pdf_text,
)


def test_extract_year_from_simple_prefix():
    doc_id = "wpam-wisconsin-property-assessment-manual-2025"
    assert extract_wpam_year_from_doc_id(doc_id) == 2025


def test_extract_year_from_volume_prefix():
    """Vol-1-2011 has multiple digit groups; we want the LAST 4-digit group."""
    doc_id = "wpam-wisconsin-property-assessment-manual-vol-1-2011"
    assert extract_wpam_year_from_doc_id(doc_id) == 2011


def test_extract_year_returns_none_on_no_year():
    doc_id = "wpam-wisconsin-property-assessment-manual"
    assert extract_wpam_year_from_doc_id(doc_id) is None


def test_extract_year_returns_none_on_non_wpam_prefix():
    assert extract_wpam_year_from_doc_id("statutes-70-32") is None


def test_extract_year_rejects_implausible_year():
    """We only accept years in [2010, current_year+1]. 1999 is too old."""
    doc_id = "wpam-wisconsin-property-assessment-manual-1999"
    assert extract_wpam_year_from_doc_id(doc_id) is None


def test_pdf_text_extracts_explicit_year():
    text = (
        "2024 Wisconsin Property Assessment Manual\n"
        "Published by the Wisconsin Department of Revenue\n"
        "Effective for 2024 assessment year"
    )
    assert extract_wpam_year_from_pdf_text(text) == 2024


def test_pdf_text_extracts_year_from_effective_date_phrase():
    text = (
        "Wisconsin Property Assessment Manual\n"
        "effective January 2026 for use during the 2026 assessment year"
    )
    assert extract_wpam_year_from_pdf_text(text) == 2026


def test_pdf_text_returns_none_on_no_year():
    text = "Wisconsin Property Assessment Manual\nPublished by the Wisconsin Department of Revenue"
    assert extract_wpam_year_from_pdf_text(text) is None


def test_pdf_text_rejects_implausible_years():
    """A document might mention historical years (e.g. '1879 statute');
    those should not be picked up."""
    text = "Originally enacted in 1879 and amended several times since."
    assert extract_wpam_year_from_pdf_text(text) is None
