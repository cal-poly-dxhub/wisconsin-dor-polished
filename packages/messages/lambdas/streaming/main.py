import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import pydantic
from bedrock_utils import ModelConfig, get_model_config_from_dynamo
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
    resource: FAQResource | DocumentResource | None = None,
) -> AsyncGenerator[str]:
    # TODO: stubbed streaming content; replace with Bedrock-generated content
    match resource:
        case FAQResource(question=question, answer=answer):
            text = f"Question: {question}\nAnswer: {answer}\nQuery: {query}"
            config: ModelConfig = get_model_config_from_dynamo("faqResponse")
            config.prompt = config.prompt.format(
                question=question,
                answer=answer,
                query=query,
            )
        case DocumentResource(documents=documents):
            text = "\n".join([doc.title for doc in documents]) or "No documents"
            config: ModelConfig = get_model_config_from_dynamo("ragResponse")
            config.prompt = config.prompt.format(
                query=query,
                documents="\n".join([d.model_dump_json() for d in documents]),
            )
        case _:
            raise ValueError(f"Unknown resource type: {resource}")

        # TODO: generate a response using the config here

    logger.info(f"Using config: {config}")

    async for fragment in fragment_message(text):
        yield fragment


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
        response: AsyncGenerator[str] = generate_response_async(job.query, job.resources)
        asyncio.run(_stream_message_async(ws_connect, response, job.query_id))
        return GenerateResponseResult(successful=True).model_dump()
    except Exception as e:
        # WebSocket connection's up; report the error
        logger.error(f"Error occurred in response streaming lambda: {e}", exc_info=True)
        asyncio.run(report_error(e, ws_connect=ws_connect, session_id=job.session_id))
        return GenerateResponseResult(successful=False).model_dump()
