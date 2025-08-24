import asyncio
import logging
import os
from typing import Any

import pydantic
from step_function_types.errors import UnknownResourceType, ValidationError, report_error
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


def process_event(event: dict) -> StreamResourcesJob:
    """
    Parses the input event.
    """
    try:
        return StreamResourcesJob.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing event: {e}")
        raise ValidationError() from e


async def _stream_resources_async(job: StreamResourcesJob, ws_connect: WebSocketServer):
    """
    Takes a job defining documents to send and streams a message with appropriate schema
    over WebSocket.
    """

    match job.resource_type:
        case "documents":
            documents_resource = DocumentResource.model_validate(job.content)
            source_documents = [
                SourceDocument(
                    document_id=doc.document_id,
                    title=doc.title,
                    content=doc.content,
                    source=doc.source,
                )
                for doc in documents_resource.documents
            ]
            message = DocumentsMessage(
                query_id=job.query_id,
                content=DocumentsContent(
                    documents=source_documents,
                ),
            )
        case "faq":
            faq_resource = FAQResource.model_validate(job.content)
            message = FAQMessage(
                query_id=job.query_id,
                content=FAQContent(
                    faq=FAQ(
                        question=faq_resource.question,
                        answer=faq_resource.answer,
                    )
                ),
            )
        case _:
            logger.error(f"Unknown resource type: {job.resource_type}")
            raise UnknownResourceType()

    try:
        await ws_connect.send_json(message)
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
