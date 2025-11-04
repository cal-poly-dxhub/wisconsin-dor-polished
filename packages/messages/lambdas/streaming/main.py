import asyncio
import datetime
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import boto3
import pydantic
from bedrock_utils import ModelConfig, call_bedrock_converse, get_model_config_from_dynamo
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    GenerateResponseJob,
    GenerateResponseResult,
)
from websocket_utils.models import AnswerEventType
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

dynamodb = boto3.resource("dynamodb")
chat_history_table = os.environ.get("CHAT_HISTORY_TABLE_NAME")


def log_chat_history(
    session_id: str,
    query: str,
    answer: str,
    faqs: FAQResource | None,
    documents: DocumentResource | None,
):
    if not chat_history_table:
        logger.error(
            "CHAT_HISTORY_TABLE_NAME environment variable not set; skipping chat history logging."
        )
        return

    table = dynamodb.Table(chat_history_table)
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()

    faqs_data = [faq.model_dump() for faq in faqs.faqs] if faqs else []
    documents_data = [doc.model_dump() for doc in documents.documents] if documents else []

    try:
        table.put_item(
            Item={
                "session_id": session_id,
                "timestamp": timestamp,
                "query": query,
                "answer": answer,
                "faqs": json.dumps(faqs_data),
                "documents": json.dumps(documents_data),
            }
        )
        logger.info(f"Chat history saved for session {session_id}")
    except Exception as e:
        logger.error(f"Failed to save chat history: {e}", exc_info=True)


def fragment_message(message: str) -> AsyncGenerator[str]:
    # TODO: retreive when model responses are implemented.
    async def _gen():
        i = 0
        while i < len(message):
            frag_len = min(3, len(message) - i)
            yield message[i : i + frag_len]
            i += frag_len

    return _gen()


async def generate_response_async(
    query: str,
    session_id: str,
    faqs: FAQResource | None = None,
    documents: DocumentResource | None = None,
) -> AsyncGenerator[str]:
    n_docs = len(documents.documents) if documents else 0
    n_faqs = len(faqs.faqs) if faqs else 0
    logger.info(f"Generating response for {n_docs} documents and {n_faqs} FAQs.")

    config: ModelConfig = get_model_config_from_dynamo("ragResponse")
    faqs_text = "\n".join(
        [f"FAQ question: {faq.question}\nFAQ answer: {faq.answer}" for faq in faqs.faqs]
        if faqs
        else "No FAQs available."
    )
    documents_text = (
        "\n".join([d.model_dump_json() for d in documents.documents])
        if documents
        else "No documents available."
    )
    text = config.prompt.format(
        query=query,
        documents=documents_text,
        faqs=faqs_text,
    )

    logger.info(f"Generating response using config: {config}")
    response = call_bedrock_converse(text, config)

    fragments = []
    async for fragment in response:
        fragments.append(fragment)
        yield fragment

    answer = "".join(fragments)
    log_chat_history(session_id, query, answer, faqs, documents)


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
        response = generate_response_async(job.query, job.session_id, job.faqs, job.documents)
        asyncio.run(_stream_message_async(ws_connect, response, job.query_id))
        return GenerateResponseResult(successful=True).model_dump()
    except Exception as e:
        # WebSocket connection's up; report the error
        logger.error(f"Error occurred in response streaming lambda: {e}", exc_info=True)
        asyncio.run(report_error(e, ws_connect=ws_connect, session_id=job.session_id))
        return GenerateResponseResult(successful=False).model_dump()
