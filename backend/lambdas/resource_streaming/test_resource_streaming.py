import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "layers"))

from main import handler
from step_function_types.errors import UnknownResourceType
from step_function_types.models import (
    StreamResourcesResult,
)
from websocket_utils.batching import (
    MAX_DOC_CONTENT_BYTES as _MAX_DOC_CONTENT_BYTES,
    WS_FRAME_BUDGET_BYTES as _WS_FRAME_BUDGET_BYTES,
    batch_documents_for_ws as _batch_documents_for_ws,
    truncate_doc_content as _truncate_doc_content,
)
from websocket_utils.models import SourceDocument


class TestResourceStreamingHandler:
    @patch("main._stream_resources_async")
    @patch("main.get_ws_connection_from_session")
    def test_handler_with_document_resource_success(
        self, mock_get_ws_connection, mock_stream_resources
    ):
        # Setup mocks
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.return_value = None

        # Test event
        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {
                "documents": [
                    {
                        "document_id": "doc-001",
                        "title": "Deployment Guide",
                        "content": "This is a deployment guide.",
                        "source": "https://example.com/deploy",
                    },
                    {
                        "document_id": "doc-002",
                        "title": "Configuration Guide",
                        "content": "This is a configuration guide.",
                        "source": "https://example.com/config",
                    },
                ]
            },
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()

        # Verify result structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    @patch("main._stream_resources_async")
    @patch("main.get_ws_connection_from_session")
    def test_handler_with_faq_resource_success(self, mock_get_ws_connection, mock_stream_resources):
        # Setup mocks
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.return_value = None

        # Test event
        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "faq",
            "content": {"question": "Example Question?", "answer": "This is an example answer."},
        }

        result = handler(event, MagicMock())

        # Verify mocks were called correctly
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()

        # Verify result structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    def test_handler_process_event_error(self):
        event = {"invalid": "data"}

        result = handler(event, MagicMock())

        # Verify error handling - process_event will fail with invalid data

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("main.get_ws_connection_from_session")
    def test_handler_no_websocket_connection(self, mock_get_ws_connection):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_get_ws_connection.return_value = None  # No WebSocket connection found

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("main.get_ws_connection_from_session")
    def test_handler_websocket_connection_error(self, mock_get_ws_connection):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_get_ws_connection.side_effect = Exception("WebSocket connection failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is False

    @patch("main.report_error")
    @patch("main._stream_resources_async")
    @patch("main.get_ws_connection_from_session")
    def test_handler_streaming_error(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = Exception("Streaming failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert (
            stream_result.successful is True
        )  # Note: this lambda returns successful=True even on streaming errors

    @patch("main.report_error")
    @patch("main._stream_resources_async")
    @patch("main.get_ws_connection_from_session")
    def test_handler_unknown_resource_type(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = UnknownResourceType()

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    @patch("main.report_error")
    @patch("main._stream_resources_async")
    @patch("main.get_ws_connection_from_session")
    def test_handler_report_error_failure(
        self, mock_get_ws_connection, mock_stream_resources, mock_report_error
    ):
        mock_query_id = "test-query-123"
        mock_session_id = "test-session-456"

        mock_ws_connection = MagicMock()
        mock_ws_connection.send_json.side_effect = Exception("Error reporting failed")

        mock_get_ws_connection.return_value = mock_ws_connection
        mock_stream_resources.side_effect = Exception("Streaming failed")

        event = {
            "query_id": mock_query_id,
            "session_id": mock_session_id,
            "resource_type": "documents",
            "content": {"documents": []},
        }

        result = handler(event, MagicMock())

        # Verify error handling
        mock_get_ws_connection.assert_called_once_with(mock_session_id)
        mock_stream_resources.assert_called_once()
        mock_report_error.assert_called_once()

        # Verify error response structure
        stream_result = StreamResourcesResult(**result)
        assert stream_result.successful is True

    def test_source_document_carries_s3_key_and_pages(self):
        """resource_streaming forwards s3_key/start_page/end_page to the WebSocket payload."""
        import asyncio
        from unittest.mock import AsyncMock

        from main import _stream_resources_async
        from step_function_types.models import (
            DocumentResource,
            RAGDocument,
            StreamResourcesJob,
        )

        rag_doc = RAGDocument(
            document_id="doc-001",
            title="WPAM",
            content="some content",
            source="https://www.revenue.wi.gov/wpam.pdf",
            source_url="https://www.revenue.wi.gov/wpam.pdf",
            s3_key="raw/wpam/wpam.pdf",
            start_page=12,
            end_page=14,
        )
        job = StreamResourcesJob(
            query_id="q-1",
            session_id="s-1",
            documents=DocumentResource(documents=[rag_doc]),
        )

        sent_messages = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

        asyncio.run(_stream_resources_async(job, ws))

        assert len(sent_messages) == 1
        sent_doc = sent_messages[0].content.documents[0]
        assert sent_doc.s3_key == "raw/wpam/wpam.pdf"
        assert sent_doc.start_page == 12
        assert sent_doc.end_page == 14

    def test_source_document_carries_edition_year(self):
        """resource_streaming forwards a WPAM edition_year onto the WebSocket payload.

        Regression guard: edition_year is declared on RAGDocument and on the
        frontend Zod/Document types, but if websocket_utils.SourceDocument omits
        the field (or the construction in _stream_resources_async forgets to copy
        it) the value is silently dropped at the wire boundary and never reaches
        the browser. Assert it both survives and aliases to `editionYear`.
        """
        import asyncio
        from unittest.mock import AsyncMock

        from main import _stream_resources_async
        from step_function_types.models import (
            DocumentResource,
            RAGDocument,
            StreamResourcesJob,
        )

        rag_doc = RAGDocument(
            document_id="wpam-2025-ch7",
            title="WPAM Chapter 7 (2025)",
            content="some content",
            source="https://www.revenue.wi.gov/wpam-2025.pdf",
            source_url="https://www.revenue.wi.gov/wpam-2025.pdf",
            s3_key="raw/wpam-2025/wpam-2025.pdf",
            start_page=12,
            end_page=14,
            edition_year=2025,
        )
        job = StreamResourcesJob(
            query_id="q-1",
            session_id="s-1",
            documents=DocumentResource(documents=[rag_doc]),
        )

        sent_messages = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

        asyncio.run(_stream_resources_async(job, ws))

        assert len(sent_messages) == 1
        sent_doc = sent_messages[0].content.documents[0]
        assert sent_doc.edition_year == 2025
        # CamelCaseModel aliasing -> editionYear on the wire.
        assert sent_doc.model_dump(by_alias=True)["editionYear"] == 2025

    def test_source_document_edition_year_defaults_none_for_non_wpam(self):
        """Non-WPAM docs carry no edition_year; the field defaults to None."""
        import asyncio
        from unittest.mock import AsyncMock

        from main import _stream_resources_async
        from step_function_types.models import (
            DocumentResource,
            RAGDocument,
            StreamResourcesJob,
        )

        rag_doc = RAGDocument(
            document_id="statutes-wi-statute-ch70",
            title="Wis. Stat. ch. 70",
            content="statute text",
            source="https://docs.legis.wisconsin.gov/statutes/70",
            source_url="https://docs.legis.wisconsin.gov/statutes/70",
        )
        job = StreamResourcesJob(
            query_id="q-2",
            session_id="s-1",
            documents=DocumentResource(documents=[rag_doc]),
        )

        sent_messages = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

        asyncio.run(_stream_resources_async(job, ws))

        sent_doc = sent_messages[0].content.documents[0]
        assert sent_doc.edition_year is None


class TestDocumentBatching:
    def _doc(self, idx: int, content_len: int) -> SourceDocument:
        return SourceDocument(
            document_id=f"doc-{idx:03d}",
            title=f"Document {idx}",
            content="x" * content_len,
            source=None,
            source_url=None,
            discovery_tag="vector-search",
        )

    def test_batches_all_docs_in_one_frame_when_under_budget(self):
        docs = [self._doc(i, 1000) for i in range(5)]
        batches = _batch_documents_for_ws(docs, query_id="q-1")

        assert len(batches) == 1
        assert len(batches[0].content.documents) == 5

    def test_splits_into_multiple_frames_when_over_budget(self):
        docs = [self._doc(i, 50_000) for i in range(3)]
        batches = _batch_documents_for_ws(docs, query_id="q-2")

        assert len(batches) >= 2
        total_docs = sum(len(b.content.documents) for b in batches)
        assert total_docs == 3

        for batch in batches:
            frame_bytes = len(batch.model_dump_json(by_alias=True).encode("utf-8"))
            assert frame_bytes < 128_000

    def test_oversize_single_doc_is_truncated(self):
        docs = [self._doc(0, _MAX_DOC_CONTENT_BYTES * 2)]
        batches = _batch_documents_for_ws(docs, query_id="q-3")

        assert len(batches) == 1
        doc = batches[0].content.documents[0]
        assert len(doc.content.encode("utf-8")) <= _MAX_DOC_CONTENT_BYTES + 100
        assert "truncated" in doc.content

    def test_every_batch_stays_under_ws_budget(self):
        # Realistic failing case from prod: one 48 KB doc + ten ~7 KB docs ~= 133 KB.
        docs = [self._doc(0, 48_000)] + [self._doc(i + 1, 7_000) for i in range(10)]
        batches = _batch_documents_for_ws(docs, query_id="q-4")

        assert len(batches) >= 2
        for batch in batches:
            payload_bytes = len(batch.model_dump_json(by_alias=True).encode("utf-8"))
            assert payload_bytes <= _WS_FRAME_BUDGET_BYTES + 1_000

    def test_truncate_doc_content_preserves_short_docs(self):
        doc = SourceDocument(
            document_id="d",
            title="t",
            content="short",
            source=None,
            source_url=None,
            discovery_tag="",
        )
        assert _truncate_doc_content(doc) is doc

    def test_empty_docs_list_produces_no_batches(self):
        assert _batch_documents_for_ws([], query_id="q-5") == []


def test_faq_models_carry_source_url():
    from step_function_types.models import FAQ as SfnFAQ
    from websocket_utils.models import FAQ as WsFAQ

    sfn = SfnFAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x")
    assert sfn.source_url == "https://revenue.wi.gov/x"

    ws = WsFAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x")
    # CamelCaseModel aliasing -> sourceUrl on the wire.
    dumped = ws.model_dump(by_alias=True)
    assert dumped["sourceUrl"] == "https://revenue.wi.gov/x"

    # Backward compatible: omitting source_url is allowed and defaults to None.
    assert SfnFAQ(faq_id="faq_2", question="Q?", answer="A.").source_url is None


def test_stream_faq_message_includes_source_url():
    import asyncio
    from unittest.mock import AsyncMock

    from main import _stream_resources_async
    from step_function_types.models import FAQResource, StreamResourcesJob

    job = StreamResourcesJob(
        query_id="q1",
        session_id="s1",
        documents=None,
        faqs=FAQResource(
            faqs=[
                {
                    "faq_id": "faq_1",
                    "question": "Q?",
                    "answer": "A.",
                    "source_url": "https://revenue.wi.gov/x",
                }
            ]
        ),
    )

    sent_messages = []
    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

    asyncio.run(_stream_resources_async(job, ws))

    faq_msgs = [m for m in sent_messages if getattr(m, "response_type", None) == "faq"]
    assert len(faq_msgs) == 1
    assert faq_msgs[0].content.faqs[0].source_url == "https://revenue.wi.gov/x"
