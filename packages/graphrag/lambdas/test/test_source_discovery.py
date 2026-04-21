"""Tests for source discovery tagging.

Every RAGDocument returned to the frontend carries a discovery_tag that
records HOW it entered the answer's evidence set:
  - 'vector-search': found via vector_search top-k chunks
  - 'graph-neighbor': surfaced by auto-enrichment or explicit get_neighbors
  - 'fetched': pulled explicitly via get_document
  - 'framework-list': seen via list_framework_docs
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic", reason="pydantic required")

# Set up the path to import from the test directory
test_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, test_dir)


def test_discovery_tag_vector_search():
    # Fresh import (test_agentic_retrieval mocks step_function_types)
    import test_agentic_retrieval  # noqa: F401  — sets up sys.modules
    if "main" in sys.modules:
        del sys.modules["main"]

    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {"title": "Doc A", "id": "doc-A"}
        MockNeptune.return_value = mock_neptune

        with patch("main.neptune", mock_neptune):
            from main import _build_rag_documents

            chunks = [{"doc_id": "doc-A", "text": "chunk text"}]
            discovery = {"doc-A": "vector-search"}
            docs = _build_rag_documents(chunks, {"doc-A"}, discovery)

            assert len(docs) == 1
            assert docs[0].discovery_tag == "vector-search"


def test_discovery_tag_default_when_absent():
    import test_agentic_retrieval  # noqa: F401
    if "main" in sys.modules:
        del sys.modules["main"]

    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {"title": "Doc A", "id": "doc-A"}
        MockNeptune.return_value = mock_neptune

        with patch("main.neptune", mock_neptune):
            from main import _build_rag_documents

            chunks = [{"doc_id": "doc-A", "text": "chunk text"}]
            docs = _build_rag_documents(chunks, {"doc-A"}, {})

            assert len(docs) == 1
            # default when no explicit tag — still a valid tag, not None
            assert docs[0].discovery_tag == "unknown"
