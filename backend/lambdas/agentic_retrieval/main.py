"""
Agentic Retrieval Lambda: replaces both classifier and retrieval Lambdas
for the GraphRAG path.

Runs Claude's agentic loop with Neptune-backed tools:
1. Receives a UserQuery (query, query_id, session_id)
2. Claude decides which tools to call (vector_search, get_neighbors, etc.)
3. Tools execute against Neptune Analytics
4. Loop continues until Claude calls the 'answer' tool
5. Returns a RetrieveResult with documents + response for streaming
"""

import asyncio
import itertools
import logging
import os
import time
from typing import Any

import boto3
import pydantic
from case_law_handling import is_case_law_stub
from case_opinion import citation_to_raw_slug, fetch_case_opinion
from chat_history import get_chat_history, save_chat_history
from faq_handling import (
    build_cited_faq_resource,
    build_faq_resource,
    faq_search_direct,
)
from neptune_client import NeptuneClient
from prompt import SYSTEM_PROMPT
from rag_documents import build_rag_documents
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    RAGDocument,
    UserQuery,
)
from tools import TOOL_DEFINITIONS, execute_tool
from trace_summaries import (
    build_tool_call_summary,
    build_tool_result_summary,
    discovery_summary,
    summarize_assistant_message,
    summarize_bedrock_response,
)
from tracing import (
    emit_trace,
    filter_metadata,
    log_agent_event,
    query_log_fields,
)
from websocket_utils.utils import get_ws_connection_from_session

MAX_TURNS = 10

# Bedrock KB relevance scores range 0-1. A well-matched FAQ typically scores
# 0.75+; loosely related hits land around 0.6-0.7. At/above this threshold the
# FAQ is treated as the primary source of truth for the answer, while the
# agentic loop still runs to supplement it with citable graph evidence.
FAQ_SCORE_THRESHOLD = 0.70

# Cap on FAQ entries passed to downstream synthesis. Prevents low-relevance
# hits below the top match from diluting the prompt.
MAX_FAQS = 3

# Neptune node labels that aren't user-citable documents.
_NON_DOCUMENT_LABELS = frozenset({"Chunk", "Topic", "Framework"})


def _is_document_neighbor(neighbor: dict) -> bool:
    """True when this neighbor node represents a citable document."""
    labels = neighbor.get("labels") or []
    return not any(label in _NON_DOCUMENT_LABELS for label in labels)


logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

REGION = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
neptune = NeptuneClient()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "")

AGENTIC_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
LOG_AGENT_TRACE = os.environ.get("LOG_AGENT_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))
EMIT_AGENT_TRACE = os.environ.get("EMIT_AGENT_TRACE", "true").lower() == "true"


# --- Convenience wrappers that bind module-level config to extracted functions ---

def _log(event: str, level: int = logging.INFO, **fields: Any) -> None:
    log_agent_event(event, level, log_enabled=LOG_AGENT_TRACE, max_chars=LOG_MAX_TEXT_CHARS, **fields)


def _query_fields(query: str) -> dict[str, Any]:
    return query_log_fields(query, log_query_text=LOG_QUERY_TEXT, max_chars=LOG_MAX_TEXT_CHARS)


def _emit(ws_server, trace_seq, **kwargs) -> None:
    emit_trace(ws_server, trace_seq, emit_enabled=EMIT_AGENT_TRACE, max_chars=LOG_MAX_TEXT_CHARS, **kwargs)


def _tool_result_summary(tool_name: str, result: dict) -> dict:
    return build_tool_result_summary(tool_name, result, neptune)


def _assistant_summary(message: dict) -> dict[str, Any]:
    return summarize_assistant_message(message, LOG_MAX_TEXT_CHARS)


def process_event(event: dict) -> UserQuery:
    """Parse input event."""
    try:
        return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


def run_agentic_loop(
    query: str,
    chat_history: list[dict] | None = None,
    *,
    query_id: str = "",
    session_id: str = "",
    request_id: str = "",
    ws_server=None,
    trace_seq=None,
) -> tuple[str, list[str], list[RAGDocument], FAQResource | None]:
    """Run Claude's agentic loop against Neptune.

    Turn 0 is hardcoded: run `faq_search` with the verbatim user query. The
    result is always seeded into the conversation and Claude is handed off to
    graph work.

    Returns:
        (answer_text, cited_doc_ids, rag_documents, faq_resource)
    """
    chat_history = chat_history or []
    if trace_seq is None:
        trace_seq = itertools.count(1).__next__
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []
    discovery: dict[str, str] = {}
    fetched_opinions: dict[str, dict] = {}

    # Turn 0 refinement: rewrite context-dependent follow-ups against history.
    search_query = query
    if chat_history:
        _emit(
            ws_server,
            trace_seq,
            query_id=query_id,
            kind="tool_call",
            turn=0,
            payload={
                "toolName": "refine_query",
                "summary": "",
                "status": "pending",
            },
        )
        refine_result = execute_tool(
            "refine_query", {"query": query}, neptune, chat_history=chat_history
        )
        refined = refine_result.get("refined_query") or query
        if refined and refined != query:
            logger.info(
                f"Turn-0 refine: '{query[:80]}' -> '{refined[:80]}'"
            )
            search_query = refined
        refine_summary = _tool_result_summary("refine_query", refine_result)
        _emit(
            ws_server,
            trace_seq,
            query_id=query_id,
            kind="tool_result",
            turn=0,
            payload={
                "toolName": "refine_query",
                "status": refine_summary["status"],
                "summary": refine_summary["summary_text"],
                "docIds": refine_summary["doc_ids"],
                "docTitles": refine_summary["doc_titles"],
                "metadata": filter_metadata(refine_summary["metadata"]),
            },
            dev_payload={"raw": refine_summary["raw"]},
        )

    trace_context = {
        "query_id": query_id,
        "session_id": session_id,
        "request_id": request_id,
    }
    loop_started = time.perf_counter()
    _log(
        "agent_loop_start",
        **trace_context,
        model_id=AGENTIC_MODEL_ID,
        max_turns=MAX_TURNS,
        **_query_fields(query),
    )
    _emit(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_start",
        payload={"maxTurns": MAX_TURNS},
    )

    # Turn 0: deterministic FAQ search.
    _emit(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="tool_call",
        turn=0,
        payload={
            "toolName": "faq_search",
            "summary": build_tool_call_summary("faq_search", {"query": search_query}),
            "status": "pending",
        },
    )
    faq_result = faq_search_direct(search_query, neptune, execute_tool)
    faq_entries = faq_result.get("faqs", [])
    top_score = faq_entries[0].get("score", 0.0) if faq_entries else 0.0
    logger.info(
        f"FAQ turn-0: {len(faq_entries)} hits, top_score={top_score:.3f}, "
        f"threshold={FAQ_SCORE_THRESHOLD}"
    )
    faq_summary = _tool_result_summary("faq_search", faq_result)
    _emit(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="tool_result",
        turn=0,
        payload={
            "toolName": "faq_search",
            "status": faq_summary["status"],
            "summary": faq_summary["summary_text"],
            "docIds": faq_summary["doc_ids"],
            "docTitles": faq_summary["doc_titles"],
            "metadata": filter_metadata(faq_summary["metadata"]),
        },
        dev_payload={"raw": faq_summary["raw"]},
    )

    high_confidence_faq: FAQResource | None = None
    if top_score >= FAQ_SCORE_THRESHOLD:
        high_confidence_faq = build_faq_resource(faq_entries)
        if high_confidence_faq:
            logger.info(
                f"FAQ high-confidence match (score={top_score:.3f}): treating "
                f"{len(high_confidence_faq.faqs)} FAQ(s) as primary source; "
                "graph traversal will supplement the answer"
            )
        else:
            logger.warning(
                "FAQ score cleared threshold but no entries parsed; "
                "loop will treat FAQs as ordinary context"
            )

    # Prepend prior turns so Claude can resolve pronouns and short follow-ups.
    messages: list[dict] = []
    for turn in chat_history:
        messages.append({"role": "user", "content": [{"text": turn["query"]}]})
        messages.append(
            {"role": "assistant", "content": [{"text": turn["answer"]}]}
        )

    # Seed the conversation with the FAQ result as if Claude had called it.
    seed_tool_use_id = "faq_search_turn0"
    messages.extend([
        {"role": "user", "content": [{"text": query}]},
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": seed_tool_use_id,
                        "name": "faq_search",
                        "input": {"query": search_query},
                    }
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": seed_tool_use_id,
                        "content": [{"json": faq_result}],
                    }
                }
            ],
        },
    ])

    if high_confidence_faq:
        faq_ids = [faq.faq_id for faq in high_confidence_faq.faqs]
        messages.append({
            "role": "user",
            "content": [{
                "text": (
                    f"The seeded faq_search returned a high-confidence match "
                    f"(top score {top_score:.2f} ≥ {FAQ_SCORE_THRESHOLD:.2f}, "
                    f"FAQ id(s): {', '.join(faq_ids)}). Treat the FAQ Q/A as "
                    "the PRIMARY source of truth for your answer. Still run "
                    "vector_search and graph traversal to find authoritative "
                    "documents (statutes, admin rules, WPAM) that support, "
                    "ground, or add useful detail to what the FAQ says — but "
                    "do NOT contradict the FAQ. Use graph results to "
                    "supplement and cite, not to replace. Include the FAQ "
                    "id(s) above in your final cited_doc_ids alongside any "
                    "supporting docs you retrieve."
                )
            }],
        })

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        turn_number = turn + 1
        _log(
            "agent_turn_start",
            **trace_context,
            turn=turn_number,
            max_turns=MAX_TURNS,
            message_count=len(messages),
            discovered_doc_count=len(all_doc_ids),
            accumulated_chunk_count=len(all_chunks),
            discovery=discovery_summary(discovery),
        )

        if turn == 7:
            warning = (
                "You are running low on turns. Call the answer tool NOW with your "
                "best answer from the context gathered so far."
            )
            messages.append({
                "role": "user",
                "content": [{"text": warning}],
            })
            _log(
                "agent_turn_budget_warning_injected",
                **trace_context,
                turn=turn_number,
            )

        converse_started = time.perf_counter()
        try:
            response = bedrock.converse(
                modelId=AGENTIC_MODEL_ID,
                messages=messages,
                system=[{"text": SYSTEM_PROMPT}],
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
            )
        except Exception as exc:
            _log(
                "bedrock_converse_error",
                logging.ERROR,
                **trace_context,
                turn=turn_number,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        converse_latency_ms = round((time.perf_counter() - converse_started) * 1000)

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)
        stop_reason = response.get("stopReason", "")
        asst_summary = _assistant_summary(assistant_message)
        _log(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            **summarize_bedrock_response(response),
            assistant=asst_summary,
        )
        if asst_summary["text_preview"]:
            _emit(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="reasoning",
                turn=turn_number,
                payload={"text": asst_summary["text_preview"]},
            )

        tool_uses = [
            block for block in assistant_message["content"]
            if "toolUse" in block
        ]

        if not tool_uses:
            text_blocks = [
                block["text"] for block in assistant_message["content"]
                if "text" in block
            ]
            answer = "\n".join(text_blocks)
            if stop_reason == "max_tokens" and answer:
                answer = answer + "\n\n_(Response may be incomplete)_"
            _log(
                "agent_final_text_without_answer_tool",
                **trace_context,
                turn=turn_number,
                stop_reason=stop_reason,
                answer_chars=len(answer),
            )
            break

        tool_results = []
        for tool_use in tool_uses:
            tool = tool_use["toolUse"]
            tool_name = tool["name"]
            tool_input = tool["input"]
            tool_use_id = tool["toolUseId"]

            _log(
                "agent_tool_call",
                **trace_context,
                turn=turn_number,
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                tool_input=tool_input,
            )
            _emit(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="tool_call",
                turn=turn_number,
                payload={
                    "toolName": tool_name,
                    "summary": build_tool_call_summary(tool_name, tool_input),
                    "status": "pending",
                },
                dev_payload={
                    "toolInput": tool_input,
                    "toolUseId": tool_use_id,
                },
            )

            tool_started = time.perf_counter()
            try:
                result = execute_tool(
                    tool_name, tool_input, neptune, chat_history=chat_history
                )
            except Exception as exc:
                _log(
                    "agent_tool_error",
                    logging.ERROR,
                    **trace_context,
                    turn=turn_number,
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    tool_latency_ms=round((time.perf_counter() - tool_started) * 1000),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                result = {
                    "error": f"{tool_name} failed: {type(exc).__name__}: {exc}"
                }
            tool_latency_ms = round((time.perf_counter() - tool_started) * 1000)

            if tool_name == "vector_search" and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                        discovery.setdefault(doc_id, "vector-search")
                    all_chunks.append(chunk)
                for neighbors in result.get("graph_context", {}).values():
                    for neighbor in neighbors:
                        neighbor_id = neighbor.get("id")
                        if neighbor_id and _is_document_neighbor(neighbor):
                            all_doc_ids.add(neighbor_id)
                            discovery.setdefault(neighbor_id, "graph-neighbor")

            if tool_name == "search_document" and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                        discovery.setdefault(doc_id, "search-document")
                    all_chunks.append(chunk)

            if tool_name == "get_neighbors" and "neighbors" in result:
                for n in result["neighbors"]:
                    if n.get("id") and _is_document_neighbor(n):
                        all_doc_ids.add(n["id"])
                        discovery.setdefault(n["id"], "graph-neighbor")

            if tool_name == "get_document":
                doc = result.get("document")
                if doc and doc.get("id"):
                    all_doc_ids.add(doc["id"])
                    discovery[doc["id"]] = "fetched"

            if tool_name == "list_framework_docs":
                for d in result.get("documents", []):
                    if d.get("id"):
                        all_doc_ids.add(d["id"])
                        discovery.setdefault(d["id"], "framework-list")

            if tool_name == "fetch_case_opinion" and result.get("found"):
                citation = result.get("citation", "")
                if citation:
                    stub_doc_id = citation_to_raw_slug(citation)
                    fetched_opinions[stub_doc_id] = {
                        "citation": citation,
                        "raw_key": result.get("raw_key", ""),
                        "text": result.get("text", ""),
                        "scholar_url": result.get("scholar_url", ""),
                    }
                    all_doc_ids.add(stub_doc_id)
                    discovery[stub_doc_id] = "opinion-fetched"

            tool_result_summary = _tool_result_summary(tool_name, result)
            _log(
                "agent_tool_result",
                **trace_context,
                turn=turn_number,
                tool_use_id=tool_use_id,
                tool_latency_ms=tool_latency_ms,
                discovered_doc_count=len(all_doc_ids),
                accumulated_chunk_count=len(all_chunks),
                discovery=discovery_summary(discovery),
                tool_result_summary=tool_result_summary["raw"],
            )
            if tool_name != "answer":
                result_metadata = dict(tool_result_summary["metadata"])
                if tool_latency_ms is not None:
                    result_metadata["latencyMs"] = tool_latency_ms
                result_metadata = filter_metadata(result_metadata)
                _emit(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
                    kind="tool_result",
                    turn=turn_number,
                    payload={
                        "toolName": tool_name,
                        "status": tool_result_summary["status"],
                        "summary": tool_result_summary["summary_text"],
                        "docIds": tool_result_summary["doc_ids"],
                        "docTitles": tool_result_summary["doc_titles"],
                        "metadata": result_metadata,
                    },
                    dev_payload={
                        "raw": tool_result_summary["raw"],
                        "toolLatencyMs": tool_latency_ms,
                    },
                )

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = set(result.get("cited_doc_ids", []))
                cited_chunks = [
                    c for c in all_chunks if c.get("doc_id") in cited
                ]
                cited_discovery = {
                    k: v for k, v in discovery.items() if k in cited
                }
                for cid in cited:
                    cited_discovery.setdefault(cid, "fetched")
                cited_opinions = {
                    k: v for k, v in fetched_opinions.items() if k in cited
                }
                # Backfill: fetch opinions for cited case-law stubs the
                # agent didn't explicitly fetch_case_opinion for.
                _OPINION_BACKFILL_CAP = 3
                unfetched_stubs = [
                    cid for cid in cited
                    if is_case_law_stub(cid) and cid not in cited_opinions
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
                rag_docs = build_rag_documents(
                    cited_chunks, cited, cited_discovery, cited_opinions,
                    neptune_client=neptune,
                )
                cited_faq_resource = (
                    high_confidence_faq
                    or build_cited_faq_resource(faq_entries, cited)
                )
                _log(
                    "agent_loop_complete",
                    **trace_context,
                    terminal_reason="answer_tool",
                    turns_used=turn_number,
                    elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
                    answer_chars=len(answer),
                    cited_doc_count=len(cited),
                    discovered_doc_count=len(all_doc_ids),
                    rag_document_count=len(rag_docs),
                    faq_count=len(cited_faq_resource.faqs) if cited_faq_resource else 0,
                    discovery=discovery_summary(cited_discovery),
                )
                _emit(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
                    kind="loop_complete",
                    payload={
                        "terminalReason": "answer_tool",
                        "turnsUsed": turn_number,
                        "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                        "citedDocCount": len(cited),
                    },
                )
                return answer, list(cited), rag_docs, cited_faq_resource

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                }
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        last_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                for block in msg.get("content", []):
                    if "text" in block and block["text"]:
                        last_text = block["text"]
                        break
                if last_text:
                    break
        if last_text:
            answer = last_text + "\n\n_(Response incomplete: turn budget reached)_"
        else:
            answer = (
                "I was unable to find a complete answer within the allowed number "
                "of search steps. Please try rephrasing your question."
            )
        _log(
            "agent_turn_budget_exhausted",
            logging.WARNING,
            **trace_context,
            answer_chars=len(answer),
            discovered_doc_count=len(all_doc_ids),
            accumulated_chunk_count=len(all_chunks),
            discovery=discovery_summary(discovery),
        )

    rag_docs = build_rag_documents(
        all_chunks, all_doc_ids, discovery, fetched_opinions,
        neptune_client=neptune,
    )
    _log(
        "agent_loop_complete",
        **trace_context,
        terminal_reason="assistant_text_or_fallback",
        elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
        answer_chars=len(answer),
        discovered_doc_count=len(all_doc_ids),
        rag_document_count=len(rag_docs),
        discovery=discovery_summary(discovery),
    )
    _emit(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_complete",
        payload={
            "terminalReason": "assistant_text_or_fallback",
            "turnsUsed": MAX_TURNS,
            "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
            "citedDocCount": len(all_doc_ids),
        },
    )
    return answer, list(all_doc_ids), rag_docs, high_confidence_faq


def handler(event: dict, context) -> dict[str, Any]:
    """
    Lambda handler. Processes a UserQuery via agentic retrieval,
    returns a RetrieveResult compatible with the existing Step Functions flow.
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

        answer, cited_doc_ids, rag_documents, faq_resource = run_agentic_loop(
            user_query.query,
            chat_history=chat_history,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            request_id=request_id,
            ws_server=ws_server,
            trace_seq=trace_seq,
        )

        save_chat_history(
            session_id,
            user_query.query_id,
            user_query.query,
            answer,
            rag_documents=rag_documents,
            faq_resource=faq_resource,
        )

        documents = DocumentResource(documents=rag_documents)
        _log(
            "agentic_retrieval_response_ready",
            request_id=request_id,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            answer_chars=len(answer),
            cited_doc_count=len(cited_doc_ids),
            rag_document_count=len(rag_documents),
            faq_count=len(faq_resource.faqs) if faq_resource else 0,
        )

        return {
            "successful": True,
            "query": user_query.query,
            "query_id": user_query.query_id,
            "session_id": user_query.session_id,
            "faqs": faq_resource.model_dump() if faq_resource else None,
            "documents": documents.model_dump(),
        }

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
