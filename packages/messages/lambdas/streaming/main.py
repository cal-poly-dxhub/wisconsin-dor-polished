import asyncio
import datetime
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import boto3
import pydantic
from baml_client import b
from baml_client.types import ChatTurn, Document, RAGResponse
from boto3.dynamodb.types import TypeDeserializer
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    GenerateResponseJob,
    GenerateResponseResult,
    RAGDocument,
)
from websocket_utils.models import (
    AnswerEventType,
    DocumentsContent,
    DocumentsMessage,
    SourceDocument,
)
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
chat_history_table = os.environ.get("CHAT_HISTORY_TABLE_NAME")
model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")


def get_chat_history(session_id: str) -> list[dict[str, str]]:
    if not chat_history_table:
        logger.warning("CHAT_HISTORY_TABLE_NAME not set; returning empty chat history.")
        return []

    table = dynamodb.Table(chat_history_table)

    try:
        response = table.query(
            IndexName="sessionIdKey",
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
            ScanIndexForward=True,
        )
        items = response.get("Items", [])
        history = [{"query": item["query"], "answer": item["answer"]} for item in items]
        logger.info(f"Retrieved {len(history)} chat history items for session {session_id}")
        return history
    except Exception as e:
        logger.error(f"Failed to retrieve chat history: {e}", exc_info=True)
        return []


def log_chat_history(
    session_id: str,
    query_id: str,
    query: str,
    answer: str,
    documents: DocumentResource | None,
):
    if not chat_history_table:
        logger.error(
            "CHAT_HISTORY_TABLE_NAME environment variable not set; skipping chat history logging."
        )
        return

    table = dynamodb.Table(chat_history_table)
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()

    documents_data = [doc.model_dump() for doc in documents.documents] if documents else []

    try:
        table.put_item(
            Item={
                "sessionId": session_id,
                "timestamp": timestamp,
                "queryId": query_id,
                "query": query,
                "answer": answer,
                "documents": json.dumps(documents_data),
            }
        )
        logger.info(f"Chat history saved for session {session_id}")
    except Exception as e:
        logger.error(f"Failed to save chat history: {e}", exc_info=True)


def get_retrieval_config() -> dict:
    """
    Load retrieval configuration from DynamoDB.

    Returns:
        Dictionary with retrieval config values, or defaults if not found
    """
    default_config = {"numRAGResults": 10, "numFAQResults": 5, "sourceIdPriority": []}

    if not model_config_table_name:
        logger.warning("MODEL_CONFIG_TABLE_NAME not set, using defaults")
        return default_config

    try:
        response = dynamodb_client.get_item(
            TableName=model_config_table_name, Key={"id": {"S": "retrievalConfig"}}
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


def convert_faqs_to_documents(faqs: FAQResource | None) -> list[RAGDocument]:
    """
    Convert FAQs to RAGDocuments with source_id='faqs'.

    Args:
        faqs: FAQResource containing list of FAQs

    Returns:
        List of RAGDocuments representing the FAQs
    """
    if not faqs or not faqs.faqs:
        return []

    documents = []
    for faq in faqs.faqs:
        # Format FAQ content to include both question and answer
        content = f"Q: {faq.question}\nA: {faq.answer}"
        documents.append(
            RAGDocument(
                document_id=faq.faq_id,
                title=f"FAQ: {faq.question}",
                content=content,
                source=None,
                source_id="faqs",
            )
        )
    return documents


def mix_and_filter_documents(
    faqs: FAQResource | None,
    documents: DocumentResource | None,
    config: dict,
) -> DocumentResource:
    """
    Mix FAQs and RAG documents and apply source priority reordering.

    FAQs are converted to RAGDocuments with source_id='faqs' and mixed with existing documents.
    Documents are then reordered based on source_id_priority.

    Args:
        faqs: FAQResource containing FAQs to include
        documents: DocumentResource containing RAG documents
        config: Retrieval configuration with sourceIdPriority

    Returns:
        DocumentResource with mixed and reordered documents
    """
    # Convert FAQs to documents
    faq_documents = convert_faqs_to_documents(faqs)

    # Get RAG documents
    rag_documents = documents.documents if documents else []

    # Mix all documents together
    all_documents = faq_documents + rag_documents

    if not all_documents:
        return DocumentResource(documents=[])

    source_id_priority = config.get("sourceIdPriority", [])

    # Apply source priority reordering if configured
    if source_id_priority:
        # Create a mapping of source_id to priority index (lower is higher priority)
        priority_map = {source_id: idx for idx, source_id in enumerate(source_id_priority)}

        # Separate documents into prioritized and non-prioritized groups
        prioritized_docs = []
        other_docs = []

        for doc in all_documents:
            if doc.source_id and doc.source_id in priority_map:
                prioritized_docs.append(doc)
            else:
                other_docs.append(doc)

        # Sort prioritized documents by their priority index
        prioritized_docs.sort(key=lambda doc: priority_map.get(doc.source_id, float("inf")))

        # Combine: prioritized documents first, followed by others
        reordered = prioritized_docs + other_docs
        logger.info(
            f"Reordered {len(all_documents)} documents: {len(prioritized_docs)} prioritized, {len(other_docs)} others"
        )
    else:
        # No priority configured, keep original order
        reordered = all_documents
        logger.info("No source_id priority configured, keeping original order")

    return DocumentResource(documents=reordered)


def fragment_message(message: str) -> AsyncGenerator[str]:
    # TODO: retreive when model responses are implemented.
    async def _gen():
        i = 0
        while i < len(message):
            frag_len = min(3, len(message) - i)
            yield message[i : i + frag_len]
            i += frag_len

    return _gen()


async def stream_documents_to_client(
    ws_connect: WebSocketServer,
    query_id: str,
    documents: DocumentResource,
):
    """
    Stream documents to the client via WebSocket.

    Args:
        ws_connect: WebSocket connection
        query_id: Query ID for the message
        documents: DocumentResource containing mixed and filtered documents
    """
    if not documents or not documents.documents:
        logger.info("No documents to stream to client")
        return

    source_documents = [
        SourceDocument(
            document_id=doc.document_id,
            title=doc.title,
            content=doc.content,
            source=doc.source,
            source_id=doc.source_id,
        )
        for doc in documents.documents
    ]

    documents_message = DocumentsMessage(
        query_id=query_id,
        content=DocumentsContent(
            documents=source_documents,
        ),
    )

    await ws_connect.send_json(documents_message)
    logger.info(f"Streamed {len(source_documents)} documents to client")


class ResponseGenerator:
    """Wrapper for streaming response that captures relevant document IDs."""

    def __init__(self):
        self.relevant_document_ids: list[str] = []

    async def generate(
        self,
        query: str,
        session_id: str,
        query_id: str,
        chat_history: list[dict[str, str]],
        documents: DocumentResource,
    ) -> AsyncGenerator[str]:
        """
        Generate response using mixed and filtered documents via BAML.

        Args:
            query: User query
            session_id: Session ID
            query_id: Query ID
            chat_history: Chat history
            documents: DocumentResource with mixed FAQs and RAG documents

        Yields:
            Answer fragments

        Side effect:
            Sets self.relevant_document_ids after streaming completes
        """
        n_docs = len(documents.documents) if documents else 0
        logger.info(f"Generating response for {n_docs} documents.")

        # Convert chat history to BAML ChatTurn objects
        history_baml = [ChatTurn(query=item["query"], answer=item["answer"]) for item in chat_history]

        # Convert documents to BAML Document objects
        documents_baml = [
            Document(
                document_id=doc.document_id,
                title=doc.title,
                content=doc.content,
                source=doc.source,
                source_id=doc.source_id,
            )
            for doc in (documents.documents if documents else [])
        ]

        logger.info(
            f"Calling BAML GenerateRAGResponse with {len(history_baml)} history items and {len(documents_baml)} documents"
        )

        # Stream response from BAML
        stream = b.stream.GenerateRAGResponse(
            history=history_baml,
            documents=documents_baml,
            query=query,
        )

        # Track what we've already sent to avoid duplication
        # BAML streams return cumulative RAGResponse objects
        previous_length = 0
        full_answer = ""

        async for partial_response in stream:
            if partial_response and partial_response.answer:
                current_answer = partial_response.answer
                # Only yield the new portion of the answer
                if len(current_answer) > previous_length:
                    new_content = current_answer[previous_length:]
                    previous_length = len(current_answer)
                    full_answer = current_answer
                    yield new_content

                # Update relevant_document_ids from latest response
                if partial_response.relevant_document_ids:
                    self.relevant_document_ids = partial_response.relevant_document_ids

        log_chat_history(session_id, query_id, query, full_answer, documents)
        logger.info(
            f"Identified {len(self.relevant_document_ids)} relevant documents: {self.relevant_document_ids}"
        )


async def _stream_message_async(
    ws_connect: WebSocketServer, response: AsyncGenerator[str], query_id: str
):
    """
    Streams message fragments over WebSocket connection.
    """
    await ws_connect.send_json(AnswerEventType(event="start", query_id=query_id))
    await ws_connect.stream_fragments(response, query_id)
    await ws_connect.send_json(AnswerEventType(event="stop", query_id=query_id))


def process_event(event: dict) -> GenerateResponseJob:
    try:
        return GenerateResponseJob.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error while validating event: {e}", exc_info=True)
        raise ValidationError() from e


async def _process_and_stream_async(
    job: GenerateResponseJob,
    ws_connect: WebSocketServer,
    chat_history: list[dict[str, str]],
):
    """
    Generate and stream response, then stream only relevant documents to client.

    Args:
        job: GenerateResponseJob containing query, FAQs, and documents
        ws_connect: WebSocket connection
        chat_history: Chat history
    """
    # Load retrieval config
    config = get_retrieval_config()

    # Mix FAQs and documents, apply priority reordering, and filter
    mixed_documents = mix_and_filter_documents(job.faqs, job.documents, config)

    logger.info(
        f"Mixed and filtered to {len(mixed_documents.documents)} documents for query {job.query_id}"
    )

    # Create response generator that will capture relevant document IDs
    response_gen = ResponseGenerator()
    response_stream = response_gen.generate(
        job.query,
        job.session_id,
        job.query_id,
        chat_history,
        mixed_documents,
    )

    # Stream answer to client
    await _stream_message_async(ws_connect, response_stream, job.query_id)

    # After streaming completes, filter documents to only relevant ones
    relevant_doc_ids = set(response_gen.relevant_document_ids)

    if relevant_doc_ids:
        relevant_documents = DocumentResource(
            documents=[
                doc for doc in mixed_documents.documents
                if doc.document_id in relevant_doc_ids
            ]
        )
        logger.info(
            f"Filtered to {len(relevant_documents.documents)} relevant documents from {len(mixed_documents.documents)} total"
        )
    else:
        # If no relevant documents identified, send all (fallback)
        logger.warning("No relevant documents identified by model, sending all documents")
        relevant_documents = mixed_documents

    # Stream filtered documents to client
    await stream_documents_to_client(ws_connect, job.query_id, relevant_documents)


def handler(event: dict, context) -> dict[str, Any]:
    ws_connect: WebSocketServer | None = None
    job: GenerateResponseJob | None = None

    try:
        job = process_event(event)
    except Exception as e:
        # Don't stream error; WebSocket connection's not available
        logger.error(f"Error while processing event: {e}", exc_info=True)
        return GenerateResponseResult(successful=False).model_dump()

    if not job:
        logger.error("No job retrieved from event.")
        return GenerateResponseResult(successful=False).model_dump()

    try:
        ws_connect = get_ws_connection_from_session(job.session_id)
    except Exception as e:
        # Don't stream error; WebSocket connection's not available
        logger.error(f"Error while getting WebSocket connection from session: {e}", exc_info=True)
        return GenerateResponseResult(successful=False).model_dump()

    if not ws_connect:
        logger.error(f"No WebSocket connection found for session {job.session_id}")
        return GenerateResponseResult(successful=False).model_dump()

    try:
        chat_history = get_chat_history(job.session_id)
    except Exception as e:
        logger.error(f"Error while getting chat history: {e}", exc_info=True)
        return GenerateResponseResult(successful=False).model_dump()

    try:
        asyncio.run(_process_and_stream_async(job, ws_connect, chat_history))
        return GenerateResponseResult(successful=True).model_dump()
    except Exception as e:
        # WebSocket connection's up; report the error
        logger.error(f"Error occurred in response streaming lambda: {e}", exc_info=True)
        asyncio.run(report_error(e, ws_connect=ws_connect, session_id=job.session_id))
        return GenerateResponseResult(successful=False).model_dump()
