"""Tests for selective multi-chunk case-law extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.ingestion import extract

SAMPLES = Path(__file__).resolve().parent.parent / "chunking_test" / "samples"


def _run(
    metadata: dict,
    *,
    opinion_text: str | None = None,
    summary: str = "",
    doc: dict | None = None,
):
    doc = doc or {
        "doc_id": "case-law-kollasch-v-adamany",
        "key": "raw/case-law/misc/kollasch-v-adamany.txt",
        "size": 9000,
    }
    with (
        patch.object(extract, "_fetch_opinion_text", return_value=opinion_text),
        patch.object(extract, "_summarize_opinion", return_value=summary) as summarize,
    ):
        result = extract.process_case_law_document(doc, "bucket", metadata, {})
    return result, summarize


def test_missing_opinion_remains_thin_stub() -> None:
    result, summarize = _run(
        {
            "doc_type": "case_law",
            "citation": "104 Wis. 2d 552",
            "case_name": "Kollasch v. Adamany",
            "citing_statutes": "",
        }
    )

    assert result["chunks"] == []
    assert result["summary"] == ""
    summarize.assert_not_called()


def test_summary_plus_selective_chunks_have_local_citations() -> None:
    opinion = (SAMPLES / "2000-wi-app-138.txt").read_text()
    summary = "The court interpreted Wis. Stat. § 943.20 and affirmed the judgment."
    result, summarize = _run(
        {
            "doc_type": "case_law",
            "citation": "2000 WI App 138",
            "case_name": "State v. Graham",
            "source_url": "https://example.test/graham",
            "citing_statutes": "",
        },
        opinion_text=opinion,
        summary=summary,
    )

    summarize.assert_called_once_with(opinion)
    assert len(result["chunks"]) == 3  # summary + two compacted selective body chunks
    assert result["chunks"][0]["metadata"]["content_role"] == "summary_holding"
    assert result["chunks"][0]["metadata"]["statute_refs"] == ["943.20"]
    assert all(chunk["metadata"]["content_role"] for chunk in result["chunks"])
    assert any(
        "943.20" in chunk["metadata"]["statute_refs"] for chunk in result["chunks"][1:]
    )
    assert result["case_law_selection_fallback"] is False


def test_title_precedence() -> None:
    result, _ = _run(
        {
            "citation": "104 Wis. 2d 552",
            "case_name": "Kollasch v. Adamany",
            "citing_statutes": "",
        }
    )
    assert result["title"] == "Kollasch v. Adamany, 104 Wis. 2d 552"

    result, _ = _run({"citation": "999 Wis. 2d 999", "citing_statutes": ""})
    assert result["title"] == "999 Wis. 2d 999"

    result, _ = _run({"citing_statutes": ""})
    assert result["title"] == "case-law-kollasch-v-adamany"


def test_statute_refs_normalize_filename_schemes() -> None:
    result, _ = _run(
        {
            "citation": "test",
            "citing_statutes": (
                '[{"file": "70.pdf", "pages": [1]}, '
                '{"file": "706 Document.pdf", "pages": [1]}, '
                '{"file": "Document 76.pdf", "pages": [1]}]'
            ),
        }
    )
    assert result["statute_refs"] == ["70", "706", "76"]


def test_result_shape_matches_load_contract() -> None:
    result, _ = _run(
        {
            "doc_type": "case_law",
            "citation": "104 Wis. 2d 552",
            "case_name": "Kollasch v. Adamany",
            "source_url": "https://www.courtlistener.com/opinion/2144662/",
            "authority_level": "3",
            "framework_id": "FW-CASE-LAW",
            "citing_statutes": "",
        }
    )
    required = {
        "doc_id",
        "s3_key",
        "doc_type",
        "framework_id",
        "authority_level",
        "title",
        "summary",
        "statute_refs",
        "admin_rule_refs",
        "implements_refs",
        "topics",
        "source_url",
        "chunks",
    }
    assert required.issubset(result)
    assert result["doc_type"] == "case_law"
    assert result["authority_level"] == 3


def test_statute_file_to_chapter_helper() -> None:
    assert extract._statute_file_to_chapter("70.pdf") == "70"
    assert extract._statute_file_to_chapter("706 Document.pdf") == "706"
    assert extract._statute_file_to_chapter("Document 76.pdf") == "76"
    assert extract._statute_file_to_chapter("random.csv") == ""
