import asyncio
import logging
import os
from typing import Any

import pydantic
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    StreamResourcesJob,
    StreamResourcesResult,
)
from websocket_utils.errors import WebSocketError
from websocket_utils.models import (
    FAQ,
    DocumentsContent,
    DocumentsMessage,
    FAQContent,
    FAQMessage,
    SourceDocument,
)
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

# API Gateway WebSocket caps a single PostToConnection frame at 128 KB.
# We batch documents into multiple frames with headroom for the JSON envelope
# and for UTF-8 escape expansion.
_WS_FRAME_BUDGET_BYTES = 100_000
# Cap any individual document's content so a single oversized chunk can't
# exceed the per-frame budget on its own. Citation cards only need enough
# content for a preview; the title + source URL carry the click-through.
_MAX_DOC_CONTENT_BYTES = 60_000
_TRUNCATION_SUFFIX = "\n\n… [content truncated]"


def process_event(event: dict) -> StreamResourcesJob:
    """
    Parses the input event.
    """
    try:
        return StreamResourcesJob.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing event: {e}")
        raise ValidationError() from e


def _truncate_doc_content(doc: SourceDocument) -> SourceDocument:
    """Cap a single document's content at _MAX_DOC_CONTENT_BYTES (UTF-8)."""
    content = doc.content or ""
    encoded = content.encode("utf-8")
    if len(encoded) <= _MAX_DOC_CONTENT_BYTES:
        return doc
    # Trim on a character boundary, not mid-codepoint.
    truncated = encoded[:_MAX_DOC_CONTENT_BYTES].decode("utf-8", errors="ignore")
    return doc.model_copy(update={"content": truncated + _TRUNCATION_SUFFIX})


def _batch_documents_for_ws(
    docs: list[SourceDocument],
    query_id: str,
) -> list[DocumentsMessage]:
    """Pack documents into DocumentsMessage batches under the frame budget.

    The API Gateway WebSocket frame limit is 128 KB. Sending all docs at
    once for large result sets raises a 413, which drops every citation.
    The frontend appends on each 'documents' message, so multiple frames
    merge seamlessly on the client.
    """
    batches: list[DocumentsMessage] = []
    current: list[SourceDocument] = []
    current_size = 0

    for doc in docs:
        safe_doc = _truncate_doc_content(doc)
        doc_bytes = len(safe_doc.model_dump_json(by_alias=True).encode("utf-8"))

        # +1 for list separator; negligible but keeps accounting honest.
        projected = current_size + doc_bytes + (1 if current else 0)
        if current and projected > _WS_FRAME_BUDGET_BYTES:
            batches.append(
                DocumentsMessage(
                    query_id=query_id,
                    content=DocumentsContent(documents=current),
                )
            )
            current = [safe_doc]
            current_size = doc_bytes
        else:
            current.append(safe_doc)
            current_size = projected

    if current:
        batches.append(
            DocumentsMessage(
                query_id=query_id,
                content=DocumentsContent(documents=current),
            )
        )

    return batches


async def _stream_resources_async(job: StreamResourcesJob, ws_connect: WebSocketServer):
    """
    Takes a job defining documents to send and streams a message with appropriate schema
    over WebSocket.
    """
    documents_messages: list[DocumentsMessage] = []
    faq_message: FAQMessage | None = None

    if job.documents:
        documents_resource = DocumentResource.model_validate(job.documents)
        source_documents = [
            SourceDocument(
                document_id=doc.document_id,
                title=doc.title,
                content=doc.content,
                source=doc.source,
                source_url=doc.source_url,
                discovery_tag=doc.discovery_tag,
                authority_level=doc.authority_level,
                s3_key=doc.s3_key,
                start_page=doc.start_page,
                end_page=doc.end_page,
                edition_year=doc.edition_year,
            )
            for doc in documents_resource.documents
        ]
        documents_messages = _batch_documents_for_ws(source_documents, job.query_id)
        if len(documents_messages) > 1:
            logger.info(
                f"Splitting {len(source_documents)} documents into "
                f"{len(documents_messages)} WebSocket frames to stay under "
                f"the {_WS_FRAME_BUDGET_BYTES} byte budget"
            )

    if job.faqs:
        faq_resource = FAQResource.model_validate(job.faqs)
        faq_message = FAQMessage(
            query_id=job.query_id,
            content=FAQContent(
                faqs=[
                    FAQ(
                        faq_id=faq.faq_id,
                        question=faq.question,
                        answer=faq.answer,
                        source_url=faq.source_url,
                    )
                    for faq in faq_resource.faqs
                ]
            ),
        )

    try:
        for documents_message in documents_messages:
            await ws_connect.send_json(documents_message)
        if faq_message:
            await ws_connect.send_json(faq_message)
    except WebSocketError as e:
        logger.error(f"Error while streaming resources over WebSocket: {e}", exc_info=True)
        return


def handler(event: dict, context) -> dict[str, Any]:
    """
    Processes a StreamResourcesJob, creates a WebSocket connector to the
    appropriate session, and streams the resources received from the job
    to the client.
    """

    ws_connect: WebSocketServer | None = None
    job: StreamResourcesJob | None = None

    try:
        job = process_event(event)
    except Exception as e:
        # Don't stream error; WebSocket connection's not available
        logger.error(f"Error while processing event: {e}", exc_info=True)
        return StreamResourcesResult(successful=False).model_dump()

    if not job:
        logger.error("No job retrieved from event.")
        return StreamResourcesResult(successful=False).model_dump()

    try:
        ws_connect = get_ws_connection_from_session(job.session_id)
    except Exception as e:
        # Don't stream error; WebSocket connection's not available
        logger.error(f"Error while getting WebSocket connection from session: {e}", exc_info=True)
        return StreamResourcesResult(successful=False).model_dump()

    if not ws_connect:
        logger.error(f"No WebSocket connection found for session {job.session_id}")
        return StreamResourcesResult(successful=False).model_dump()

    try:
        asyncio.run(_stream_resources_async(job, ws_connect))
        return StreamResourcesResult(successful=True).model_dump()
    except Exception as e:
        # WebSocket connection's up; report the error
        logger.error(f"Error while streaming resources over WebSocket: {e}", exc_info=True)
        asyncio.run(report_error(e, ws_connect=ws_connect, session_id=job.session_id))
        return StreamResourcesResult(successful=True).model_dump()
