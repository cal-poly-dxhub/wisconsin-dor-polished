import asyncio
import json
import logging
import os
from typing import Any

import boto3
import pydantic
from boto3.dynamodb.types import TypeDeserializer
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    RAGDocument,
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

model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")
dynamodb_client = boto3.client("dynamodb")


def get_retrieval_config() -> dict:
    """
    Load retrieval configuration from DynamoDB.

    Returns:
        Dictionary with retrieval config values, or defaults if not found
    """
    default_config = {
        "numRAGResults": 10,
        "numFAQResults": 5,
        "maxDocumentsToClient": 5,
        "sourceIdPriority": []
    }

    if not model_config_table_name:
        logger.warning("MODEL_CONFIG_TABLE_NAME not set, using defaults")
        return default_config

    try:
        response = dynamodb_client.get_item(
            TableName=model_config_table_name,
            Key={"id": {"S": "retrievalConfig"}}
        )

        item = response.get("Item")
        if not item:
            logger.warning("retrievalConfig not found in DynamoDB, using defaults")
            return default_config

        deserializer = TypeDeserializer()
        config = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Remove the 'id' field
        config.pop("id", None)

        logger.info(f"Loaded retrieval config from DynamoDB: {config}")
        return config
    except Exception as e:
        logger.error(f"Error loading retrieval config from DynamoDB: {e}", exc_info=True)
        return default_config


def process_event(event: dict) -> StreamResourcesJob:
    """
    Parses the input event.
    """
    try:
        return StreamResourcesJob.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing event: {e}")
        raise ValidationError() from e


def reorder_and_filter_documents(documents: list[RAGDocument], config: dict) -> list[RAGDocument]:
    """
    Reorder documents based on source_id priority list and apply top-k filtering.

    Documents with source_ids in the priority list appear first, in the order specified.
    Documents not in the priority list appear after, maintaining their original order.
    Then applies top-k filtering based on maxDocumentsToClient.

    Args:
        documents: List of RAGDocument objects to reorder
        config: Retrieval configuration dictionary

    Returns:
        Reordered and filtered list of RAGDocument objects
    """
    source_id_priority = config.get("sourceIdPriority", [])
    max_documents = int(config.get("maxDocumentsToClient", 0))  # Convert Decimal to int

    if not source_id_priority:
        # No priority list configured, documents stay in original order
        reordered = documents
        logger.info("No source_id priority configured, keeping original order")
    else:
        # Create a mapping of source_id to priority index (lower is higher priority)
        priority_map = {source_id: idx for idx, source_id in enumerate(source_id_priority)}

        # Separate documents into prioritized and non-prioritized groups
        prioritized_docs = []
        other_docs = []

        for doc in documents:
            if doc.source_id and doc.source_id in priority_map:
                prioritized_docs.append(doc)
            else:
                other_docs.append(doc)

        # Sort prioritized documents by their priority index
        prioritized_docs.sort(key=lambda doc: priority_map.get(doc.source_id, float('inf')))

        # Return prioritized documents first, followed by others
        reordered = prioritized_docs + other_docs

        logger.info(f"Reordered {len(documents)} documents: {len(prioritized_docs)} prioritized, {len(other_docs)} others")

    # Apply top-k filtering if configured
    if max_documents > 0 and len(reordered) > max_documents:
        filtered = reordered[:max_documents]
        logger.info(f"Filtered documents from {len(reordered)} to top {max_documents}")
        return filtered

    return reordered


async def _stream_resources_async(job: StreamResourcesJob, ws_connect: WebSocketServer):
    """
    Takes a job defining documents to send and streams a message with appropriate schema
    over WebSocket.
    """
    documents_message: DocumentsMessage | None = None
    faq_message: FAQMessage | None = None

    if job.documents:
        documents_resource = DocumentResource.model_validate(job.documents)

        # Load retrieval config
        config = get_retrieval_config()

        # Reorder and filter documents based on source_id priority and max documents
        reordered_documents = reorder_and_filter_documents(documents_resource.documents, config)

        source_documents = [
            SourceDocument(
                document_id=doc.document_id,
                title=doc.title,
                content=doc.content,
                source=doc.source,
                source_id=doc.source_id,
            )
            for doc in reordered_documents
        ]
        documents_message = DocumentsMessage(
            query_id=job.query_id,
            content=DocumentsContent(
                documents=source_documents,
            ),
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
                    )
                    for faq in faq_resource.faqs
                ]
            ),
        )

    try:
        if documents_message:
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
