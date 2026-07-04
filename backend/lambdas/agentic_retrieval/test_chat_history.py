"""Tests for the chat_history module."""

import sys
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic")


class FakeRAGDocument(pydantic.BaseModel):
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


class FakeFAQ(pydantic.BaseModel):
    faq_id: str
    question: str
    answer: str
    source_url: str | None = None


class FakeFAQResource(pydantic.BaseModel):
    faqs: list[FakeFAQ]


sys.modules["step_function_types.models"].RAGDocument = FakeRAGDocument
sys.modules["step_function_types.models"].FAQ = FakeFAQ
sys.modules["step_function_types.models"].FAQResource = FakeFAQResource

import chat_history


class TestGetChatHistory:
    def test_returns_empty_when_unconfigured(self):
        with patch.object(chat_history, "CHAT_HISTORY_TABLE", ""):
            assert chat_history.get_chat_history("sess-1") == []

    def test_returns_empty_when_no_session(self):
        assert chat_history.get_chat_history("") == []

    def test_reads_from_gsi(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"query": "q1", "answer": "a1", "timestamp": "2025-01-01"},
                {"query": "q2", "answer": "a2", "timestamp": "2025-01-02"},
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.object(chat_history, "dynamodb_resource", mock_resource), \
             patch.object(chat_history, "CHAT_HISTORY_TABLE", "SomeTable"):
            history = chat_history.get_chat_history("sess-1")

        assert len(history) == 2
        assert history[0] == {"query": "q1", "answer": "a1"}
        assert history[1] == {"query": "q2", "answer": "a2"}
        kwargs = mock_table.query.call_args.kwargs
        assert kwargs["IndexName"] == "sessionIdKey"
        assert kwargs["ScanIndexForward"] is True

    def test_caps_at_max_turns(self):
        items = [
            {"query": f"q{i}", "answer": f"a{i}", "timestamp": f"2025-01-{i:02d}"}
            for i in range(1, 11)
        ]
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": items}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.object(chat_history, "dynamodb_resource", mock_resource), \
             patch.object(chat_history, "CHAT_HISTORY_TABLE", "SomeTable"), \
             patch.object(chat_history, "MAX_HISTORY_TURNS", 3):
            history = chat_history.get_chat_history("sess-1")

        assert len(history) == 3
        assert [h["query"] for h in history] == ["q8", "q9", "q10"]


class TestSaveChatHistory:
    def test_noop_when_unconfigured(self):
        with patch.object(chat_history, "CHAT_HISTORY_TABLE", ""):
            chat_history.save_chat_history("s1", "q1", "query", "answer")

    def test_persists_faq_source_url(self):
        captured = {}

        class FakeTable:
            def put_item(self, Item):
                captured["item"] = Item

        mock_resource = MagicMock()
        mock_resource.Table.return_value = FakeTable()

        faq_resource = FakeFAQResource(faqs=[
            FakeFAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x"),
        ])

        with patch.object(chat_history, "dynamodb_resource", mock_resource), \
             patch.object(chat_history, "CHAT_HISTORY_TABLE", "ChatHistory"):
            chat_history.save_chat_history(
                "s1", "q1", "the query", "the answer",
                faq_resource=faq_resource,
            )

        faq_res = [r for r in captured["item"]["resources"] if r["type"] == "faq"]
        assert faq_res[0]["data"]["sourceUrl"] == "https://revenue.wi.gov/x"

    def test_persists_authority_level_and_content(self):
        captured = {}

        class FakeTable:
            def put_item(self, Item):
                captured["item"] = Item

        mock_resource = MagicMock()
        mock_resource.Table.return_value = FakeTable()

        doc = FakeRAGDocument(
            document_id="case-law-x",
            title="Some Case",
            content="The opinion text.",
            source="379 Wis. 2d 141",
            authority_level=3,
        )

        with patch.object(chat_history, "dynamodb_resource", mock_resource), \
             patch.object(chat_history, "CHAT_HISTORY_TABLE", "ChatHistory"):
            chat_history.save_chat_history(
                "s1", "q1", "the query", "the answer",
                rag_documents=[doc],
            )

        doc_res = [r for r in captured["item"]["resources"] if r["type"] == "document"]
        assert doc_res[0]["data"]["authorityLevel"] == 3
        assert doc_res[0]["data"]["content"] == "The opinion text."
