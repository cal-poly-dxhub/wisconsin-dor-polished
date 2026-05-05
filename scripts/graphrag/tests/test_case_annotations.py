"""Tests for case-annotation extraction from Wisconsin Statutes PDFs.

The fast tests (text-only) don't touch disk — they exercise the parsing logic
against synthetic inputs that reproduce the Wisconsin Statutes annotation
format. The slow tests (PDF-based) are gated behind availability of the real
statute PDFs in docs/state-laws/ and are skipped when those aren't present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.graphrag.case_annotations import (
    DEFAULT_MAX_CHARS,
    MIN_ANNOTATION_CHARS,
    extract_annotation_from_pdf,
    extract_annotation_from_text,
    extract_case_name,
    gather_case_annotations,
)


# ---------------------------------------------------------------------------
# Text-only parsing tests (no PDFs required)
# ---------------------------------------------------------------------------

ANNOTATION_WITH_DOCKET_BOUNDARY = """\
A prior annotation ends here. State v. Tappa, 127 Wis. 2d 155, 378 N.W.2d
883 (1985), 84-1234.
A violation of sub. (1) (d) does not require proof that the accused personally
received property. State v. O’Neil, 141 Wis. 2d 535, 416 N.W.2d 77 (Ct. App.
1987).
"""


def test_extract_uses_docket_boundary() -> None:
    ann = extract_annotation_from_text(ANNOTATION_WITH_DOCKET_BOUNDARY, "416 N.W.2d 77")
    assert ann is not None
    assert ann.startswith("A violation of sub. (1) (d)")
    assert "State v. Tappa" not in ann
    assert "O’Neil" in ann


ANNOTATION_WITH_YEAR_PAREN_BOUNDARY = """\
The prior annotation says something. Earlier Case v. Other, 100 Wis. 2d 1,
200 N.W.2d 2 (Ct. App. 1984).
The current annotation begins here with a substantive sentence about the
holding. Nestle USA, Inc. v. DOR, 2009 WI App 159, 322 Wis. 2d 156.
"""


def test_extract_uses_year_paren_boundary() -> None:
    ann = extract_annotation_from_text(
        ANNOTATION_WITH_YEAR_PAREN_BOUNDARY, "2009 WI App 159"
    )
    assert ann is not None
    assert ann.startswith("The current annotation begins here")
    assert "Earlier Case" not in ann


ANNOTATION_AFTER_HISTORY_BLOCK = """\
(1) Body of the statute section.
History: 1983 a. 405; 2009 a. 2.
A seller is not relieved of liability if the purchaser’s certificate on its
face fails to state a legal basis for exempting the sale. DOR v. Moebius
Printing Co., 89 Wis. 2d 610, 279 N.W.2d 213 (1979).
"""


def test_extract_uses_history_boundary() -> None:
    """History: blocks separate statute body from annotations."""
    ann = extract_annotation_from_text(ANNOTATION_AFTER_HISTORY_BLOCK, "89 Wis. 2d 610")
    assert ann is not None
    assert ann.startswith("A seller is not relieved of liability")
    assert "History:" not in ann
    assert "1983 a. 405" not in ann


def test_citation_not_present_returns_none() -> None:
    assert extract_annotation_from_text("Some unrelated text.", "999 Wis. 2d 1") is None


def test_extract_case_name_simple() -> None:
    text = (
        "The holding was important. State v. Smith, 127 Wis. 2d 155, "
        "378 N.W.2d 883 (1985)."
    )
    assert extract_case_name(text, "127 Wis. 2d 155") == "State v. Smith"


def test_extract_case_name_multi_word() -> None:
    text = (
        "A comprehensive plan prohibition is permitted. Wisconsin Realtors "
        "Ass’n v. Town of West Point, 2008 WI App 40."
    )
    assert (
        extract_case_name(text, "2008 WI App 40")
        == "Wisconsin Realtors Ass’n v. Town of West Point"
    )


def test_extract_case_name_missing_returns_none() -> None:
    assert extract_case_name("just some text, no case here, 123 Wis. 2d 1", "123 Wis. 2d 1") is None


def test_hyphenated_line_break_rejoined() -> None:
    """PyMuPDF splits words across hyphens at line breaks; we must rejoin."""
    raw = (
        "The principle is that an arm's-length sale of compa-\nrable\n"
        "properties establishes value. Test v. Other, 100 Wis. 2d 1 (1984)."
    )
    ann = extract_annotation_from_text(raw, "100 Wis. 2d 1")
    assert ann is not None
    assert "comparable" in ann
    assert "compa- rable" not in ann


def test_short_annotation_not_filtered_at_text_level() -> None:
    """Text-level extraction returns whatever is found; filtering happens at PDF level."""
    raw = "Affirmed. 2011 WI 4, 331 Wis. 2d 256."
    ann = extract_annotation_from_text(raw, "2011 WI 4")
    assert ann is not None
    assert ann.startswith("Affirmed")


def test_leading_breadcrumb_stripped() -> None:
    """Section headings and chapter breadcrumbs are stripped from annotation start."""
    raw = (
        "70.11 Exempt property. A benevolent association need not provide free "
        "services. Friendship Village v. City, 181 Wis. 2d 207."
    )
    ann = extract_annotation_from_text(raw, "181 Wis. 2d 207")
    assert ann is not None
    assert not ann.startswith("70.11")
    assert ann.startswith("A benevolent association")


# ---------------------------------------------------------------------------
# PDF-integration tests (skipped when docs/state-laws/ is missing)
# ---------------------------------------------------------------------------

STATE_LAWS_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "state-laws"


def _has_real_pdfs() -> bool:
    return STATE_LAWS_DIR.exists() and (STATE_LAWS_DIR / "70.pdf").exists()


@pytest.mark.skipif(not _has_real_pdfs(), reason="statute PDFs not available")
def test_real_pdf_nestle_v_dor() -> None:
    """Known good case: Nestle USA, Inc. v. DOR in 70.pdf page 25."""
    ann = extract_annotation_from_pdf(
        STATE_LAWS_DIR / "70.pdf", "2009 WI App 159", [25]
    )
    assert ann is not None
    assert len(ann) > 200
    assert "Nestle" in ann
    assert "History:" not in ann
    # Must include substantive discussion, not just the case name
    assert "tier" in ann.lower() or "market" in ann.lower() or "value" in ann.lower()


@pytest.mark.skipif(not _has_real_pdfs(), reason="statute PDFs not available")
def test_real_pdf_page_break_case() -> None:
    """Annotations that start on the previous page are handled via retry."""
    # "138 S. Ct. 2080" is on 77 Document.pdf p26 but starts on p25.
    pdf = STATE_LAWS_DIR / "77 Document.pdf"
    if not pdf.exists():
        pytest.skip("77 Document.pdf not available")
    ann = extract_annotation_from_pdf(pdf, "138 S. Ct. 2080", [26])
    # With page-break retry, we get a substantive annotation.
    # Without it, we'd get just "Wayfair, Inc., 585 U.S. 162,"
    assert ann is not None
    assert len(ann) > MIN_ANNOTATION_CHARS


@pytest.mark.skipif(not _has_real_pdfs(), reason="statute PDFs not available")
def test_gather_case_annotations_real_pdf() -> None:
    citing_statutes = [{"file": "70.pdf", "pages": [25]}]
    results = gather_case_annotations(
        "2009 WI App 159", citing_statutes, STATE_LAWS_DIR
    )
    assert len(results) == 1
    result = results[0]
    assert result["source_file"] == "70.pdf"
    assert result["pages"] == [25]
    assert result["case_name"] == "Nestle USA, Inc. v. DOR"
    assert "tier" in result["text"].lower() or "market" in result["text"].lower()


@pytest.mark.skipif(not _has_real_pdfs(), reason="statute PDFs not available")
def test_gather_case_annotations_missing_pdf_silently_skipped() -> None:
    citing_statutes = [
        {"file": "70.pdf", "pages": [25]},
        {"file": "nonexistent.pdf", "pages": [1]},
    ]
    results = gather_case_annotations(
        "2009 WI App 159", citing_statutes, STATE_LAWS_DIR
    )
    assert len(results) == 1
    assert results[0]["source_file"] == "70.pdf"


# ---------------------------------------------------------------------------
# Default constant sanity checks
# ---------------------------------------------------------------------------

def test_default_max_chars_reasonable() -> None:
    assert DEFAULT_MAX_CHARS > 500
    assert DEFAULT_MAX_CHARS < 5000


def test_min_annotation_chars_reasonable() -> None:
    assert MIN_ANNOTATION_CHARS > 20
    assert MIN_ANNOTATION_CHARS < 200
