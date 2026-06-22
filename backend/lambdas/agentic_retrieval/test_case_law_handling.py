"""Tests for the case_law_handling module."""

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

from case_law_handling import (
    apply_case_law_links,
    build_opinion_card,
    collapse_case_law_by_title,
    extract_case_name,
    extract_year,
    is_case_law_stub,
)


class TestIsCaseLawStub:
    def test_true_for_case_law_prefix(self):
        assert is_case_law_stub("case-law-123-wis-2d-45") is True

    def test_false_for_other(self):
        assert is_case_law_stub("wpam-ch-1") is False
        assert is_case_law_stub("WIS-STAT-70.32") is False


class TestExtractCaseName:
    def test_cuts_at_em_dash(self):
        name = extract_case_name("Fee v. Florence Board – Court of Appeals Decision")
        assert name == "fee v. florence board"

    def test_cuts_at_colon(self):
        name = extract_case_name("Smith v. Jones: Summary of Holdings")
        assert name == "smith v. jones"

    def test_cuts_at_comma_year(self):
        name = extract_case_name("Thoma v. Village of Slinger, 2018 WI 45")
        assert name == "thoma v. village of slinger"

    def test_no_separator(self):
        name = extract_case_name("Simple Case Name")
        assert name == "simple case name"

    def test_collapses_whitespace(self):
        name = extract_case_name("  Foo   v.   Bar  ")
        assert name == "foo v. bar"


class TestExtractYear:
    def test_finds_year(self):
        assert extract_year("Smith v. Jones, 2018 WI 45") == "2018"

    def test_no_year(self):
        assert extract_year("Smith v. Jones") is None

    def test_first_year(self):
        assert extract_year("2001 WI 10, 2002 supplement") == "2001"


class TestCollapseCaseLawByTitle:
    def test_merges_parallel_citations(self):
        docs = {
            "case-law-972-n-w-2d-544": MockRAGDocument(
                document_id="case-law-972-n-w-2d-544-abc",
                title="State of Wisconsin ex rel. Nudo Holdings",
                content="nudo content from n.w.2d host",
                discovery_tag="vector-search",
            ),
            "case-law-401-wis-2d-27": MockRAGDocument(
                document_id="case-law-401-wis-2d-27-def",
                title="State of Wisconsin ex rel. Nudo Holdings",
                content="nudo content from wis.2d host",
                discovery_tag="graph-neighbor",
            ),
            "wpam-ch-1": MockRAGDocument(
                document_id="wpam-ch-1-aaa",
                title="WPAM Chapter 1",
                content="wpam content",
                discovery_tag="vector-search",
            ),
        }

        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 2
        nudo_cards = [d for d in merged.values() if "Nudo" in d.title]
        assert len(nudo_cards) == 1
        assert nudo_cards[0].discovery_tag == "vector-search"
        assert "n.w.2d host" in nudo_cards[0].content
        assert "wis.2d host" in nudo_cards[0].content

    def test_preserves_source_url(self):
        docs = {
            "case-law-foo-1": MockRAGDocument(
                document_id="case-law-foo-1-abc",
                title="Foo v. Bar",
                content="primary",
                source_url="https://scholar.google.com/foo",
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
            "case-law-foo-2": MockRAGDocument(
                document_id="case-law-foo-2-def",
                title="Foo v. Bar",
                content="parallel",
                source_url="https://scholar.google.com/bar",
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
        }

        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 1
        surviving = next(iter(merged.values()))
        assert surviving.source_url is not None
        assert "scholar.google.com" in surviving.source_url

    def test_leaves_distinct_cases_alone(self):
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
        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 2

    def test_merges_divergent_suffixes(self):
        docs = {
            "case-law-657-n-w-2d-112": MockRAGDocument(
                document_id="case-law-657-n-w-2d-112-aaa",
                title="Fee and Fogarty v. Town of Florence Board of Review – Court of Appeals",
                content="nw2d host",
                discovery_tag="vector-search",
            ),
            "case-law-259-wis-2d-868": MockRAGDocument(
                document_id="case-law-259-wis-2d-868-bbb",
                title="Fee and Fogarty v. Town of Florence Board of Review – Property Tax Appeal",
                content="wis2d host",
                discovery_tag="vector-search",
            ),
        }
        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 1
        only = next(iter(merged.values()))
        assert "nw2d host" in only.content
        assert "wis2d host" in only.content

    def test_does_not_overmerge_different_years(self):
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
        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 2

    def test_yearless_joins_dominant_year_bucket(self):
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
        merged = collapse_case_law_by_title(docs)
        assert len(merged) == 1
        only = next(iter(merged.values()))
        assert "yearful" in only.content
        assert "yearless" in only.content


class TestBuildOpinionCard:
    def test_links_to_scholar(self):
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {
            "title": "State v. Defendant",
            "authority_level": 3,
        }
        payload = {
            "citation": "123 Wis. 2d 45",
            "raw_key": "raw/case-law-123-wis-2d-45/123-wis-2d-45.txt",
            "text": "full opinion text...",
            "scholar_url": "https://scholar.google.com/foo",
        }
        card = build_opinion_card("case-law-123-wis-2d-45", payload, mock_neptune)
        assert card.s3_key is None
        assert card.source_url == "https://scholar.google.com/foo"
        assert card.start_page is None
        assert card.authority_level == 3
        assert card.discovery_tag == "opinion-fetched"
        assert card.content == "full opinion text..."

    def test_fallback_when_no_doc_info(self):
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = None
        payload = {
            "citation": "123 Wis. 2d 45",
            "raw_key": "",
            "text": "",
            "scholar_url": "https://scholar.google.com/foo",
        }
        card = build_opinion_card("case-law-123-wis-2d-45", payload, mock_neptune)
        assert card.source_url == "https://scholar.google.com/foo"
        assert card.authority_level == 3

    def test_no_link_when_citation_and_scholar_empty(self):
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {"title": "Untitled", "authority_level": 3}
        payload = {"citation": "", "raw_key": "", "text": "body", "scholar_url": ""}
        card = build_opinion_card("case-law-unknown", payload, mock_neptune)
        assert card.s3_key is None
        assert card.source_url is None


class TestApplyCaseLawLinks:
    def test_replaces_s3_with_public_url(self):
        docs_by_id = {
            "case-law-200-wis-2d-1": MockRAGDocument(
                document_id="case-law-200-wis-2d-1-abc",
                title="Some Case",
                content="text",
                s3_key="raw/case-law-200-wis-2d-1/file.txt",
                source_url=None,
            ),
        }
        doc_infos = {
            "case-law-200-wis-2d-1": {
                "source_url": "https://courtlistener.com/opinion/123/",
                "citation": "200 Wis. 2d 1",
            }
        }
        result = apply_case_law_links(docs_by_id, doc_infos)
        card = result["case-law-200-wis-2d-1"]
        assert card.source_url == "https://courtlistener.com/opinion/123/"
        assert card.s3_key is None

    def test_skips_non_case_law(self):
        docs_by_id = {
            "wpam-ch-1": MockRAGDocument(
                document_id="wpam-ch-1-abc",
                title="WPAM",
                content="text",
                s3_key="raw/wpam/wpam.pdf",
            ),
        }
        result = apply_case_law_links(docs_by_id, {})
        assert result["wpam-ch-1"].s3_key == "raw/wpam/wpam.pdf"
