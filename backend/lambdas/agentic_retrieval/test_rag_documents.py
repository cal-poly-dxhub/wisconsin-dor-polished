"""Tests for the rag_documents module."""

import sys
from unittest.mock import MagicMock

import pytest

pydantic = pytest.importorskip("pydantic")


class MockRAGDocument(pydantic.BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"
    authority_level: int | None = None
    s3_key: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    edition_year: int | None = None

    def model_copy(self, update=None):
        data = self.model_dump()
        if update:
            data.update(update)
        return MockRAGDocument(**data)


sys.modules["step_function_types.models"].RAGDocument = MockRAGDocument

from rag_documents import build_rag_documents, _generate_source_label


class TestGenerateSourceLabel:
    def test_returns_gov_url_when_present(self):
        chunk = {"source_url": "https://www.revenue.wi.gov/dor-publications/wpam.pdf"}
        doc_info = {"title": "Wisconsin Property Assessment Manual"}
        assert _generate_source_label(chunk, doc_info) == "https://www.revenue.wi.gov/dor-publications/wpam.pdf"

    def test_falls_back_to_title(self):
        assert _generate_source_label({"s3_key": "raw/x/x.pdf"}, {"title": "WPAM"}) == "WPAM"

    def test_returns_empty_when_no_info(self):
        assert _generate_source_label({}, None) == ""
        assert _generate_source_label({}, {}) == ""


class TestBuildRagDocuments:
    def _mock_neptune(self, doc_info=None):
        mock = MagicMock()
        mock.get_document.return_value = doc_info or {"title": "Test Doc", "id": "doc-1"}
        mock.find_stub_promotion.return_value = None
        return mock

    def test_basic_chunk_assembly(self):
        neptune = self._mock_neptune()
        chunks = [
            {"doc_id": "doc-1", "text": "chunk 1", "source_url": "http://example.com", "s3_key": "raw/doc-1/doc-1.pdf", "start_page": 12, "end_page": 14},
            {"doc_id": "doc-1", "text": "chunk 2", "source_url": "http://example.com", "s3_key": "raw/doc-1/doc-1.pdf", "start_page": 20, "end_page": 22},
        ]
        docs = build_rag_documents(chunks, {"doc-1"}, {}, neptune_client=neptune)
        assert len(docs) == 1
        assert "chunk 1" in docs[0].content
        assert "chunk 2" in docs[0].content
        assert docs[0].s3_key == "raw/doc-1/doc-1.pdf"
        assert docs[0].start_page == 12
        assert docs[0].source_url == "http://example.com"

    def test_discovery_tag_vector_search(self):
        neptune = self._mock_neptune()
        chunks = [{"doc_id": "doc-A", "text": "text"}]
        docs = build_rag_documents(chunks, {"doc-A"}, {"doc-A": "vector-search"}, neptune_client=neptune)
        assert docs[0].discovery_tag == "vector-search"

    def test_discovery_tag_defaults_to_unknown(self):
        neptune = self._mock_neptune()
        chunks = [{"doc_id": "doc-A", "text": "text"}]
        docs = build_rag_documents(chunks, {"doc-A"}, {}, neptune_client=neptune)
        assert docs[0].discovery_tag == "unknown"

    def test_promotes_stub_statute_to_parent(self):
        mock = MagicMock()

        def get_document(doc_id):
            if doc_id == "WIS-STAT-70.32":
                return {
                    "id": "WIS-STAT-70.32",
                    "title": "Wis. Stat. 70.32",
                    "summary": None,
                    "source_url": None,
                    "s3_key": None,
                    "authority_level": 2,
                    "labels": ["Statute"],
                }
            return None

        mock.get_document.side_effect = get_document
        mock.find_stub_promotion.return_value = {
            "id": "statutes-70",
            "title": "Chapter 70",
            "summary": "Chapter 70 governs general property taxes...",
            "source_url": "",
            "s3_key": "raw/statutes-70/statutes-70.pdf",
            "authority_level": 2,
            "start_page": 22,
            "end_page": 23,
        }

        docs = build_rag_documents([], {"WIS-STAT-70.32"}, {}, neptune_client=mock)
        assert len(docs) == 1
        card = docs[0]
        assert card.document_id.startswith("WIS-STAT-70.32-")
        assert card.title == "Wis. Stat. 70.32"
        assert "Chapter 70 governs" in card.content
        assert card.s3_key == "raw/statutes-70/statutes-70.pdf"
        assert card.start_page == 22
        assert card.authority_level == 2

    def test_stub_keeps_statute_authority_when_promoted_to_wpam(self):
        mock = MagicMock()

        def get_document(doc_id):
            if doc_id == "WIS-STAT-70.49(2)":
                return {
                    "id": "WIS-STAT-70.49(2)",
                    "title": "Wis. Stat. 70.49(2)",
                    "summary": None,
                    "authority_level": None,
                    "labels": ["Statute"],
                }
            return None

        mock.get_document.side_effect = get_document
        mock.find_stub_promotion.return_value = {
            "summary": "The WPAM reference...",
            "s3_key": "raw/wpam/wpam.pdf",
            "authority_level": 5,
            "start_page": 10,
            "end_page": 11,
        }

        docs = build_rag_documents([], {"WIS-STAT-70.49(2)"}, {}, neptune_client=mock)
        card = docs[0]
        assert "WPAM reference" in card.content
        assert card.authority_level == 2  # Statute, not WPAM's 5

    def test_skips_promotion_when_no_parent(self):
        mock = MagicMock()
        mock.get_document.return_value = {
            "id": "ORPHAN-STUB",
            "title": "Orphan Stub",
            "summary": None,
            "labels": ["Statute"],
        }
        mock.find_stub_promotion.return_value = None

        docs = build_rag_documents([], {"ORPHAN-STUB"}, {}, neptune_client=mock)
        assert len(docs) == 1
        assert docs[0].title == "Orphan Stub"
        assert docs[0].content == ""

    def test_case_law_stub_uses_node_source_url(self):
        mock = MagicMock()
        mock.get_document.return_value = {
            "title": "Some Case v. Other",
            "authority_level": 3,
            "citation": "200 Wis. 2d 1",
            "source_url": "https://www.courtlistener.com/opinion/12345/",
            "s3_key": "raw/case-law-200-wis-2d-1/file.txt",
        }
        mock.find_stub_promotion.return_value = None

        chunks = [{"doc_id": "case-law-200-wis-2d-1", "text": "stub summary", "s3_key": "raw/case-law-200-wis-2d-1/file.txt"}]
        docs = build_rag_documents(chunks, {"case-law-200-wis-2d-1"}, {}, neptune_client=mock)
        card = docs[0]
        assert card.s3_key is None
        assert card.source_url == "https://www.courtlistener.com/opinion/12345/"

    def test_case_law_stub_falls_back_to_scholar(self):
        mock = MagicMock()
        mock.get_document.return_value = {
            "title": "Some Case",
            "authority_level": 3,
            "citation": "200 Wis. 2d 1",
            "source_url": None,
            "s3_key": "raw/case-law-200-wis-2d-1/file.txt",
        }
        mock.find_stub_promotion.return_value = None

        chunks = [{"doc_id": "case-law-200-wis-2d-1", "text": "stub", "s3_key": "raw/x.txt"}]
        docs = build_rag_documents(chunks, {"case-law-200-wis-2d-1"}, {}, neptune_client=mock)
        card = docs[0]
        assert card.s3_key is None
        assert card.source_url is not None
        assert "scholar.google.com" in card.source_url

    def test_fetched_opinion_replaces_stub(self):
        mock = MagicMock()
        mock.get_document.return_value = {
            "title": "Test Case",
            "authority_level": 3,
        }
        mock.find_stub_promotion.return_value = None

        fetched_opinions = {
            "case-law-100-wis-2d-1": {
                "citation": "100 Wis. 2d 1",
                "raw_key": "raw/opinion.txt",
                "text": "Full opinion text here.",
                "scholar_url": "https://scholar.google.com/test",
            }
        }
        docs = build_rag_documents(
            [], {"case-law-100-wis-2d-1"}, {}, fetched_opinions,
            neptune_client=mock,
        )
        assert len(docs) == 1
        card = docs[0]
        assert card.content == "Full opinion text here."
        assert card.discovery_tag == "opinion-fetched"
