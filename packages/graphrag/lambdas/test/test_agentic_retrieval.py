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
    """_build_opinion_card carries the stable raw_key on s3_key; source_url
    is None when raw_key is present (resolver mints presigned at click time)."""
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

            assert card.s3_key == "raw/case-law-123-wis-2d-45/123-wis-2d-45.txt"
            assert card.start_page is None
            assert card.end_page is None
            # When raw_key is present, source_url is None (resolver handles it).
            assert card.source_url is None
            assert card.authority_level == 3
            assert card.discovery_tag == "opinion-fetched"


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
    """The merge constructor must carry the s3 reference forward; otherwise the
    fetched-opinion case-law cards (which set source_url=None) become
    non-clickable after collapse."""
    with patch("main.boto3"), patch("main.NeptuneClient"):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import _collapse_case_law_by_title

        docs = {
            "case-law-foo-1": MockRAGDocument(
                document_id="case-law-foo-1-abc",
                title="Foo v. Bar",
                content="primary opinion text",
                source_url=None,
                s3_key="raw/case-law-foo/foo.txt",
                start_page=None,
                end_page=None,
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
            "case-law-foo-2": MockRAGDocument(
                document_id="case-law-foo-2-def",
                title="Foo v. Bar",
                content="parallel citation text",
                source_url=None,
                s3_key="raw/case-law-foo/foo.txt",
                start_page=None,
                end_page=None,
                discovery_tag="opinion-fetched",
                authority_level=3,
            ),
        }

        merged = _collapse_case_law_by_title(docs)
        assert len(merged) == 1
        surviving = next(iter(merged.values()))
        assert surviving.s3_key == "raw/case-law-foo/foo.txt"
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


def test_run_agentic_loop_faq_short_circuit_emits_bracketed_pair(monkeypatch):
    """High-scoring FAQ short-circuit must still emit loop_start + loop_complete
    so the UI can rely on a consistent open/close event pair on both paths.
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

    # High-scoring FAQ triggers short-circuit.
    monkeypatch.setattr(main, "_faq_search_direct", lambda q: {
        "faqs": [{"text": "Q: what is TID\nA: answer", "score": 0.90,
                  "source_uri": "s3://f/faq_1.txt"}],
        "count": 1,
    })

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

    main.run_agentic_loop(
        "what is TID?",
        query_id="q-1",
        session_id="s-1",
        ws_server=mock_ws,
        trace_seq=itertools.count(1).__next__,
    )

    kinds = [m.kind for m in sent]
    # Turn-0 FAQ now emits as a tool_call/tool_result pair (Change 2).
    assert kinds == [
        "loop_start",
        "tool_call",
        "tool_result",
        "loop_complete",
    ]
    assert sent[-1].payload["terminalReason"] == "faq_short_circuit"
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
