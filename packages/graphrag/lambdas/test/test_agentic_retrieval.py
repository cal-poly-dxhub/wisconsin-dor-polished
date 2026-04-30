"""Unit tests for agentic retrieval handler.

These tests mock the Lambda layer dependencies (step_function_types, websocket_utils)
since they are only available at deploy time via Lambda layers.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic", reason="pydantic required for agentic retrieval tests")

# Mock Lambda layer dependencies before importing main
sys.modules["step_function_types"] = MagicMock()
sys.modules["step_function_types.errors"] = MagicMock()
sys.modules["step_function_types.models"] = MagicMock()
sys.modules["websocket_utils"] = MagicMock()
sys.modules["websocket_utils.models"] = MagicMock()
sys.modules["websocket_utils.utils"] = MagicMock()

# Set up realistic mock models
from unittest.mock import PropertyMock
from types import SimpleNamespace


class MockUserQuery(pydantic.BaseModel):
    query: str
    query_id: str
    session_id: str


class MockRAGDocument(pydantic.BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"


# Patch the models module to provide real Pydantic models
models_mock = sys.modules["step_function_types.models"]
models_mock.UserQuery = MockUserQuery
models_mock.RAGDocument = MockRAGDocument
models_mock.DocumentResource = MagicMock()
models_mock.GenerateResponseJob = MagicMock()
models_mock.RetrieveResult = MagicMock()
models_mock.StreamResourcesJob = MagicMock()

errors_mock = sys.modules["step_function_types.errors"]
errors_mock.ValidationError = Exception
errors_mock.report_error = MagicMock()


def test_process_event_flat_input():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import process_event

    event = {"query": "What is property tax?", "query_id": "q-1", "session_id": "s-1"}
    result = process_event(event)

    assert result.query == "What is property tax?"
    assert result.query_id == "q-1"
    assert result.session_id == "s-1"


def test_process_event_rejects_malformed():
    import pytest

    with patch("main.boto3"), patch("main.NeptuneClient"):
        from main import process_event

    with pytest.raises(Exception):
        process_event({"bad": "data"})


def test_build_rag_documents():
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()
        mock_instance.get_document.return_value = {"title": "Test Doc", "id": "doc-1"}
        MockNeptune.return_value = mock_instance

        # Re-import to get fresh module with mocked neptune
        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_rag_documents

            chunks = [
                {"doc_id": "doc-1", "text": "chunk 1 text", "source_url": "http://example.com"},
                {"doc_id": "doc-1", "text": "chunk 2 text", "source_url": "http://example.com"},
            ]

            docs = _build_rag_documents(chunks, {"doc-1"}, {})

            assert len(docs) == 1
            assert "chunk 1 text" in docs[0].content
            assert "chunk 2 text" in docs[0].content


def test_collapse_case_law_by_title_merges_parallel_citations():
    """Parallel citations of the same decision (e.g., N.W.2d + Wis.2d) get
    separate Neptune Document nodes but share a title — we collapse them."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-972-n-w-2d-544": MockRAGDocument(
                document_id="case-law-972-n-w-2d-544-abc1234",
                title="State of Wisconsin ex rel. Nudo Holdings",
                content="nudo content from n.w.2d host",
                discovery_tag="vector-search",
            ),
            "case-law-401-wis-2d-27": MockRAGDocument(
                document_id="case-law-401-wis-2d-27-def5678",
                title="State of Wisconsin ex rel. Nudo Holdings",
                content="nudo content from wis.2d host",
                discovery_tag="graph-neighbor",
            ),
            "wpam-ch-1": MockRAGDocument(
                document_id="wpam-ch-1-aaa1111",
                title="WPAM Chapter 1",
                content="wpam content",
                discovery_tag="vector-search",
            ),
        }

        merged = _collapse_case_law_by_title(docs)

        assert len(merged) == 2  # two Nudo stubs collapsed, WPAM untouched
        nudo_cards = [d for d in merged.values() if "Nudo" in d.title]
        assert len(nudo_cards) == 1
        # Higher-priority tag wins (vector-search > graph-neighbor)
        assert nudo_cards[0].discovery_tag == "vector-search"
        # Both chunks' content concatenated
        assert "n.w.2d host" in nudo_cards[0].content
        assert "wis.2d host" in nudo_cards[0].content


def test_collapse_case_law_leaves_distinct_cases_alone():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-1": MockRAGDocument(
                document_id="case-law-1-x",
                title="Smith v. Jones",
                content="a",
                discovery_tag="vector-search",
            ),
            "case-law-2": MockRAGDocument(
                document_id="case-law-2-y",
                title="Doe v. Roe",
                content="b",
                discovery_tag="vector-search",
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 2


def test_collapse_case_law_merges_divergent_suffixes():
    """LLM classifier sometimes gives parallel citations of the same decision
    different descriptive suffixes after an em-dash. They should still merge."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-657-n-w-2d-112": MockRAGDocument(
                document_id="case-law-657-n-w-2d-112-aaa",
                title=(
                    "Fee and Fogarty v. Town of Florence Board of Review – "
                    "Court of Appeals Decision on Agricultural Land Classification"
                ),
                content="nw2d host",
                discovery_tag="vector-search",
            ),
            "case-law-259-wis-2d-868": MockRAGDocument(
                document_id="case-law-259-wis-2d-868-bbb",
                title=(
                    "Fee and Fogarty v. Town of Florence Board of Review – "
                    "Property Tax Assessment Appeal (Agricultural Classification)"
                ),
                content="wis2d host",
                discovery_tag="vector-search",
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 1
        only = next(iter(merged.values()))
        assert "nw2d host" in only.content
        assert "wis2d host" in only.content


def test_collapse_case_law_does_not_overmerge_same_name_different_year():
    """Two decisions with the same case name but different years (different
    cases with reused party names) must not collapse."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-2001": MockRAGDocument(
                document_id="case-law-2001-x",
                title="Smith v. Jones, 2001 WI 10",
                content="older",
                discovery_tag="vector-search",
            ),
            "case-law-2015": MockRAGDocument(
                document_id="case-law-2015-y",
                title="Smith v. Jones, 2015 WI 50",
                content="newer",
                discovery_tag="vector-search",
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 2


def test_collapse_case_law_yearless_doc_joins_dominant_year_bucket():
    """When one parallel citation has a year and the other lost it during
    ingest, the yearless doc attaches to the dominant year bucket."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-with-year": MockRAGDocument(
                document_id="case-law-with-year-x",
                title="Thoma v. Village of Slinger, 2018 WI 45",
                content="yearful",
                discovery_tag="vector-search",
            ),
            "case-law-no-year": MockRAGDocument(
                document_id="case-law-no-year-y",
                title="Thoma v. Village of Slinger",
                content="yearless",
                discovery_tag="graph-neighbor",
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 1
        only = next(iter(merged.values()))
        assert "yearful" in only.content
        assert "yearless" in only.content
