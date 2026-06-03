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
    authority_level: int | None = None
    s3_key: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    edition_year: int | None = None


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
                {
                    "doc_id": "doc-1",
                    "text": "chunk 1 text",
                    "source_url": "http://example.com",
                    "s3_key": "raw/doc-1/doc-1.pdf",
                    "start_page": 12,
                    "end_page": 14,
                },
                {
                    "doc_id": "doc-1",
                    "text": "chunk 2 text",
                    "source_url": "http://example.com",
                    "s3_key": "raw/doc-1/doc-1.pdf",
                    "start_page": 20,
                    "end_page": 22,
                },
            ]

            docs = _build_rag_documents(chunks, {"doc-1"}, {})

            assert len(docs) == 1
            assert "chunk 1 text" in docs[0].content
            assert "chunk 2 text" in docs[0].content
            # Stable s3 reference is populated; first chunk wins.
            assert docs[0].s3_key == "raw/doc-1/doc-1.pdf"
            assert docs[0].start_page == 12
            assert docs[0].end_page == 14
            # source_url carries the public gov URL, not a presigned URL.
            assert docs[0].source_url == "http://example.com"
            assert "X-Amz-Signature" not in (docs[0].source_url or "")


def test_build_rag_documents_promotes_stub_statute_to_parent():
    """Stub Statute nodes (id/title only, no summary) cited by the agent get
    promoted to the parent chapter document so the card has real content and
    a page range that points the resolver at the right PDF page."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()

        def get_document(doc_id):
            if doc_id == "WIS-STAT-70.32":
                # Stub: title only, no summary, no s3_key.
                return {
                    "id": "WIS-STAT-70.32",
                    "title": "Wis. Stat. 70.32",
                    "summary": None,
                    "source_url": None,
                    "s3_key": None,
                    "authority_level": 2,
                    "edition_year": None,
                    "labels": ["Statute"],
                }
            return None

        mock_instance.get_document.side_effect = get_document
        mock_instance.find_stub_promotion.return_value = {
            "id": "statutes-70",
            "title": "Chapter 70 - General Property Taxes",
            "summary": "Chapter 70 governs general property taxes...",
            "source_url": "",
            "s3_key": "raw/statutes-70/statutes-70.pdf",
            "authority_level": 2,
            "start_page": 22,
            "end_page": 23,
        }
        MockNeptune.return_value = mock_instance

        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_rag_documents

            # No chunks — only cited_doc_ids drives the build.
            docs = _build_rag_documents([], {"WIS-STAT-70.32"}, {})

            assert len(docs) == 1
            stub_card = docs[0]
            # Stub's identity preserved so prose links still resolve.
            assert stub_card.document_id.startswith("WIS-STAT-70.32-")
            assert stub_card.title == "Wis. Stat. 70.32"
            # Promoted content + s3 reference from parent.
            assert "Chapter 70 governs" in stub_card.content
            assert stub_card.s3_key == "raw/statutes-70/statutes-70.pdf"
            assert stub_card.start_page == 22
            assert stub_card.end_page == 23
            assert stub_card.authority_level == 2


def test_build_rag_documents_stub_keeps_statute_authority_when_promoted_to_wpam():
    """A statute section stub (authority_level=None in the graph) promoted to a
    WPAM parent for content must keep its STATUTE authority, not inherit the
    WPAM parent's level 5. Otherwise 'Wis. Stat. 70.49(2)' renders a WPAM badge.

    Reproduces the live bug: WIS-STAT-* stubs have authority_level=None, and the
    chunk that best explains a section often lives in the WPAM, so the stub
    promotes to a WPAM Document and borrowed its level 5.
    """
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()

        def get_document(doc_id):
            if doc_id == "WIS-STAT-70.49(2)":
                return {
                    "id": "WIS-STAT-70.49(2)",
                    "title": "Wis. Stat. 70.49(2)",
                    "summary": None,
                    "source_url": None,
                    "s3_key": None,
                    "authority_level": None,  # live condition
                    "edition_year": None,
                    "labels": ["Statute"],
                }
            return None

        mock_instance.get_document.side_effect = get_document
        # Best-explaining chunk lives in the WPAM (authority_level 5).
        mock_instance.find_stub_promotion.return_value = {
            "id": "wpam-wisconsin-property-assessment-manual-2011",
            "title": "Wisconsin Property Assessment Manual (2011)",
            "summary": "The WPAM is a comprehensive reference guide...",
            "source_url": "",
            "s3_key": "raw/wpam-.../wpam.pdf",
            "authority_level": 5,
            "start_page": 10,
            "end_page": 11,
        }
        MockNeptune.return_value = mock_instance

        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_rag_documents

            docs = _build_rag_documents([], {"WIS-STAT-70.49(2)"}, {})

            assert len(docs) == 1
            card = docs[0]
            # Content is borrowed from the WPAM parent (that's fine)...
            assert "WPAM is a comprehensive" in card.content
            # ...but the card's IDENTITY is a statute, so the badge must be
            # Statute (2), NOT WPAM (5).
            assert card.authority_level == 2


def test_build_rag_documents_skips_promotion_when_no_parent():
    """When no chunk cites the stub, the card falls back to empty content
    rather than crashing — graceful degrade, not a failed query."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()
        mock_instance.get_document.return_value = {
            "id": "ORPHAN-STUB",
            "title": "Orphan Stub",
            "summary": None,
            "labels": ["Statute"],
        }
        mock_instance.find_stub_promotion.return_value = None
        MockNeptune.return_value = mock_instance

        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_rag_documents

            docs = _build_rag_documents([], {"ORPHAN-STUB"}, {})

            assert len(docs) == 1
            assert docs[0].title == "Orphan Stub"
            assert docs[0].content == ""
            assert docs[0].start_page is None


def test_generate_source_label_returns_gov_url_when_present():
    """_generate_source_label returns the gov URL as the badge label; no URL minting."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _generate_source_label

    chunk = {
        "s3_key": "raw/wpam/wpam.pdf",
        "start_page": 12,
        "end_page": 14,
        "source_url": "https://www.revenue.wi.gov/dor-publications/wpam.pdf",
    }
    doc_info = {"title": "Wisconsin Property Assessment Manual"}

    label = _generate_source_label(chunk, doc_info)
    assert label == "https://www.revenue.wi.gov/dor-publications/wpam.pdf"


def test_generate_source_label_falls_back_to_doc_title():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _generate_source_label

    label = _generate_source_label({"s3_key": "raw/wpam/wpam.pdf"}, {"title": "WPAM"})
    assert label == "WPAM"


def test_generate_source_label_returns_empty_when_no_info():
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _generate_source_label

    assert _generate_source_label({}, None) == ""
    assert _generate_source_label({}, {}) == ""


def test_build_opinion_card_uses_s3_key_not_presigned_url():
    """_build_opinion_card always links to Google Scholar, not S3, even when
    the opinion .txt is archived. The S3 text still rides in content for synthesis."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()
        mock_instance.get_document.return_value = {
            "title": "State v. Defendant",
            "authority_level": 3,
        }
        MockNeptune.return_value = mock_instance

        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_opinion_card

            payload = {
                "citation": "123 Wis. 2d 45",
                "raw_key": "raw/case-law-123-wis-2d-45/123-wis-2d-45.txt",
                "text": "full opinion text...",
                "scholar_url": "https://scholar.google.com/foo",
            }
            card = _build_opinion_card("case-law-123-wis-2d-45", payload)

            # Always link to Scholar, not S3.
            assert card.s3_key is None
            assert card.source_url == "https://scholar.google.com/foo"
            assert card.start_page is None
            assert card.end_page is None
            assert card.authority_level == 3
            assert card.discovery_tag == "opinion-fetched"
            # Opinion text still available for synthesis.
            assert card.content == "full opinion text..."


def test_build_opinion_card_falls_back_to_scholar_when_no_raw_key():
    """When fetch_case_opinion didn't find the .txt in S3, scholar_url is the
    public fallback so the card stays clickable without the resolver."""
    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_instance = MagicMock()
        mock_instance.get_document.return_value = None
        MockNeptune.return_value = mock_instance

        if "main" in sys.modules:
            del sys.modules["main"]

        with patch("main.neptune", mock_instance):
            from main import _build_opinion_card

            payload = {
                "citation": "123 Wis. 2d 45",
                "raw_key": "",
                "text": "",
                "scholar_url": "https://scholar.google.com/foo",
            }
            card = _build_opinion_card("case-law-123-wis-2d-45", payload)

            assert card.s3_key is None
            assert card.source_url == "https://scholar.google.com/foo"
            # No doc_info → fall back to literal 3 for case-law authority.
            assert card.authority_level == 3


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


def test_collapse_case_law_preserves_s3_key_and_pages():
    """The merge constructor must preserve the source_url (Google Scholar link)
    so parallel citations stay clickable after collapse."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-foo-1": MockRAGDocument(
                document_id="case-law-foo-1-abc",
                title="Foo v. Bar",
                content="primary opinion text",
                source_url="https://scholar.google.com/scholar?q=foo",
                s3_key=None,
                start_page=None,
                end_page=None,
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
            "case-law-foo-2": MockRAGDocument(
                document_id="case-law-foo-2-def",
                title="Foo v. Bar",
                content="parallel citation text",
                source_url="https://scholar.google.com/scholar?q=bar",
                s3_key=None,
                start_page=None,
                end_page=None,
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 1
        surviving = next(iter(merged.values()))
        # Merged cards preserve a source_url (Google Scholar link).
        assert surviving.source_url is not None
        assert "scholar.google.com" in surviving.source_url
        assert surviving.s3_key is None
        assert surviving.start_page is None
        assert surviving.end_page is None


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


def test_get_chat_history_returns_empty_when_unconfigured():
    """Without CHAT_HISTORY_TABLE_NAME set, history loading must not blow up;
    it returns [] so the agent falls back to no-history behavior."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch.dict(os.environ, {}, clear=False):
            import main
            main.CHAT_HISTORY_TABLE = ""
            assert main.get_chat_history("sess-1") == []


def test_get_chat_history_reads_from_gsi():
    """The loader must use the sessionIdKey GSI so it picks up prior turns
    ordered oldest → newest by timestamp."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main

        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"query": "q1", "answer": "a1", "timestamp": "2025-01-01"},
                {"query": "q2", "answer": "a2", "timestamp": "2025-01-02"},
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.object(main, "dynamodb_resource", mock_resource), \
             patch.object(main, "CHAT_HISTORY_TABLE", "SomeTable"):
            history = main.get_chat_history("sess-1")

        assert len(history) == 2
        assert history[0] == {"query": "q1", "answer": "a1"}
        assert history[1] == {"query": "q2", "answer": "a2"}
        # Must query the GSI — a table scan or primary-key query would
        # return the wrong records.
        kwargs = mock_table.query.call_args.kwargs
        assert kwargs["IndexName"] == "sessionIdKey"
        assert kwargs["ScanIndexForward"] is True


def test_get_chat_history_caps_at_max_turns():
    """Long sessions must not blow out the context window — the loader
    keeps only the last MAX_HISTORY_TURNS."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main

        items = [
            {"query": f"q{i}", "answer": f"a{i}", "timestamp": f"2025-01-{i:02d}"}
            for i in range(1, 11)
        ]
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": items}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.object(main, "dynamodb_resource", mock_resource), \
             patch.object(main, "CHAT_HISTORY_TABLE", "SomeTable"), \
             patch.object(main, "MAX_HISTORY_TURNS", 3):
            history = main.get_chat_history("sess-1")

        assert len(history) == 3
        # Newest three, ordered oldest-first.
        assert [h["query"] for h in history] == ["q8", "q9", "q10"]


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


def test_build_tool_call_summary_vector_search():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "vector_search", {"query": "ag use value"}
    ) == '"ag use value"'


def test_build_tool_call_summary_get_neighbors():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "get_neighbors", {"doc_id": "stat-70-32"}
    ) == "doc stat-70-32"


def test_build_tool_call_summary_faq_search():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "faq_search", {"query": "what is TID"}
    ) == '"what is TID"'


def test_build_tool_call_summary_answer():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary(
        "answer",
        {"response": "Use value...", "cited_doc_ids": ["a", "b", "c"]},
    ) == "with 3 cited doc(s)"


def test_build_tool_call_summary_unknown_tool_returns_empty():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    assert _build_tool_call_summary("mystery_tool", {"foo": "bar"}) == ""


def test_build_tool_call_summary_empty_inputs():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_call_summary

    # Empty query → empty string (not the literal '""')
    assert _build_tool_call_summary("vector_search", {}) == ""
    assert _build_tool_call_summary("faq_search", {"query": ""}) == ""
    # Empty doc_id → empty string (not "doc ")
    assert _build_tool_call_summary("get_neighbors", {"doc_id": ""}) == ""
    assert _build_tool_call_summary("get_authority_chain", {}) == ""
    # Empty cited list → "with 0 cited doc(s)" (count is meaningful here)
    assert _build_tool_call_summary("answer", {}) == "with 0 cited doc(s)"
    assert _build_tool_call_summary("answer", {"cited_doc_ids": None}) == "with 0 cited doc(s)"


def test_build_tool_result_summary_vector_search_ok():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary

    result = {
        "chunks": [
            {"doc_id": "doc-a", "text": "x", "score": 0.91},
            {"doc_id": "doc-a", "text": "y", "score": 0.85},
            {"doc_id": "doc-b", "text": "z", "score": 0.80},
        ],
        "graph_context": {"doc-a": [{"id": "doc-c"}]},
    }
    s = _build_tool_result_summary("vector_search", result)
    assert s["status"] == "ok"
    assert "3 chunks" in s["summary_text"]
    assert set(s["doc_ids"]) == {"doc-a", "doc-b"}
    assert isinstance(s["doc_titles"], list)
    # `raw` is the same dict produced by _summarize_tool_result.
    assert s["raw"]["tool_name"] == "vector_search"
    assert s["raw"]["chunk_count"] == 3
    # Structured metadata for the UI subtitle.
    assert s["metadata"]["chunkCount"] == 3
    assert s["metadata"]["docCount"] == 2
    assert s["metadata"]["neighborCount"] == 1
    assert s["metadata"]["topScore"] == pytest.approx(0.91)


def test_build_tool_result_summary_get_neighbors():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary
    result = {
        "neighbors": [
            {"id": "d1", "relationship": "CITES"},
            {"id": "d2", "relationship": "IMPLEMENTS"},
        ]
    }
    s = _build_tool_result_summary("get_neighbors", result)
    assert s["status"] == "ok"
    assert "2 neighbor" in s["summary_text"]
    assert set(s["doc_ids"]) == {"d1", "d2"}
    assert s["metadata"]["neighborCount"] == 2


def test_build_tool_result_summary_faq_search_with_scores():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary
    result = {
        "faqs": [
            {"text": "Q: x\nA: y", "score": 0.84},
            {"text": "Q: p\nA: q", "score": 0.71},
        ],
        "count": 2,
    }
    s = _build_tool_result_summary("faq_search", result)
    assert s["status"] == "ok"
    assert "top score 0.84" in s["summary_text"]
    assert s["doc_ids"] == []
    assert s["metadata"]["faqCount"] == 2
    assert s["metadata"]["topScore"] == pytest.approx(0.84)


def test_build_tool_result_summary_error_tool_result():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "get_document", {"error": "not found", "fallback_matches": []}
    )
    assert s["status"] == "error"
    assert "not found" in s["summary_text"]


def test_build_tool_result_summary_answer_terminal():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "answer", {"response": "Use value...", "cited_doc_ids": ["a", "b"]}
    )
    assert s["status"] == "terminal"
    assert "2 cited" in s["summary_text"]
    assert s["doc_ids"] == ["a", "b"]
    assert s["metadata"]["citedDocCount"] == 2


def test_build_tool_result_summary_fetch_opinion_miss():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _build_tool_result_summary
    s = _build_tool_result_summary(
        "fetch_case_opinion", {"found": False, "citation": "123 Wis. 2d 45"}
    )
    assert s["status"] == "miss"
    assert "123 Wis. 2d 45" in s["summary_text"]


def test_filter_metadata_keeps_allowed_keys_only():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _filter_metadata
    out = _filter_metadata({
        "chunkCount": 3,
        "topScore": 0.9,
        "latencyMs": 120,
        "query": "how do I appeal my property tax",
        "rawUserText": "sensitive",
    })
    assert out == {"chunkCount": 3, "topScore": 0.9, "latencyMs": 120}


def test_filter_metadata_non_dict_returns_empty():
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            from main import _filter_metadata
    assert _filter_metadata(None) == {}
    assert _filter_metadata("not a dict") == {}
    assert _filter_metadata([("key", "value")]) == {}


def test_emit_trace_sends_agent_event_message():
    import itertools
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            # Force re-import so EMIT_AGENT_TRACE module constant picks up env.
            import importlib, sys
            if "main" in sys.modules:
                del sys.modules["main"]
            import main

    mock_ws = MagicMock()
    mock_ws.send_json = MagicMock(return_value=None)
    with patch("main.asyncio.run", side_effect=lambda coro: None) as run_mock:
        main._emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="reasoning",
            turn=2,
            payload={"text": "thinking"},
            dev_payload={"foo": "bar"},
        )
        assert run_mock.called


def test_emit_trace_noop_when_ws_is_none():
    import itertools
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import sys
            if "main" in sys.modules:
                del sys.modules["main"]
            from main import _emit_trace
    _emit_trace(
        None,
        itertools.count(1).__next__,
        query_id="q-1",
        kind="loop_start",
        payload={"maxTurns": 10},
    )


def test_emit_trace_swallows_ws_exceptions():
    import itertools
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import sys
            if "main" in sys.modules:
                del sys.modules["main"]
            import main
    mock_ws = MagicMock()
    with patch("main.asyncio.run", side_effect=RuntimeError("boom")):
        main._emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="reasoning",
            payload={"text": "x"},
        )


def test_emit_trace_respects_emit_agent_trace_false():
    import itertools
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "false",
    }, clear=False):
        import sys
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    mock_ws = MagicMock()
    with patch("main.asyncio.run") as run_mock:
        main._emit_trace(
            mock_ws,
            itertools.count(1).__next__,
            query_id="q-1",
            kind="loop_start",
            payload={"maxTurns": 10},
        )
    run_mock.assert_not_called()
    # Restore default env for downstream tests that re-import main.
    import sys
    if "main" in sys.modules:
        del sys.modules["main"]


def test_run_agentic_loop_emits_trace_sequence(monkeypatch):
    """Drive run_agentic_loop through vector_search + answer and assert the
    WebSocket received reasoning, tool_call, tool_result, loop_complete.

    (loop_start lands in Task 8; this test asserts the subset Task 7 emits.)
    """
    import itertools
    import sys as _sys
    # Replace the MagicMock'd AgentEventMessage with a real lightweight model so
    # the test can inspect `.kind`, `.seq`, `.payload` on each emitted event.
    class FakeAgentEventMessage:
        def __init__(self, **fields):
            self.__dict__.update(fields)
    _sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage

    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    # FAQ returns a low-scoring hit so we fall through to the loop.
    monkeypatch.setattr(main, "_faq_search_direct", lambda q: {
        "faqs": [{"text": "Q: unrelated\nA: nope", "score": 0.2,
                  "source_uri": "s3://f/faq_1.txt"}],
        "count": 1,
    })

    # Turn 1: vector_search; Turn 2: answer.
    responses = [
        {
            "output": {"message": {"content": [
                {"text": "I'll search the graph."},
                {"toolUse": {"toolUseId": "t1", "name": "vector_search",
                             "input": {"query": "use value"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 100},
        },
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t2", "name": "answer",
                             "input": {"response": "Use value is...",
                                       "cited_doc_ids": ["doc-a"]}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
            "metrics": {"latencyMs": 120},
        },
    ]
    main.bedrock.converse = MagicMock(side_effect=responses)

    def fake_execute(name, input_, neptune_client, chat_history=None):
        if name == "vector_search":
            return {
                "chunks": [{"doc_id": "doc-a", "text": "..."}],
                "graph_context": {},
            }
        if name == "answer":
            return {
                "response": input_.get("response", ""),
                "cited_doc_ids": input_.get("cited_doc_ids", []),
            }
        return {}
    monkeypatch.setattr(main, "execute_tool", fake_execute)
    main.neptune.get_document = MagicMock(return_value=None)

    # Capture emitted messages: intercept asyncio.run(ws.send_json(msg)).
    sent_messages = []

    def fake_run(coro):
        coro.close()
    monkeypatch.setattr(main.asyncio, "run", fake_run)

    mock_ws = MagicMock()

    def capture_send(msg):
        sent_messages.append(msg)
        async def _noop():
            return None
        return _noop()
    mock_ws.send_json = capture_send

    main.run_agentic_loop(
        "what is use value?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    kinds = [m.kind for m in sent_messages]
    assert "reasoning" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "loop_complete"
    # seq is monotonically increasing and starts at 1.
    seqs = [m.seq for m in sent_messages]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1
    # The `answer` tool must NOT produce a tool_result event.
    answer_tool_results = [
        m for m in sent_messages
        if m.kind == "tool_result" and m.payload.get("toolName") == "answer"
    ]
    assert answer_tool_results == []
    # loop_complete carries terminalReason=answer_tool.
    complete = [m for m in sent_messages if m.kind == "loop_complete"][-1]
    assert complete.payload["terminalReason"] == "answer_tool"
    assert complete.payload["citedDocCount"] == 1
    # Cleanup for downstream re-imports.
    if "main" in _sys.modules:
        del _sys.modules["main"]


def test_run_agentic_loop_recovers_from_tool_exception(monkeypatch):
    """A tool that raises (e.g. a malformed get_document call) must NOT crash
    the request. The loop should feed an error tool_result back to the model
    so it can recover and still produce an answer.

    Regression for the live hang: get_document raised KeyError('doc_id') when
    the model passed node_id, the exception propagated out of the loop, the
    Lambda failed, and the user was stuck on "answering".
    """
    import itertools
    import sys as _sys

    class FakeAgentEventMessage:
        def __init__(self, **fields):
            self.__dict__.update(fields)
    _sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage

    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    monkeypatch.setattr(main, "_faq_search_direct", lambda q: {
        "faqs": [{"text": "Q: unrelated\nA: nope", "score": 0.2,
                  "source_uri": "s3://f/faq_1.txt"}],
        "count": 1,
    })

    # Turn 1: get_document (will raise); Turn 2: answer.
    responses = [
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t1", "name": "get_document",
                             "input": {"node_id": "doc-a"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 100},
        },
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t2", "name": "answer",
                             "input": {"response": "Recovered answer.",
                                       "cited_doc_ids": []}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
            "metrics": {"latencyMs": 120},
        },
    ]
    main.bedrock.converse = MagicMock(side_effect=responses)

    def fake_execute(name, input_, neptune_client, chat_history=None):
        if name == "get_document":
            raise KeyError("doc_id")
        if name == "answer":
            return {
                "response": input_.get("response", ""),
                "cited_doc_ids": input_.get("cited_doc_ids", []),
            }
        return {}
    monkeypatch.setattr(main, "execute_tool", fake_execute)

    def fake_run(coro):
        coro.close()
    monkeypatch.setattr(main.asyncio, "run", fake_run)

    mock_ws = MagicMock()

    def capture_send(msg):
        async def _noop():
            return None
        return _noop()
    mock_ws.send_json = capture_send

    # Must NOT raise — the tool exception is recovered, not propagated.
    answer, cited, rag_docs, faq_resource = main.run_agentic_loop(
        "what is use value?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    assert answer == "Recovered answer."
    # The model got a second turn, proving the error was fed back, not fatal.
    assert main.bedrock.converse.call_count == 2

    if "main" in _sys.modules:
        del _sys.modules["main"]


def test_run_agentic_loop_high_confidence_faq_continues_into_graph(monkeypatch):
    """High FAQ score no longer short-circuits — the loop continues into the
    graph so Claude can supplement the FAQ answer with citable evidence.

    Asserts:
      - bedrock.converse is invoked (graph traversal happens).
      - The trace emits loop_start, the seeded FAQ tool_call/tool_result,
        downstream tool events, and loop_complete with terminalReason=answer_tool.
      - The FAQ resource flows through to the result even when Claude's
        cited_doc_ids omit the FAQ id.
    """
    import itertools
    import sys as _sys

    class FakeAgentEventMessage:
        def __init__(self, **fields):
            self.__dict__.update(fields)
    _sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage

    # Realistic FAQResource for the assertion path.
    class FakeFAQ(pydantic.BaseModel):
        faq_id: str
        question: str
        answer: str

    class FakeFAQResource(pydantic.BaseModel):
        faqs: list[FakeFAQ]

    _sys.modules["step_function_types.models"].FAQ = FakeFAQ
    _sys.modules["step_function_types.models"].FAQResource = FakeFAQResource

    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    # High-scoring FAQ — used to trigger the new "anchor + supplement" path.
    monkeypatch.setattr(main, "_faq_search_direct", lambda q: {
        "faqs": [{"text": "Q: what is TID\nA: tax incremental district",
                  "score": 0.90, "source_uri": "s3://f/faq_1.txt"}],
        "count": 1,
    })

    # Turn 1: vector_search; Turn 2: answer (note cited_doc_ids omits the FAQ
    # id intentionally — the FAQ resource must still flow through).
    converse_responses = [
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t1", "name": "vector_search",
                             "input": {"query": "TID statute"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 100},
        },
        {
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t2", "name": "answer",
                             "input": {"response": "A TID is...",
                                       "cited_doc_ids": ["doc-stat"]}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 15, "outputTokens": 30, "totalTokens": 45},
            "metrics": {"latencyMs": 120},
        },
    ]
    main.bedrock.converse = MagicMock(side_effect=converse_responses)

    def fake_execute(name, input_, neptune_client, chat_history=None):
        if name == "vector_search":
            return {"chunks": [{"doc_id": "doc-stat", "text": "..."}],
                    "graph_context": {}}
        if name == "answer":
            return {"response": input_.get("response", ""),
                    "cited_doc_ids": input_.get("cited_doc_ids", [])}
        return {}
    monkeypatch.setattr(main, "execute_tool", fake_execute)
    main.neptune.get_document = MagicMock(return_value=None)

    sent = []

    def fake_run(coro):
        coro.close()
    monkeypatch.setattr(main.asyncio, "run", fake_run)

    mock_ws = MagicMock()

    def capture(msg):
        sent.append(msg)
        async def _noop():
            return None
        return _noop()
    mock_ws.send_json = capture

    answer, cited, rag_docs, faq_resource = main.run_agentic_loop(
        "what is TID?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    # Bedrock was actually called — the loop did not short-circuit.
    assert main.bedrock.converse.call_count == 2

    # FAQ resource is returned even though Claude only cited "doc-stat".
    assert faq_resource is not None
    assert len(faq_resource.faqs) == 1
    assert faq_resource.faqs[0].faq_id == "faq_1"

    # Trace emits the answer-tool terminal, not faq_short_circuit.
    kinds = [m.kind for m in sent]
    assert kinds[0] == "loop_start"
    assert kinds[-1] == "loop_complete"
    assert sent[-1].payload["terminalReason"] == "answer_tool"

    # Cleanup for downstream re-imports.
    if "main" in _sys.modules:
        del _sys.modules["main"]


def test_handler_attaches_ws_server_when_session_lookup_succeeds(monkeypatch):
    """The handler should look up the WebSocket connection from session_id and
    pass the WebSocketServer + a trace_seq counter into run_agentic_loop.
    """
    import sys as _sys
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
        "SESSIONS_TABLE_NAME": "t-sessions",
        "WEBSOCKET_CALLBACK_URL": "wss://example/stage",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    mock_ws = MagicMock()
    monkeypatch.setattr(
        main, "get_ws_connection_from_session", MagicMock(return_value=mock_ws)
    )
    monkeypatch.setattr(
        main,
        "run_agentic_loop",
        MagicMock(return_value=("ans", [], [], None)),
    )
    monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
    monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
        query="q", query_id="q-1", session_id="s-1"
    ))
    monkeypatch.setattr(main, "DocumentResource", MagicMock())

    ctx = SimpleNamespace(aws_request_id="r-1")
    main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

    kwargs = main.run_agentic_loop.call_args.kwargs
    assert kwargs["ws_server"] is mock_ws
    assert callable(kwargs["trace_seq"])
    if "main" in _sys.modules:
        del _sys.modules["main"]


def test_handler_runs_with_ws_none_when_session_lookup_fails(monkeypatch):
    """When the session lookup raises, the handler must fall back to ws_server=None
    so the retrieval loop still runs (trace emission is best-effort).
    """
    import sys as _sys
    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
        "SESSIONS_TABLE_NAME": "t-sessions",
        "WEBSOCKET_CALLBACK_URL": "wss://example/stage",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main

    def raise_lookup(sid):
        raise RuntimeError("no session")
    monkeypatch.setattr(main, "get_ws_connection_from_session", raise_lookup)
    monkeypatch.setattr(main, "run_agentic_loop",
                        MagicMock(return_value=("ans", [], [], None)))
    monkeypatch.setattr(main, "get_chat_history", lambda sid: [])
    monkeypatch.setattr(main, "process_event", lambda e: SimpleNamespace(
        query="q", query_id="q-1", session_id="s-1"
    ))
    monkeypatch.setattr(main, "DocumentResource", MagicMock())

    ctx = SimpleNamespace(aws_request_id="r-1")
    main.handler({"query": "q", "query_id": "q-1", "session_id": "s-1"}, ctx)

    kwargs = main.run_agentic_loop.call_args.kwargs
    assert kwargs["ws_server"] is None
    if "main" in _sys.modules:
        del _sys.modules["main"]


def test_build_opinion_card_links_to_scholar_not_s3(monkeypatch):
    import main

    # get_document is consulted for title/authority; stub it.
    monkeypatch.setattr(
        main.neptune,
        "get_document",
        lambda doc_id: {"title": "Corroon v. Hosch", "authority_level": 3},
    )

    payload = {
        "citation": "109 Wis. 2d 290",
        "raw_key": "raw/case-law-109-wis-2d-290/case-law-109-wis-2d-290.txt",
        "text": "Full opinion text...",
        "scholar_url": "http://scholar.google.com/scholar?q=109%20Wis.%202d%20290",
    }
    card = main._build_opinion_card("case-law-109-wis-2d-290", payload)

    # Even though raw_key is present, the user link must be Google Scholar.
    assert card.s3_key is None
    assert card.source_url == "http://scholar.google.com/scholar?q=109%20Wis.%202d%20290"
    # Opinion text still feeds downstream synthesis.
    assert card.content == "Full opinion text..."


def test_build_opinion_card_no_link_when_citation_and_scholar_url_empty(monkeypatch):
    import main

    monkeypatch.setattr(
        main.neptune, "get_document",
        lambda doc_id: {"title": "Untitled", "authority_level": 3},
    )
    payload = {"citation": "", "raw_key": "", "text": "body", "scholar_url": ""}
    card = main._build_opinion_card("case-law-unknown", payload)
    assert card.s3_key is None
    assert card.source_url is None


def test_build_rag_documents_case_law_stub_links_to_scholar(monkeypatch):
    import main

    monkeypatch.setattr(
        main.neptune,
        "get_document",
        lambda doc_id: {
            "title": "Some Case v. Other",
            "authority_level": 3,
            "citation": "200 Wis. 2d 1",
            "source_url": "https://docs.legis.wisconsin.gov/document/courts/200%20Wis.%202d%201",
            "s3_key": "raw/case-law-200-wis-2d-1/case-law-200-wis-2d-1.txt",
        },
    )

    chunks = [
        {
            "doc_id": "case-law-200-wis-2d-1",
            "text": "stub summary text",
            "s3_key": "raw/case-law-200-wis-2d-1/case-law-200-wis-2d-1.txt",
        }
    ]
    docs = main._build_rag_documents(chunks, {"case-law-200-wis-2d-1"})
    assert len(docs) == 1
    card = docs[0]
    assert card.s3_key is None
    assert card.source_url.startswith("http://scholar.google.com/scholar?")
    assert "q=200%20Wis.%202d%201" in card.source_url


def test_build_rag_documents_case_law_multi_chunk_preserves_scholar_link(monkeypatch):
    import main

    monkeypatch.setattr(
        main.neptune,
        "get_document",
        lambda doc_id: {
            "title": "Multi-Chunk Case",
            "authority_level": 3,
            "citation": "300 Wis. 2d 100",
            "source_url": "https://docs.legis.wisconsin.gov/document/courts/300",
            "s3_key": "raw/case-law-300-wis-2d-100/case-law-300-wis-2d-100.txt",
        },
    )
    chunks = [
        {"doc_id": "case-law-300-wis-2d-100", "text": "first chunk",
         "s3_key": "raw/case-law-300-wis-2d-100/case-law-300-wis-2d-100.txt"},
        {"doc_id": "case-law-300-wis-2d-100", "text": "second chunk",
         "s3_key": "raw/case-law-300-wis-2d-100/case-law-300-wis-2d-100.txt"},
    ]
    docs = main._build_rag_documents(chunks, {"case-law-300-wis-2d-100"})
    assert len(docs) == 1
    card = docs[0]
    assert card.s3_key is None
    assert card.source_url.startswith("http://scholar.google.com/scholar?")
    assert "q=300%20Wis.%202d%20100" in card.source_url
    assert "first chunk" in card.content
    assert "second chunk" in card.content


def _import_main_with_real_faq_models(_sys):
    """Re-import `main` with real FAQ / FAQResource models (incl. source_url).

    `main` does `from step_function_types.models import FAQ, FAQResource` at
    import time, so the only way to give it real Pydantic classes is to set them
    on the mocked module and re-import main fresh — the same idiom the rest of
    this file uses.
    """
    class FakeFAQ(pydantic.BaseModel):
        faq_id: str
        question: str
        answer: str
        source_url: str | None = None

    class FakeFAQResource(pydantic.BaseModel):
        faqs: list[FakeFAQ]

    _sys.modules["step_function_types.models"].FAQ = FakeFAQ
    _sys.modules["step_function_types.models"].FAQResource = FakeFAQResource

    with patch.dict(os.environ, {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
    }):
        if "main" in _sys.modules:
            del _sys.modules["main"]
        with patch("boto3.client"), patch("boto3.resource"), \
             patch("neptune_client.NeptuneClient"):
            import main
    return main


def test_build_faq_resource_attaches_source_url(monkeypatch):
    import sys as _sys
    main = _import_main_with_real_faq_models(_sys)

    # Fake DynamoDB table returning a URL for the normalized question.
    class FakeTable:
        def __init__(self):
            self.requested = []

        def get_item(self, Key):
            self.requested.append(Key["normalized_question"])
            if Key["normalized_question"] == "is x a y":
                return {"Item": {"normalized_question": "is x a y",
                                 "source_url": "https://revenue.wi.gov/x"}}
            return {}

    fake = FakeTable()
    monkeypatch.setattr(main, "FAQ_URL_TABLE", "FaqUrlTable")
    monkeypatch.setattr(main, "_faq_url_table", lambda: fake)

    results = [
        {"text": "Q: Is X a Y?\nA: Yes it is.", "source_uri": "s3://b/faq_1.txt"},
        {"text": "Q: Unknown thing?\nA: No idea.", "source_uri": "s3://b/faq_2.txt"},
    ]
    resource = main._build_faq_resource(results)
    by_q = {f.question: f.source_url for f in resource.faqs}
    assert by_q["Is X a Y?"] == "https://revenue.wi.gov/x"
    assert by_q["Unknown thing?"] is None


def test_build_faq_resource_tolerates_missing_table(monkeypatch):
    import sys as _sys
    main = _import_main_with_real_faq_models(_sys)

    monkeypatch.setattr(main, "FAQ_URL_TABLE", "")  # not configured
    results = [{"text": "Q: Anything?\nA: Sure.", "source_uri": "s3://b/faq_1.txt"}]
    resource = main._build_faq_resource(results)
    assert resource.faqs[0].source_url is None


def test_save_chat_history_persists_faq_source_url(monkeypatch):
    # Use the same real-FAQ-model import idiom the other FAQ tests use.
    import sys as _sys
    main = _import_main_with_real_faq_models(_sys)
    FAQ = main.FAQ
    FAQResource = main.FAQResource

    captured = {}

    class FakeTable:
        def put_item(self, Item):
            captured["item"] = Item

    monkeypatch.setattr(main, "CHAT_HISTORY_TABLE", "ChatHistory")
    monkeypatch.setattr(main.dynamodb_resource, "Table", lambda name: FakeTable())

    faq_resource = FAQResource(faqs=[
        FAQ(faq_id="faq_1", question="Q?", answer="A.",
            source_url="https://revenue.wi.gov/x"),
    ])
    main.save_chat_history(
        "s1", "q1", "the query", "the answer",
        rag_documents=None, faq_resource=faq_resource,
    )

    faq_res = [r for r in captured["item"]["resources"] if r["type"] == "faq"]
    assert faq_res[0]["data"]["sourceUrl"] == "https://revenue.wi.gov/x"


def test_save_chat_history_persists_authority_level_and_content(monkeypatch):
    """Resumed sessions must keep their authority badge and preview text.

    save_chat_history rebuilds document resources field-by-field; if it
    omits authorityLevel, replayed cards render with no/incorrect badge,
    and omitting content blanks the card preview. Both must round-trip.
    """
    import sys as _sys

    main = _import_main_with_real_faq_models(_sys)
    RAGDocument = main.RAGDocument

    captured = {}

    class FakeTable:
        def put_item(self, Item):
            captured["item"] = Item

    monkeypatch.setattr(main, "CHAT_HISTORY_TABLE", "ChatHistory")
    monkeypatch.setattr(main.dynamodb_resource, "Table", lambda name: FakeTable())

    doc = RAGDocument(
        document_id="case-law-x",
        title="Lowe's Home Centers, LLC v. City of Delavan",
        content="The opinion text preview.",
        source="379 Wis. 2d 141",
        authority_level=3,
    )
    main.save_chat_history(
        "s1", "q1", "the query", "the answer",
        rag_documents=[doc], faq_resource=None,
    )

    doc_res = [r for r in captured["item"]["resources"] if r["type"] == "document"]
    assert doc_res[0]["data"]["authorityLevel"] == 3
    assert doc_res[0]["data"]["content"] == "The opinion text preview."


def test_faq_question_normalizer_matches_seed_script():
    """main._normalize_faq_question must stay byte-identical to the seed/extract
    scripts' faq_url_map.normalize_question.

    The two are independent copies (the lambda bundle can't import from scripts/),
    coupled only by docstrings. A FAQ's source URL is keyed on the normalized
    question, so if these ever drift, lookups at query time silently miss and the
    "View on revenue.wi.gov" link disappears. This guard fails the moment one
    copy is edited without the other.
    """
    import sys as _sys

    main = _import_main_with_real_faq_models(_sys)
    from scripts.graphrag.faq_url_map import normalize_question

    cases = [
        "What is the homestead credit?",
        "Already clean.",
        "  leading and trailing  ",
        "a\tb\n c",
        "Non\xa0breaking\xa0space?",
        "zero​width",
        "﻿BOM prefix",
        "",
        "MiXeD CaSe Question???",
        "trailing dots...",
        "ends with q mark and dot?.",
    ]
    for text in cases:
        assert main._normalize_faq_question(text) == normalize_question(text), (
            f"normalizer drift on {text!r}"
        )
