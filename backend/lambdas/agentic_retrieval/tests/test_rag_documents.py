"""Tests for the rag_documents module.

Uses the real step_function_types models (available via backend/layers on
sys.path) — do NOT stub them into sys.modules; that mutates shared module
objects and poisons other test files in the same pytest process.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pydantic")

from rag_documents import _generate_source_label, build_rag_documents


class TestGenerateSourceLabel:
    def test_returns_gov_url_when_present(self):
        chunk = {"source_url": "https://www.revenue.wi.gov/dor-publications/wpam.pdf"}
        doc_info = {"title": "Wisconsin Property Assessment Manual"}
        assert (
            _generate_source_label(chunk, doc_info)
            == "https://www.revenue.wi.gov/dor-publications/wpam.pdf"
        )

    def test_falls_back_to_title(self):
        assert _generate_source_label({"s3_key": "raw/x/x.pdf"}, {"title": "WPAM"}) == "WPAM"

    def test_returns_empty_when_no_info(self):
        assert _generate_source_label({}, None) == ""
        assert _generate_source_label({}, {}) == ""


class TestBuildRagDocuments:
    def _mock_neptune(self, doc_info=None):
        mock = MagicMock()
        mock.get_document.return_value = doc_info or {"title": "Test Doc", "id": "doc-1"}
        return mock

    def test_basic_chunk_assembly(self):
        neptune = self._mock_neptune()
        chunks = [
            {
                "doc_id": "doc-1",
                "text": "chunk 1",
                "source_url": "http://example.com",
                "s3_key": "raw/doc-1/doc-1.pdf",
                "start_page": 12,
                "end_page": 14,
            },
            {
                "doc_id": "doc-1",
                "text": "chunk 2",
                "source_url": "http://example.com",
                "s3_key": "raw/doc-1/doc-1.pdf",
                "start_page": 20,
                "end_page": 22,
            },
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
        docs = build_rag_documents(
            chunks, {"doc-A"}, {"doc-A": "vector-search"}, neptune_client=neptune
        )
        assert docs[0].discovery_tag == "vector-search"

    def test_discovery_tag_defaults_to_unknown(self):
        neptune = self._mock_neptune()
        chunks = [{"doc_id": "doc-A", "text": "text"}]
        docs = build_rag_documents(chunks, {"doc-A"}, {}, neptune_client=neptune)
        assert docs[0].discovery_tag == "unknown"

    def test_doc_with_summary_uses_summary_as_content(self):
        """Docs reached only via graph traversal render their node summary.

        Stub resolution now happens in the graph itself (DEFINED_BY
        auto-traversal in get_neighbors, from the graph wiring overhaul),
        so build_rag_documents no longer promotes stubs to parent docs.
        """
        mock = MagicMock()
        mock.get_document.return_value = {
            "id": "statutes-70",
            "title": "Chapter 70",
            "summary": "Chapter 70 governs general property taxes...",
            "source_url": "https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf",
            "s3_key": "raw/statutes-70/statutes-70.pdf",
            "authority_level": 2,
            "labels": ["Statute"],
        }

        docs = build_rag_documents([], {"statutes-70"}, {}, neptune_client=mock)
        assert len(docs) == 1
        card = docs[0]
        assert card.document_id.startswith("statutes-70-")
        assert card.title == "Chapter 70"
        assert "Chapter 70 governs" in card.content
        assert card.s3_key == "raw/statutes-70/statutes-70.pdf"
        assert card.authority_level == 2

    def test_summaryless_stub_builds_empty_card_and_warns(self, caplog):
        """A summaryless stub reaching the citation card is unexpected —
        DEFINED_BY auto-resolution should have replaced it. The card is
        still rendered (empty content) and a loud warning is logged."""
        mock = MagicMock()
        mock.get_document.return_value = {
            "id": "WIS-STAT-70.32",
            "title": "Wis. Stat. 70.32",
            "summary": None,
            "source_url": None,
            "s3_key": None,
            "authority_level": 2,
            "labels": ["Statute"],
        }

        with caplog.at_level("WARNING", logger="rag_documents"):
            docs = build_rag_documents([], {"WIS-STAT-70.32"}, {}, neptune_client=mock)
        assert len(docs) == 1
        card = docs[0]
        assert card.title == "Wis. Stat. 70.32"
        assert card.content == ""
        assert card.authority_level == 2
        assert any("STUB_PROMOTION_TRIGGERED" in r.getMessage() for r in caplog.records)

    def test_statute_chapter_splits_sections_by_heading(self):
        """A statute chapter PDF splits into per-section cards, each titled
        'Statute § <heading>'."""
        neptune = self._mock_neptune(
            {"title": "Chapter 70", "authority_level": 2, "source_url": "http://x"}
        )
        chunks = [
            {"doc_id": "statutes-70", "text": "a", "heading": "70.30", "authority_level": 2},
            {"doc_id": "statutes-70", "text": "b", "heading": "70.32", "authority_level": 2},
        ]
        docs = build_rag_documents(chunks, {"statutes-70"}, {}, neptune_client=neptune)
        titles = sorted(d.title for d in docs)
        assert titles == ["Statute § 70.30", "Statute § 70.32"]

    def test_non_statute_headings_stay_collapsed_and_untitled_as_statute(self):
        """WPAM / gov-pub chunks carry headings too, but must NOT be split into
        per-heading cards nor titled 'Statute § ...' (regression from 4d97390)."""
        neptune = self._mock_neptune(
            {
                "title": "WI Property Assessment Manual",
                "authority_level": 5,
                "source_url": "http://wpam",
            }
        )
        chunks = [
            {
                "doc_id": "wpam-2024",
                "text": "a",
                "heading": "Chapter 9 Real Property Valuation",
                "authority_level": 5,
                "framework_id": "FW-WPAM",
            },
            {
                "doc_id": "wpam-2024",
                "text": "b",
                "heading": "Chapter 12 Residential Property Valuation",
                "authority_level": 5,
                "framework_id": "FW-WPAM",
            },
        ]
        docs = build_rag_documents(chunks, {"wpam-2024"}, {}, neptune_client=neptune)
        assert len(docs) == 1
        assert docs[0].title == "WI Property Assessment Manual"
        assert not docs[0].title.startswith("Statute §")

    def test_case_law_stub_uses_node_source_url(self):
        mock = MagicMock()
        mock.get_document.return_value = {
            "title": "Some Case v. Other",
            "authority_level": 3,
            "citation": "200 Wis. 2d 1",
            "source_url": "https://www.courtlistener.com/opinion/12345/",
            "s3_key": "raw/case-law/wis-2d/200-wis-2d-1.txt",
        }

        chunks = [
            {
                "doc_id": "case-law-200-wis-2d-1",
                "text": "stub summary",
                "s3_key": "raw/case-law/wis-2d/200-wis-2d-1.txt",
            }
        ]
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
            "s3_key": "raw/case-law/wis-2d/200-wis-2d-1.txt",
        }

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

        fetched_opinions = {
            "case-law-100-wis-2d-1": {
                "citation": "100 Wis. 2d 1",
                "raw_key": "raw/opinion.txt",
                "text": "Full opinion text here.",
                "scholar_url": "https://scholar.google.com/test",
            }
        }
        docs = build_rag_documents(
            [],
            {"case-law-100-wis-2d-1"},
            {},
            fetched_opinions,
            neptune_client=mock,
        )
        assert len(docs) == 1
        card = docs[0]
        assert card.content == "Full opinion text here."
        assert card.discovery_tag == "opinion-fetched"
