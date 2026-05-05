"""Tests for the case-law branch of extract.process_document.

Validates:
  - Substantive annotations go in as-is, no LLM call.
  - Thin annotations (< MIN_ANNOTATION_TOTAL_CHARS) trigger the LLM fallback.
  - The LLM fallback produces a chunk tagged `chunk_kind="llm_summary"` and
    uses the LLM output as the Document summary.
  - LLM failure falls back to citation-only placeholder without crashing.
  - Title selection prefers the extracted case name over the citation.
  - Opinion chunks come after annotation and llm_summary chunks.

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

# Now import extract; its boto3 clients will still be real, but individual
# tests rebind extract.s3 and extract.bedrock before invoking code paths.
from scripts.graphrag import extract  # noqa: E402


def _mock_clients(opinion_bytes: bytes = b"Opinion text.", llm_response: str | None = "Generated summary."):
    """Build mock boto3 s3 + bedrock-runtime clients for a single test."""
    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: opinion_bytes)}

    mock_bedrock = MagicMock()
    if llm_response is not None:
        mock_bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": llm_response}]}}
        }
    else:
        mock_bedrock.converse.side_effect = RuntimeError("LLM unavailable")

    return mock_s3, mock_bedrock


def _with_mocked_extract(mock_s3, mock_bedrock, callback):
    """Run `callback(extract)` with extract's s3 + bedrock clients replaced.

    We patch the module-level client attributes directly because extract.py
    creates its boto3 clients at import time — patching boto3.client is too
    late in tests that run after extract has already been imported.
    """
    with patch.object(extract, "s3", mock_s3), patch.object(extract, "bedrock", mock_bedrock):
        return callback(extract)


def test_substantive_annotation_skips_llm_fallback() -> None:
    """Nestle v. DOR has a 650-char annotation; should not trigger LLM."""
    mock_s3, mock_bedrock = _mock_clients()
    doc = {
        "doc_id": "case-law-2009-wi-app-159",
        "key": "raw/case-law-2009-wi-app-159/case-law-2009-wi-app-159.txt",
        "size": 35000,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "2009 WI App 159",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    assert mock_bedrock.converse.call_count == 0
    assert result["title"].startswith("Nestle USA")
    kinds = [c["metadata"].get("chunk_kind") for c in result["chunks"]]
    assert "llm_summary" not in kinds
    assert "annotation" in kinds


def test_thin_annotation_triggers_llm_fallback() -> None:
    """'Affirmed. 2011 WI 4,' style short stub triggers LLM."""
    mock_s3, mock_bedrock = _mock_clients(
        llm_response="This is a Supreme Court affirmance of the Court of Appeals in a property-tax assessment case."
    )
    doc = {
        "doc_id": "case-law-331-wis-2d-256",
        "key": "raw/case-law-331-wis-2d-256/case-law-331-wis-2d-256.txt",
        "size": 500,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "331 Wis. 2d 256",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    assert mock_bedrock.converse.call_count == 1
    kinds = [c["metadata"].get("chunk_kind") for c in result["chunks"]]
    assert "llm_summary" in kinds
    # Summary should come from LLM, not the tiny annotation
    assert "Supreme Court" in result["summary"]


def test_llm_fallback_failure_falls_through() -> None:
    """When Bedrock throws, we keep the thin annotation as summary, no crash."""
    mock_s3, mock_bedrock = _mock_clients(llm_response=None)  # LLM raises
    doc = {
        "doc_id": "case-law-331-wis-2d-256",
        "key": "raw/case-law-331-wis-2d-256/case-law-331-wis-2d-256.txt",
        "size": 500,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "331 Wis. 2d 256",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    # Should not crash; summary falls through to the thin annotation.
    assert result is not None
    assert result["summary"]
    kinds = [c["metadata"].get("chunk_kind") for c in result["chunks"]]
    assert "llm_summary" not in kinds  # LLM failed; no fallback chunk added


def test_case_with_no_citing_statutes_uses_citation_summary() -> None:
    """Malformed/missing citing_statutes metadata still produces a valid record."""
    mock_s3, mock_bedrock = _mock_clients(llm_response="Unknown case.")
    doc = {
        "doc_id": "case-law-orphan-case",
        "key": "raw/case-law-orphan-case/case-law-orphan-case.json",
        "size": 200,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "999 Wis. 2d 999",
        "source_url": "",
        "citing_statutes": "",  # missing
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    assert result is not None
    # With no annotation and no citing statutes, LLM still gets called (zero-char annotation)
    # but receives empty context. We just need a valid doc to emerge.
    assert result["title"] == "999 Wis. 2d 999"


def test_chunk_ordering_annotations_before_llm_before_opinions() -> None:
    """Verify annotation indices < llm_summary index < opinion indices."""
    # Long opinion so multiple opinion chunks are produced
    opinion = "Opinion text block. " * 500  # ~10000 chars
    mock_s3, mock_bedrock = _mock_clients(
        opinion_bytes=opinion.encode(),
        llm_response="Inferred holding summary.",
    )
    doc = {
        "doc_id": "case-law-331-wis-2d-256",
        "key": "raw/case-law-331-wis-2d-256/case-law-331-wis-2d-256.txt",
        "size": 10000,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "331 Wis. 2d 256",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    # Index groups by chunk_index: annotations=0..999, llm_summary=500, opinion=1000+
    annotation_idxs = [c["metadata"]["chunk_index"] for c in result["chunks"] if c["metadata"]["chunk_kind"] == "annotation"]
    llm_idxs = [c["metadata"]["chunk_index"] for c in result["chunks"] if c["metadata"]["chunk_kind"] == "llm_summary"]
    opinion_idxs = [c["metadata"]["chunk_index"] for c in result["chunks"] if c["metadata"]["chunk_kind"] == "opinion"]

    if annotation_idxs and llm_idxs:
        assert max(annotation_idxs) < min(llm_idxs)
    if llm_idxs and opinion_idxs:
        assert max(llm_idxs) < min(opinion_idxs)


def test_opinion_chunk_count_capped() -> None:
    """Very long opinions are capped to MAX_OPINION_CHUNKS to keep graph usable."""
    # 100k-char opinion would produce ~55 chunks without cap
    opinion = "Opinion text. " * 7000
    mock_s3, mock_bedrock = _mock_clients(opinion_bytes=opinion.encode())
    doc = {
        "doc_id": "case-law-2009-wi-app-159",
        "key": "raw/case-law-2009-wi-app-159/case-law-2009-wi-app-159.txt",
        "size": 100000,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "2009 WI App 159",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)
    from scripts.graphrag.extract import MAX_OPINION_CHUNKS

    opinion_chunks = [c for c in result["chunks"] if c["metadata"]["chunk_kind"] == "opinion"]
    assert len(opinion_chunks) <= MAX_OPINION_CHUNKS


def test_stub_only_case_no_opinion_file() -> None:
    """Case where only a .json stub exists (Scholar+CourtListener both missed)."""
    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    # .json stubs should NOT be read as opinions — key ends in .json not .txt
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": "Topic-only inference."}]}}
    }

    doc = {
        "doc_id": "case-law-missing-case",
        "key": "raw/case-law-missing-case/case-law-missing-case.json",
        "size": 200,
    }
    metadata = {
        "doc_type": "case_law",
        "citation": "456 Wis. 2d 1",
        "source_url": "",
        "citing_statutes": '[{"file": "70.pdf", "pages": [25]}]',
    }
    config = {"bedrock_llm_model": "test-model"}

    def run(extract):
        return extract.process_case_law_document(doc, "bucket", metadata, config)

    result = _with_mocked_extract(mock_s3, mock_bedrock, run)

    # No opinion_text should be read since key ends in .json
    assert not any(c["metadata"]["chunk_kind"] == "opinion" for c in result["chunks"])
