"""Tests for Path A thin-stub case-law extraction.

Case-law documents are now stored as thin citation markers: no chunks, no
embeddings, no summary text. The annotation content lives on the citing
statute's chunks (which already include it inline since Wisconsin Statutes
Annotated prints annotations directly under statute sections).

These tests validate:
  - No chunks produced (Path A removes case-law from the vector index)
  - No embeddings, no topics, no summary populated
  - Title selection prefers metadata case_name > citation > doc_id
  - statute_refs normalize across Wisconsin's three naming schemes
  - No Bedrock calls made (thin stubs never need the LLM)

Importing `scripts.graphrag.extract` transitively imports
`pdf_chunking.pdfChunker`, which makes a live AWS head_bucket call at module
load time. We stub out the pdf_chunking import before loading extract so
the tests run without AWS credentials.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Pre-register a fake pdf_chunking.pdfChunker module so extract.py's import
# succeeds without loading the real one (which calls s3.head_bucket on import).
_fake_module = MagicMock()
_fake_module.process_pdf_from_s3 = MagicMock()
sys.modules.setdefault("pdf_chunking", MagicMock())
sys.modules.setdefault("pdf_chunking.pdfChunker", _fake_module)

from scripts.graphrag import extract  # noqa: E402


def _run(metadata: dict, doc: dict | None = None):
    """Invoke process_case_law_document with mocked boto clients."""
    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = AssertionError(
        "Path A must never call Bedrock when building a case-law stub"
    )
    doc = doc or {
        "doc_id": "case-law-kollasch-v-adamany",
        "key": "raw/case-law-kollasch-v-adamany/case-law-kollasch-v-adamany.txt",
        "size": 9000,
    }
    config = {"bedrock_llm_model": "test-model"}
    with patch.object(extract, "s3", mock_s3), patch.object(extract, "bedrock", mock_bedrock):
        return extract.process_case_law_document(doc, "bucket", metadata, config), mock_bedrock


def test_thin_stub_has_no_chunks_or_summary() -> None:
    metadata = {
        "doc_type": "case_law",
        "citation": "104 Wis. 2d 552",
        "case_name": "Kollasch v. Adamany",
        "source_url": "https://www.courtlistener.com/opinion/2144662/",
        "citing_statutes": '[{"file": "77 Document.pdf", "pages": [31]}]',
    }
    result, _ = _run(metadata)

    assert result["chunks"] == []
    assert result["summary"] == ""
    assert result["topics"] == []


def test_title_prefers_metadata_case_name() -> None:
    metadata = {
        "citation": "104 Wis. 2d 552",
        "case_name": "Kollasch v. Adamany",
        "citing_statutes": "",
    }
    result, _ = _run(metadata)
    assert result["title"] == "Kollasch v. Adamany, 104 Wis. 2d 552"


def test_title_falls_back_to_citation() -> None:
    metadata = {
        "citation": "999 Wis. 2d 999",
        "citing_statutes": "",
    }
    result, _ = _run(metadata)
    assert result["title"] == "999 Wis. 2d 999"


def test_title_falls_back_to_doc_id_when_nothing_else() -> None:
    metadata = {"citing_statutes": ""}
    doc = {
        "doc_id": "case-law-unknown-case",
        "key": "raw/case-law-unknown-case/case-law-unknown-case.json",
        "size": 0,
    }
    result, _ = _run(metadata, doc)
    assert result["title"] == "case-law-unknown-case"


def test_statute_refs_normalize_filename_schemes() -> None:
    """Wisconsin uses three naming schemes; all must yield clean chapter numbers."""
    metadata = {
        "citation": "test",
        "citing_statutes": (
            '[{"file": "70.pdf", "pages": [1]}, '
            '{"file": "706 Document.pdf", "pages": [1]}, '
            '{"file": "Document 76.pdf", "pages": [1]}]'
        ),
    }
    result, _ = _run(metadata)
    assert result["statute_refs"] == ["70", "706", "76"]


def test_statute_refs_drops_unparseable_filenames() -> None:
    metadata = {
        "citation": "test",
        "citing_statutes": '[{"file": "Spring Quarter Feedback.csv", "pages": [1]}]',
    }
    result, _ = _run(metadata)
    assert result["statute_refs"] == []


def test_no_bedrock_calls_made() -> None:
    """Thin stubs never need LLM fallback — verifies the side_effect assert fires if called."""
    metadata = {
        "citation": "104 Wis. 2d 552",
        "case_name": "Kollasch v. Adamany",
        "citing_statutes": "",
    }
    _, mock_bedrock = _run(metadata)
    mock_bedrock.converse.assert_not_called()


def test_result_shape_matches_load_contract() -> None:
    """load.py's phase_2_document_nodes needs specific keys to exist."""
    metadata = {
        "doc_type": "case_law",
        "citation": "104 Wis. 2d 552",
        "case_name": "Kollasch v. Adamany",
        "source_url": "https://www.courtlistener.com/opinion/2144662/",
        "authority_level": "3",
        "framework_id": "FW-CASE-LAW",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    result, _ = _run(metadata)

    # Fields load.py expects to exist on every document record
    required = {
        "doc_id", "s3_key", "doc_type", "framework_id", "authority_level",
        "title", "summary", "statute_refs", "admin_rule_refs",
        "implements_refs", "topics", "source_url", "chunks",
    }
    assert required.issubset(result.keys())
    assert result["doc_type"] == "case_law"
    assert result["framework_id"] == "FW-CASE-LAW"
    assert result["authority_level"] == 3
    assert result["source_url"].startswith("https://")


def test_statute_file_to_chapter_helper() -> None:
    assert extract._statute_file_to_chapter("70.pdf") == "70"
    assert extract._statute_file_to_chapter("706 Document.pdf") == "706"
    assert extract._statute_file_to_chapter("Document 76.pdf") == "76"
    assert extract._statute_file_to_chapter("random.csv") == ""
    assert extract._statute_file_to_chapter("") == ""
