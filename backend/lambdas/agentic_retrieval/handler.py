"""Agentic Retrieval Lambda entry point: two-phase GraphRAG retrieval.

Phase A (Research Loop, loop/phase_a.py):
    Claude decides which tools to call against Neptune Analytics until it
    calls prepare_answer (cited docs + answer plan, no answer text).

Phase B (Answer Stream, loop/phase_b.py):
    Build resource cards from cited_doc_ids, send them over WebSocket, then
    stream the answer token-by-token via converse_stream() with no tools.
"""

import asyncio
import itertools
import json
import logging
from typing import Any

import pydantic
from case_law import fetch_case_opinion, is_case_law_stub
from chat_history import get_chat_history, save_chat_history
from faq import build_cited_faq_resource
from loop.phase_a import run_agentic_loop
from loop.phase_b import apply_persona, build_answer_context, stream_answer
from prompt import ANSWER_STREAM_SYSTEM_PROMPT
from rag_documents import build_rag_documents
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import UserQuery
from streaming.delivery import send_resources, send_resources_and_finalize
from tracing.runtime import emit as _emit
from tracing.runtime import log_event as _log
from tracing.runtime import query_fields as _query_fields
from websocket_utils.models import ChoicesContent, ChoicesMessage
from websocket_utils.utils import get_ws_connection_from_session

from config import AGENTIC_MODEL_ID, ENABLE_DISAMBIGUATION, RAW_BUCKET, bedrock, neptune

logger = logging.getLogger(__name__)


def process_event(event: dict) -> UserQuery:
    """Parse input event."""
    try:
        return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


def handler(event: dict, context) -> dict[str, Any]:
    """
    Lambda handler. Processes a UserQuery via two-phase agentic retrieval:
    Phase A: Research loop (non-streaming converse calls with tools)
    Phase B: Answer streaming (converse_stream with no tools)
    """
    session_id: str | None = None
    request_id = getattr(context, "aws_request_id", "") if context else ""

    try:
        user_query = process_event(event)
        session_id = user_query.session_id
        _log(
            "agentic_retrieval_request_received",
            request_id=request_id,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            **_query_fields(user_query.query),
        )

        chat_history = get_chat_history(session_id)

        ws_server = None
        if session_id:
            try:
                ws_server = get_ws_connection_from_session(session_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not look up WebSocket connection; trace events will be skipped",
                    exc_info=True,
                )
                ws_server = None
        trace_seq = itertools.count(1).__next__

        _emit(
            ws_server,
            trace_seq,
            query_id=user_query.query_id,
            kind="phase",
            payload={"phase": "request_received"},
        )
        if chat_history:
            _emit(
                ws_server,
                trace_seq,
                query_id=user_query.query_id,
                kind="phase",
                payload={
                    "phase": "history_loaded",
                    "historyTurns": len(chat_history),
                },
            )

        # Pre-loop disambiguation: if enabled, check whether the query is
        # a generic property assessment question that needs classification.
        if ENABLE_DISAMBIGUATION:
            from disambiguation import (
                CLARIFICATION_QUESTION,
                PROPERTY_TYPE_CHOICES,
                should_disambiguate,
            )

            needs_disambiguation = should_disambiguate(user_query.query, chat_history)
            _emit(
                ws_server,
                trace_seq,
                query_id=user_query.query_id,
                kind="phase",
                payload={
                    "phase": "generality_classified",
                    "label": "Query needs clarification on property type"
                    if needs_disambiguation
                    else "Query is specific enough to proceed",
                    "result": "disambiguate" if needs_disambiguation else "proceed",
                },
            )

            if needs_disambiguation:
                _log(
                    "disambiguation_short_circuit",
                    request_id=request_id,
                    query_id=user_query.query_id,
                    session_id=user_query.session_id,
                    **_query_fields(user_query.query),
                )
                answer = CLARIFICATION_QUESTION
                if ws_server:
                    send_resources_and_finalize(
                        ws_server,
                        user_query.query_id,
                        answer,
                        rag_documents=[],
                        faq_resource=None,
                    )
                    choices_msg = ChoicesMessage(
                        query_id=user_query.query_id,
                        content=ChoicesContent(choices=PROPERTY_TYPE_CHOICES),
                    )
                    data = json.dumps(
                        {"streamId": "choices", "body": choices_msg.model_dump(by_alias=True)}
                    )
                    ws_server.client.post_to_connection(
                        ConnectionId=ws_server.connection_id, Data=data
                    )
                save_chat_history(
                    session_id,
                    user_query.query_id,
                    user_query.query,
                    answer,
                )
                return {"successful": True}

        # === Phase A: Research Loop ===
        persona = user_query.persona
        result = run_agentic_loop(
            user_query.query,
            chat_history=chat_history,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            request_id=request_id,
            ws_server=ws_server,
            trace_seq=trace_seq,
        )

        if result.fallback_answer is not None:
            # Edge case: clarify tool, turn budget exhausted, or model responded
            # with text instead of calling prepare_answer. No Phase B needed.
            answer = result.fallback_answer
            rag_documents = build_rag_documents(
                result.all_chunks,
                result.all_doc_ids,
                result.discovery,
                result.fetched_opinions,
                neptune_client=neptune,
            )
            faq_resource = result.high_confidence_faq or build_cited_faq_resource(
                result.faq_entries, result.all_doc_ids
            )

            _log(
                "agentic_retrieval_response_ready",
                request_id=request_id,
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                answer_chars=len(answer),
                cited_doc_count=len(result.cited_doc_ids),
                rag_document_count=len(rag_documents),
                faq_count=len(faq_resource.faqs) if faq_resource else 0,
                phase="fallback",
            )

            if ws_server and result.connection_alive:
                try:
                    send_resources_and_finalize(
                        ws_server,
                        user_query.query_id,
                        answer,
                        rag_documents,
                        faq_resource,
                    )
                except Exception:
                    logger.info("WebSocket connection lost during finalize; answer saved to DB")

        else:
            # === Normal path: Phase B streaming ===
            # 1. Build resources from cited_doc_ids
            cited = set(result.cited_doc_ids)
            cited_chunks = [c for c in result.all_chunks if c.get("doc_id") in cited]
            cited_discovery = {k: v for k, v in result.discovery.items() if k in cited}
            for cid in cited:
                cited_discovery.setdefault(cid, "fetched")

            # Opinion backfill for case law stubs not already fetched
            cited_opinions = {k: v for k, v in result.fetched_opinions.items() if k in cited}
            _OPINION_BACKFILL_CAP = 3
            unfetched_stubs = [
                cid for cid in cited if is_case_law_stub(cid) and cid not in cited_opinions
            ]
            for stub_id in unfetched_stubs[:_OPINION_BACKFILL_CAP]:
                try:
                    doc_info = neptune.get_document(stub_id)
                    citation = (doc_info or {}).get("citation", "")
                    if not citation:
                        continue
                    opinion = fetch_case_opinion(citation, raw_bucket=RAW_BUCKET)
                    if opinion.get("found"):
                        cited_opinions[stub_id] = {
                            "citation": citation,
                            "raw_key": opinion.get("raw_key", ""),
                            "text": opinion.get("text", ""),
                            "scholar_url": opinion.get("scholar_url", ""),
                        }
                        cited_discovery[stub_id] = "opinion-backfill"
                        logger.info(
                            f"Opinion backfill: fetched {citation} "
                            f"({len(opinion.get('text', ''))} chars)"
                        )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        f"Opinion backfill failed for {stub_id}",
                        exc_info=True,
                    )

            rag_documents = build_rag_documents(
                cited_chunks,
                cited,
                cited_discovery,
                cited_opinions,
                neptune_client=neptune,
            )
            faq_resource = result.high_confidence_faq or build_cited_faq_resource(
                result.faq_entries, cited
            )

            _log(
                "agentic_retrieval_response_ready",
                request_id=request_id,
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                cited_doc_count=len(cited),
                rag_document_count=len(rag_documents),
                faq_count=len(faq_resource.faqs) if faq_resource else 0,
                phase="streaming",
            )

            answer = ""  # Will be populated by streaming or fallback
            if ws_server and result.connection_alive:
                ws_connection_alive = [result.connection_alive]
                try:
                    # 2. Send resource cards over WebSocket
                    send_resources(ws_server, user_query.query_id, rag_documents, faq_resource)

                    # 3. Stream answer (Phase B)
                    answer_context = build_answer_context(
                        user_query.query,
                        cited_chunks,
                        cited,
                        cited_discovery,
                        cited_opinions,
                        result.answer_plan,
                        chat_history=chat_history,
                        neptune_client=neptune,
                    )
                    answer = stream_answer(
                        ws_server,
                        user_query.query_id,
                        answer_context,
                        trace_seq,
                        ws_connection_alive,
                        persona=persona,
                    )
                except Exception as phase_b_exc:
                    logger.error(
                        "Phase B failed | exc_type=%s exc=%s",
                        type(phase_b_exc).__name__,
                        phase_b_exc,
                        exc_info=True,
                    )
                    if not answer:
                        try:
                            if not answer_context:
                                answer_context = build_answer_context(
                                    user_query.query,
                                    cited_chunks,
                                    cited,
                                    cited_discovery,
                                    cited_opinions,
                                    result.answer_plan,
                                    chat_history=chat_history,
                                    neptune_client=neptune,
                                )
                            response = bedrock.converse(
                                modelId=AGENTIC_MODEL_ID,
                                messages=[{"role": "user", "content": [{"text": answer_context}]}],
                                system=[
                                    {"text": apply_persona(ANSWER_STREAM_SYSTEM_PROMPT, persona)}
                                ],
                                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
                            )
                            text_blocks = [
                                block["text"]
                                for block in response["output"]["message"]["content"]
                                if "text" in block
                            ]
                            answer = "\n".join(text_blocks)
                            logger.info(
                                "Phase B fallback: generated answer via non-streaming converse()"
                            )
                        except Exception as fallback_exc:
                            logger.error(f"Phase B non-streaming fallback failed: {fallback_exc}")
                            answer = "(Answer generation failed — please retry)"
            else:
                # No WebSocket — generate answer without streaming for DB save
                answer_context = build_answer_context(
                    user_query.query,
                    cited_chunks,
                    cited,
                    cited_discovery,
                    cited_opinions,
                    result.answer_plan,
                    chat_history=chat_history,
                    neptune_client=neptune,
                )
                try:
                    response = bedrock.converse(
                        modelId=AGENTIC_MODEL_ID,
                        messages=[{"role": "user", "content": [{"text": answer_context}]}],
                        system=[{"text": apply_persona(ANSWER_STREAM_SYSTEM_PROMPT, persona)}],
                        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
                    )
                    text_blocks = [
                        block["text"]
                        for block in response["output"]["message"]["content"]
                        if "text" in block
                    ]
                    answer = "\n".join(text_blocks)
                except Exception as exc:
                    logger.error(f"Phase B non-streaming fallback failed: {exc}")
                    answer = "(Answer generation failed — please retry)"

        save_chat_history(
            session_id,
            user_query.query_id,
            user_query.query,
            answer,
            rag_documents=rag_documents,
            faq_resource=faq_resource,
            trace_log=result.trace_log,
        )

        return {"successful": True}

    except Exception as e:
        _log(
            "agentic_retrieval_failed",
            logging.ERROR,
            request_id=request_id,
            session_id=session_id,
            error_type=type(e).__name__,
            error=str(e),
        )
        logger.error(f"Agentic retrieval failed: {e}", exc_info=True)
        if session_id:
            asyncio.run(report_error(e, session_id=session_id))

        return {"successful": False}
