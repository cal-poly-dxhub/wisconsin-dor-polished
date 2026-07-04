"""
Agentic Retrieval Lambda: two-phase architecture for the GraphRAG path.

Phase A (Research Loop):
1. Receives a UserQuery (query, query_id, session_id)
2. Claude decides which tools to call (vector_search, get_neighbors, etc.)
3. Tools execute against Neptune Analytics
4. Loop continues until Claude calls prepare_answer(cited_doc_ids, answer_plan)
5. Returns cited docs and research context (no answer text yet)

Phase B (Answer Stream):
1. Build resource cards from cited_doc_ids (presigned URLs, opinion backfill)
2. Send resource cards over WebSocket
3. Call converse_stream() with NO tools — just research context + focused prompt
4. Stream answer text token-by-token over WebSocket
"""

import asyncio
import itertools
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
import pydantic
from case_law_handling import is_case_law_stub
from case_opinion import citation_to_doc_id, fetch_case_opinion
from chat_history import get_chat_history, save_chat_history
from faq_handling import (
    build_cited_faq_resource,
    build_faq_resource,
    faq_search_direct,
)
from neptune_client import NeptuneClient
from prompt import ANSWER_STREAM_SYSTEM_PROMPT, SYSTEM_PROMPT
from rag_documents import build_rag_documents
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    FAQResource,
    RAGDocument,
    UserQuery,
)
from agent_tools import TOOL_DEFINITIONS, execute_tool
from bedrock_messages import converse_with_cache, converse_stream_with_cache
from trace_summaries import (
    build_tool_call_summary,
    build_tool_result_summary,
    discovery_summary,
    summarize_assistant_message,
)
from tracing import (
    emit_trace,
    filter_metadata,
    log_agent_event,
    query_log_fields,
)
from websocket_utils.batching import batch_documents_for_ws
from websocket_utils.models import (
    FAQ,
    AnswerEventType,
    ChoicesContent,
    ChoicesMessage,
    FAQContent,
    FAQMessage,
    FragmentContent,
    FragmentMessage,
    SourceDocument,
)
from websocket_utils.utils import WebSocketServer, get_ws_connection_from_session

MAX_TURNS = 10
_WS_HEARTBEAT_INTERVAL = 15  # seconds between keepalive pings

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


_CHUNK_FIELDS_FOR_MODEL = frozenset({
    "chunk_id", "text", "doc_id", "heading", "subheading",
    "start_page", "end_page", "authority_level", "doc_title",
})

_NEIGHBOR_FIELDS_FOR_MODEL = frozenset({
    "id", "title", "relationship", "labels", "authority_level", "framework_id",
})


def _compact_for_model(result: dict, tool_name: str) -> dict:
    """Strip fields from tool results that the model doesn't need for reasoning.

    Removes null values, internal scores, and verbose metadata to reduce token
    count in the conversation history. The full result is still available in
    all_chunks for citation generation.
    """
    if tool_name not in ("vector_search", "search_document", "get_section"):
        return result

    compacted: dict = {}
    for key, value in result.items():
        if value is None:
            continue
        if key == "chunks":
            compacted["chunks"] = [
                {k: v for k, v in chunk.items()
                 if k in _CHUNK_FIELDS_FOR_MODEL and v is not None}
                for chunk in value
            ]
        elif key == "graph_context":
            compacted["graph_context"] = {
                doc_id: [
                    {k: v for k, v in n.items()
                     if k in _NEIGHBOR_FIELDS_FOR_MODEL and v is not None}
                    for n in neighbors
                ]
                for doc_id, neighbors in value.items()
            }
        elif key in ("score", "pre_dedup_count", "ranking_stats"):
            continue
        else:
            compacted[key] = value
    return compacted


# Property-type detection for disambiguation. When retrieved chunks discuss
# 3+ distinct property classifications and the user didn't specify one, the
# agent is nudged to call the clarify tool.
_PROPERTY_TYPE_KEYWORDS: dict[str, list[str]] = {
    "manufacturing": [
        "manufacturing property", "manufacturing assessment",
        "manufacturing classification", "manufacturer",
    ],
    "agricultural": [
        "agricultural property", "agricultural land", "agricultural assessment",
        "use-value", "use value assessment", "farmland", "agricultural classification",
    ],
    "residential": [
        "residential property", "residential assessment", "residential classification",
        "single-family", "single family home",
    ],
    "commercial": [
        "commercial property", "commercial assessment", "commercial classification",
        "income approach", "commercial valuation",
    ],
    "personal property": [
        "personal property", "business personal property", "statement of personal property",
    ],
}


def _detect_property_type_breadth(chunks: list[dict], doc_ids: set[str]) -> list[str]:
    """Return distinct property-type categories present in chunk text or doc IDs."""
    text_blob = " ".join(c.get("text", "") for c in chunks).lower()
    doc_ids_lower = " ".join(doc_ids).lower()
    search_text = text_blob + " " + doc_ids_lower

    matched: list[str] = []
    for category, keywords in _PROPERTY_TYPE_KEYWORDS.items():
        if any(kw in search_text for kw in keywords):
            matched.append(category)
    return matched


def _query_specifies_property_type(query: str) -> bool:
    """True if the user's query already names a specific property type."""
    q = query.lower()
    type_keywords = [
        "manufacturing", "agricultural", "agriculture", "farmland", "farm land",
        "residential", "commercial", "personal property", "exempt",
    ]
    return any(kw in q for kw in type_keywords)


ENABLE_DISAMBIGUATION = os.environ.get("ENABLE_DISAMBIGUATION", "false").lower() == "true"

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


@dataclass
class AgentLoopResult:
    """Result from Phase A (research loop)."""
    cited_doc_ids: list[str]
    all_chunks: list[dict]
    all_doc_ids: set[str]
    discovery: dict[str, str]
    fetched_opinions: dict[str, dict]
    faq_resource: FAQResource | None
    answer_plan: str
    trace_log: list[dict]
    connection_alive: bool
    # Fallback answer for edge cases (turn budget exhausted, clarify tool)
    fallback_answer: str | None = None
    # High-confidence FAQ entries (from seeded faq_search)
    high_confidence_faq: FAQResource | None = None
    # Raw FAQ entries for cited-faq resolution
    faq_entries: list[dict] = field(default_factory=list)


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
) -> AgentLoopResult:
    """Run Claude's agentic research loop (Phase A) against Neptune.

    Turn 0 is hardcoded: run `faq_search` with the verbatim user query. The
    result is always seeded into the conversation and Claude is handed off to
    graph work.

    The loop exits when the model calls `prepare_answer` (declaring cited docs
    and an answer plan). The actual answer text is NOT generated here — that
    happens in Phase B (streaming).

    Returns an AgentLoopResult dataclass.
    """
    chat_history = chat_history or []
    if trace_seq is None:
        trace_seq = itertools.count(1).__next__
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []
    discovery: dict[str, str] = {}
    fetched_opinions: dict[str, dict] = {}
    trace_log: list[dict] = []
    ws_connection_alive = [True]

    def _record_trace(kind: str, turn: int | None = None, **data) -> None:
        trace_log.append({"kind": kind, "turn": turn, "ts": int(time.time() * 1000), **data})

    def _emit_safe(ws, trace_seq_fn, **kwargs) -> None:
        """Emit trace only if WebSocket connection is still alive."""
        if not ws_connection_alive[0]:
            return
        _emit(ws, trace_seq_fn, **kwargs)

    # Turn 0 refinement: rewrite context-dependent follow-ups against history.
    search_query = query
    if chat_history:
        _emit_safe(
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
        _record_trace(
            "tool_result", turn=0,
            toolName="refine_query",
            status=refine_summary["status"],
            summary=refine_summary["summary_text"],
            metadata=refine_summary["metadata"],
        )
        _emit_safe(
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
    _emit_safe(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_start",
        payload={"maxTurns": MAX_TURNS},
    )

    # Turn 0: deterministic FAQ search.
    _emit_safe(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="tool_call",
        turn=0,
        payload={
            "toolName": "faq_search",
            "summary": build_tool_call_summary("faq_search", {"query": search_query}, neptune),
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
    _record_trace(
        "tool_result", turn=0,
        toolName="faq_search",
        status=faq_summary["status"],
        summary=faq_summary["summary_text"],
        docIds=faq_summary["doc_ids"],
        docTitles=faq_summary["doc_titles"],
        metadata=faq_summary["metadata"],
    )
    _emit_safe(
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
            _emit_safe(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="phase",
                payload={
                    "phase": "faq_transition",
                    "label": "FAQ match found, supplementing with graph search",
                },
            )
        else:
            logger.warning(
                "FAQ score cleared threshold but no entries parsed; "
                "loop will treat FAQs as ordinary context"
            )
            _emit_safe(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="phase",
                payload={
                    "phase": "faq_transition",
                    "label": "No strong FAQ match, searching knowledge graph",
                },
            )
    else:
        _emit_safe(
            ws_server,
            trace_seq,
            query_id=query_id,
            kind="phase",
            payload={
                "phase": "faq_transition",
                "label": "No strong FAQ match, searching knowledge graph",
            },
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
                "You are running low on turns. Call prepare_answer NOW with the "
                "documents gathered so far — list cited_doc_ids and a brief answer_plan."
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

        heartbeat_stop = threading.Event()

        def _heartbeat_loop():
            while not heartbeat_stop.wait(_WS_HEARTBEAT_INTERVAL):
                if not ws_server or not ws_connection_alive[0]:
                    break
                try:
                    ws_server.client.post_to_connection(
                        ConnectionId=ws_server.connection_id,
                        Data=json.dumps({"streamId": "heartbeat", "body": {}}),
                    )
                except Exception:
                    logger.info("WebSocket connection gone during heartbeat")
                    ws_connection_alive[0] = False
                    break

        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        if ws_server and ws_connection_alive[0]:
            heartbeat_thread.start()

        try:
            response = converse_with_cache(
                bedrock,
                model_id=AGENTIC_MODEL_ID,
                messages=messages,
                system=[{"text": SYSTEM_PROMPT}],
                tool_config=tool_config,
                inference_config={"maxTokens": 4096, "temperature": 0.0},
            )
        except Exception as exc:
            heartbeat_stop.set()
            _log(
                "bedrock_converse_error",
                logging.ERROR,
                **trace_context,
                turn=turn_number,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        heartbeat_stop.set()
        converse_latency_ms = round((time.perf_counter() - converse_started) * 1000)

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)
        stop_reason = response["stopReason"]
        usage = response.get("usage", {})
        asst_summary = _assistant_summary(assistant_message)
        _log(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            stop_reason=stop_reason,
            usage=usage,
            assistant=asst_summary,
        )
        if asst_summary["text_preview"]:
            _emit_safe(
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
            fallback_answer = "\n".join(text_blocks)
            if stop_reason == "max_tokens" and fallback_answer:
                fallback_answer = fallback_answer + "\n\n_(Response may be incomplete)_"
            _log(
                "agent_final_text_without_answer_tool",
                **trace_context,
                turn=turn_number,
                stop_reason=stop_reason,
                answer_chars=len(fallback_answer),
            )
            # Model responded with text instead of calling prepare_answer.
            # Treat the text as a fallback answer.
            _log(
                "agent_loop_complete",
                **trace_context,
                terminal_reason="assistant_text_fallback",
                elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
                answer_chars=len(fallback_answer),
                discovered_doc_count=len(all_doc_ids),
                discovery=discovery_summary(discovery),
            )
            _record_trace(
                "loop_complete",
                terminalReason="assistant_text_fallback",
                turnsUsed=turn_number,
                elapsedMs=round((time.perf_counter() - loop_started) * 1000),
                citedDocCount=len(all_doc_ids),
                citedDocIds=list(all_doc_ids)[:20],
                discovery=discovery_summary(discovery),
            )
            _emit_safe(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="loop_complete",
                payload={
                    "terminalReason": "assistant_text_fallback",
                    "turnsUsed": turn_number,
                    "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                    "citedDocCount": len(all_doc_ids),
                    "discoveryCounts": discovery_summary(discovery),
                },
            )
            return AgentLoopResult(
                cited_doc_ids=list(all_doc_ids),
                all_chunks=all_chunks,
                all_doc_ids=all_doc_ids,
                discovery=discovery,
                fetched_opinions=fetched_opinions,
                faq_resource=None,
                answer_plan="",
                trace_log=trace_log,
                connection_alive=ws_connection_alive[0],
                fallback_answer=fallback_answer,
                high_confidence_faq=high_confidence_faq,
                faq_entries=faq_entries,
            )

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
            _emit_safe(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="tool_call",
                turn=turn_number,
                payload={
                    "toolName": tool_name,
                    "summary": build_tool_call_summary(tool_name, tool_input, neptune),
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

            if tool_name in ("search_document", "get_section") and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                        discovery.setdefault(doc_id, tool_name.replace("_", "-"))
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
                    stub_doc_id = citation_to_doc_id(citation)
                    fetched_opinions[stub_doc_id] = {
                        "citation": citation,
                        "raw_key": result.get("raw_key", ""),
                        "text": result.get("text", ""),
                        "scholar_url": result.get("scholar_url", ""),
                    }
                    all_doc_ids.add(stub_doc_id)
                    discovery[stub_doc_id] = "opinion-fetched"

            tool_result_summary = _tool_result_summary(tool_name, result)
            _record_trace(
                "tool_result", turn=turn_number,
                toolName=tool_name,
                status=tool_result_summary["status"],
                summary=tool_result_summary["summary_text"],
                docIds=tool_result_summary["doc_ids"],
                docTitles=tool_result_summary["doc_titles"],
                metadata=tool_result_summary["metadata"],
                latencyMs=tool_latency_ms,
            )
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
            if tool_name not in ("prepare_answer", "clarify"):
                result_metadata = dict(tool_result_summary["metadata"])
                if tool_latency_ms is not None:
                    result_metadata["latencyMs"] = tool_latency_ms
                result_metadata = filter_metadata(result_metadata)
                _emit_safe(
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

            if tool_name == "clarify":
                answer = result.get("question", "")
                _log(
                    "agent_loop_complete",
                    **trace_context,
                    terminal_reason="clarify_tool",
                    turns_used=turn_number,
                    elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
                    answer_chars=len(answer),
                    discovered_doc_count=len(all_doc_ids),
                )
                _record_trace(
                    "loop_complete", turn=turn_number,
                    terminalReason="clarify_tool",
                    turnsUsed=turn_number,
                    elapsedMs=round((time.perf_counter() - loop_started) * 1000),
                )
                _emit_safe(
                    ws_server, trace_seq, query_id=query_id,
                    kind="loop_complete",
                    payload={
                        "terminalReason": "clarify_tool",
                        "turnsUsed": turn_number,
                        "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                        "citedDocCount": 0,
                        "discoveryCounts": discovery_summary(discovery),
                    },
                )
                return AgentLoopResult(
                    cited_doc_ids=[],
                    all_chunks=all_chunks,
                    all_doc_ids=all_doc_ids,
                    discovery=discovery,
                    fetched_opinions=fetched_opinions,
                    faq_resource=None,
                    answer_plan="",
                    trace_log=trace_log,
                    connection_alive=ws_connection_alive[0],
                    fallback_answer=answer,
                    high_confidence_faq=high_confidence_faq,
                    faq_entries=faq_entries,
                )

            if tool_name == "prepare_answer":
                cited = list(result.get("cited_doc_ids", []))
                answer_plan = result.get("answer_plan", "")
                _log(
                    "agent_loop_complete",
                    **trace_context,
                    terminal_reason="prepare_answer",
                    turns_used=turn_number,
                    elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
                    cited_doc_count=len(cited),
                    discovered_doc_count=len(all_doc_ids),
                    has_plan=bool(answer_plan),
                    discovery=discovery_summary(discovery),
                )
                _record_trace(
                    "loop_complete", turn=turn_number,
                    terminalReason="prepare_answer",
                    turnsUsed=turn_number,
                    elapsedMs=round((time.perf_counter() - loop_started) * 1000),
                    citedDocCount=len(cited),
                    citedDocIds=cited[:20],
                    discovery=discovery_summary(discovery),
                )
                discovery_titles: dict[str, str] = {}
                for doc_id in cited[:20]:
                    try:
                        info = neptune.get_document(doc_id)
                        discovery_titles[doc_id] = (info or {}).get("title") or doc_id
                    except Exception:
                        discovery_titles[doc_id] = doc_id
                _emit_safe(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
                    kind="loop_complete",
                    payload={
                        "terminalReason": "prepare_answer",
                        "turnsUsed": turn_number,
                        "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                        "citedDocCount": len(cited),
                        "discoveryCounts": discovery_summary(discovery),
                        "discoveryTitles": discovery_titles,
                    },
                )
                return AgentLoopResult(
                    cited_doc_ids=cited,
                    all_chunks=all_chunks,
                    all_doc_ids=all_doc_ids,
                    discovery=discovery,
                    fetched_opinions=fetched_opinions,
                    faq_resource=None,  # resolved in handler
                    answer_plan=answer_plan,
                    trace_log=trace_log,
                    connection_alive=ws_connection_alive[0],
                    high_confidence_faq=high_confidence_faq,
                    faq_entries=faq_entries,
                )

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": _compact_for_model(result, tool_name)}],
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
            fallback_answer = last_text + "\n\n_(Response incomplete: turn budget reached)_"
        else:
            fallback_answer = (
                "I was unable to find a complete answer within the allowed number "
                "of search steps. Please try rephrasing your question."
            )
        _log(
            "agent_turn_budget_exhausted",
            logging.WARNING,
            **trace_context,
            answer_chars=len(fallback_answer),
            discovered_doc_count=len(all_doc_ids),
            accumulated_chunk_count=len(all_chunks),
            discovery=discovery_summary(discovery),
        )
        _log(
            "agent_loop_complete",
            **trace_context,
            terminal_reason="turn_budget_exhausted",
            elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
            answer_chars=len(fallback_answer),
            discovered_doc_count=len(all_doc_ids),
            discovery=discovery_summary(discovery),
        )
        _record_trace(
            "loop_complete",
            terminalReason="turn_budget_exhausted",
            turnsUsed=MAX_TURNS,
            elapsedMs=round((time.perf_counter() - loop_started) * 1000),
            citedDocCount=len(all_doc_ids),
            citedDocIds=list(all_doc_ids)[:20],
            discovery=discovery_summary(discovery),
        )
        _emit_safe(
            ws_server,
            trace_seq,
            query_id=query_id,
            kind="loop_complete",
            payload={
                "terminalReason": "turn_budget_exhausted",
                "turnsUsed": MAX_TURNS,
                "elapsedMs": round((time.perf_counter() - loop_started) * 1000),
                "citedDocCount": len(all_doc_ids),
                "discoveryCounts": discovery_summary(discovery),
            },
        )
        return AgentLoopResult(
            cited_doc_ids=list(all_doc_ids),
            all_chunks=all_chunks,
            all_doc_ids=all_doc_ids,
            discovery=discovery,
            fetched_opinions=fetched_opinions,
            faq_resource=None,
            answer_plan="",
            trace_log=trace_log,
            connection_alive=ws_connection_alive[0],
            fallback_answer=fallback_answer,
            high_confidence_faq=high_confidence_faq,
            faq_entries=faq_entries,
        )


def _send_resources(
    ws_server: WebSocketServer,
    query_id: str,
    rag_documents: list[RAGDocument],
    faq_resource: FAQResource | None,
) -> None:
    """Send resource cards (documents + FAQs) over WebSocket."""
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
            chunks=[{"page": c.page, "text": c.text} for c in doc.chunks],
        )
        for doc in rag_documents
    ]

    for msg in batch_documents_for_ws(source_documents, query_id):
        data = json.dumps({"streamId": "resources", "body": msg.model_dump(by_alias=True)})
        ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

    if faq_resource:
        faq_message = FAQMessage(
            query_id=query_id,
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
        data = json.dumps({"streamId": "resources", "body": faq_message.model_dump(by_alias=True)})
        ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)


def _send_resources_and_finalize(
    ws_server: WebSocketServer,
    query_id: str,
    answer: str,
    rag_documents: list[RAGDocument],
    faq_resource: FAQResource | None,
) -> None:
    """Send documents, FAQs, and full answer over WebSocket (fallback path)."""
    _send_resources(ws_server, query_id, rag_documents, faq_resource)

    start_msg = AnswerEventType(event="start", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": start_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

    frag_msg = FragmentMessage(
        query_id=query_id, content=FragmentContent(fragment=answer)
    )
    frag_data = json.dumps({"streamId": "answer", "body": frag_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=frag_data)

    stop_msg = AnswerEventType(event="stop", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": stop_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)


# --- Phase B: Answer Streaming ---

# ANSWER_STREAM_SYSTEM_PROMPT is now loaded from DynamoDB via prompt.py


def _build_answer_context(
    query: str,
    cited_chunks: list[dict],
    cited_doc_ids: set[str],
    discovery: dict[str, str],
    fetched_opinions: dict[str, dict],
    answer_plan: str,
    chat_history: list[dict] | None = None,
    neptune_client: NeptuneClient | None = None,
) -> str:
    """Build the context message for Phase B answer generation."""
    parts = []

    if chat_history:
        parts.append("## Prior Conversation")
        for turn in chat_history[-3:]:  # last 3 turns for context
            parts.append(f"User: {turn.get('query', '')}")
            parts.append(f"Assistant: {turn.get('answer', '')[:500]}")
        parts.append("")

    parts.append(f"## User Question\n{query}\n")

    if answer_plan:
        parts.append(f"## Answer Plan\n{answer_plan}\n")

    parts.append("## Retrieved Documents and Chunks\n")

    # Group chunks by document
    chunks_by_doc: dict[str, list[dict]] = {}
    for chunk in cited_chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunks_by_doc.setdefault(doc_id, []).append(chunk)

    for doc_id in sorted(cited_doc_ids):
        doc_chunks = chunks_by_doc.get(doc_id, [])
        # Get document metadata
        doc_info = None
        if neptune_client:
            try:
                doc_info = neptune_client.get_document(doc_id)
            except Exception:
                pass

        title = (doc_info or {}).get("title", doc_id)
        authority = (doc_info or {}).get("authority_level", "")

        parts.append(f"### [{title}](doc:{doc_id})")
        if authority:
            parts.append(f"Authority level: {authority}")

        if doc_chunks:
            # Sub-group by heading so chapter boundaries are unambiguous
            chunks_by_heading: dict[str, list[dict]] = {}
            for chunk in doc_chunks:
                h = chunk.get("heading", "")
                chunks_by_heading.setdefault(h, []).append(chunk)

            for heading, h_chunks in chunks_by_heading.items():
                if heading:
                    pages = sorted({c.get("start_page") for c in h_chunks if c.get("start_page")})
                    page_range = f" (pages {pages[0]}-{pages[-1]})" if len(pages) > 1 else (f" (page {pages[0]})" if pages else "")
                    parts.append(f"\n#### {heading}{page_range}")
                for chunk in h_chunks:
                    page = chunk.get("start_page")
                    page_ref = f" (page {page})" if page else ""
                    parts.append(f"\n**Chunk{page_ref}:**")
                    parts.append(chunk.get("text", "")[:2000])

        # For statute docs, include a section→page index so the model
        # can look up correct page numbers for sections not in the
        # retrieved chunks.
        if doc_id.startswith("statutes-") and neptune_client:
            try:
                sections = neptune_client.list_document_sections(doc_id)
                if sections:
                    # Extract numeric chapter (e.g. "70" from "statutes-70")
                    chapter_match = re.match(r"statutes-(\d+)", doc_id)
                    chapter = chapter_match.group(1) if chapter_match else doc_id.replace("statutes-", "")
                    # Same pattern the statute chunker uses to identify
                    # canonical section headings (rejects cross-references
                    # like "70.32 (2) (a) 6..." that appear inside other
                    # sections).
                    section_pattern = re.compile(
                        rf"^({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)"
                    )
                    # Fallback: any heading starting with the section number
                    # (used when no canonical heading exists)
                    loose_pattern = re.compile(rf"^{re.escape(chapter)}\.\d+")
                    index_lines = []
                    seen_sections: dict[str, int] = {}
                    fallback_sections: dict[str, int] = {}
                    for sec in sections:
                        heading = sec.get("heading", "")
                        first_page = sec.get("first_page")
                        if not heading or first_page is None:
                            continue
                        m = section_pattern.match(heading)
                        if m:
                            sec_num = m.group(1)
                            if sec_num not in seen_sections:
                                seen_sections[sec_num] = first_page
                        else:
                            m2 = loose_pattern.match(heading)
                            if m2:
                                sec_num = m2.group(0)
                                if sec_num not in fallback_sections:
                                    fallback_sections[sec_num] = first_page
                    merged = {**fallback_sections, **seen_sections}
                    # Search ALL cited chunks for statute section references,
                    # not just chunks from this statute. Other documents (guides,
                    # WPAM, admin rules) frequently reference statute sections,
                    # and the model needs page numbers for those references.
                    all_text_blob = " ".join(
                        c.get("text", "") for c in cited_chunks
                    )
                    referenced = set(
                        re.findall(rf"{re.escape(chapter)}\.\d+[A-Za-z\-]*", all_text_blob)
                    )
                    for sec_num, page in merged.items():
                        if sec_num in referenced:
                            index_lines.append(f"- § {sec_num} → page {page}")
                    if index_lines:
                        parts.append("\n**Section Page Index** (use these page numbers for `#page=N` citations; subsections like 70.32(2)(c)1g use the parent section's page, e.g. § 70.32 → page 23 means all 70.32(...) subsections start at page 23):")
                        parts.extend(index_lines)
            except Exception:
                pass

        # Include case opinion text if available
        if doc_id in fetched_opinions:
            opinion = fetched_opinions[doc_id]
            parts.append(f"\n**Case Opinion ({opinion.get('citation', '')}):**")
            parts.append(opinion.get("text", "")[:3000])

        parts.append("")

    # Fallback: build section page indexes for statute chapters referenced
    # in cited chunks but not explicitly in cited_doc_ids. The agent often
    # discovers statutes via get_neighbors but doesn't fetch them directly.
    if neptune_client and cited_chunks:
        all_text_blob = " ".join(c.get("text", "") for c in cited_chunks)
        # Match patterns like "70.32", "73.03", "74.485" — real statute refs.
        # Also handles OCR artifacts with spaces: "70. 32", "73. 03".
        raw_refs = re.findall(r"(\d+)\.\s*(\d+)", all_text_blob)
        referenced_chapters: set[str] = set()
        for chap, _sec in raw_refs:
            doc_id_candidate = f"statutes-{chap}"
            if doc_id_candidate not in cited_doc_ids and int(chap) >= 70:
                referenced_chapters.add(chap)

        for chapter in sorted(referenced_chapters):
            stat_doc_id = f"statutes-{chapter}"
            try:
                sections = neptune_client.list_document_sections(stat_doc_id)
                if not sections:
                    continue
                section_pattern = re.compile(
                    rf"^({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)"
                )
                loose_pattern = re.compile(rf"^{re.escape(chapter)}\.\d+")
                seen_sections: dict[str, int] = {}
                fallback_sections: dict[str, int] = {}
                for sec in sections:
                    heading = sec.get("heading", "")
                    first_page = sec.get("first_page")
                    if not heading or first_page is None:
                        continue
                    m = section_pattern.match(heading)
                    if m:
                        sec_num = m.group(1)
                        if sec_num not in seen_sections:
                            seen_sections[sec_num] = first_page
                    else:
                        m2 = loose_pattern.match(heading)
                        if m2:
                            sec_num = m2.group(0)
                            if sec_num not in fallback_sections:
                                fallback_sections[sec_num] = first_page
                merged = {**fallback_sections, **seen_sections}
                # Filter to sections actually referenced in chunk text
                referenced = set(
                    re.findall(rf"{re.escape(chapter)}\.\s*\d+[A-Za-z\-]*", all_text_blob)
                )
                # Normalize OCR spaces: "70. 32" → "70.32"
                referenced_normalized = {r.replace(" ", "") for r in referenced}
                index_lines = []
                for sec_num, page in merged.items():
                    if sec_num in referenced_normalized:
                        index_lines.append(f"- § {sec_num} → page {page}")
                if index_lines:
                    parts.append(f"### Statute Chapter {chapter} — Section Page Index")
                    parts.append("(Link directly with `doc:statutes-" + chapter + "#page=N`; subsections use the parent section's page)")
                    parts.extend(index_lines)
                    parts.append("")
            except Exception:
                pass

    parts.append(f"\n## Documents to Cite\nYou MUST cite these document IDs: {sorted(cited_doc_ids)}")

    return "\n".join(parts)


def _stream_answer(
    ws_server: WebSocketServer,
    query_id: str,
    answer_context: str,
    trace_seq,
    ws_connection_alive: list[bool],
) -> str:
    """Phase B: Stream the answer token-by-token via converse_stream().

    Returns the full accumulated answer text.
    """
    _emit(
        ws_server, trace_seq,
        query_id=query_id,
        kind="phase",
        payload={"phase": "answer_streaming"},
    )

    # Send answer-event: start
    start_msg = AnswerEventType(event="start", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": start_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

    # Start heartbeat for the streaming phase
    heartbeat_stop = threading.Event()

    def _heartbeat_loop():
        while not heartbeat_stop.wait(_WS_HEARTBEAT_INTERVAL):
            if not ws_connection_alive[0]:
                break
            try:
                ws_server.client.post_to_connection(
                    ConnectionId=ws_server.connection_id,
                    Data=json.dumps({"streamId": "heartbeat", "body": {}}),
                )
            except Exception:
                logger.info("WebSocket connection gone during answer stream heartbeat")
                ws_connection_alive[0] = False
                break

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    if ws_connection_alive[0]:
        heartbeat_thread.start()

    # Start streaming answer with NO tools — pure text output
    stream_started = time.perf_counter()
    try:
        stream_response = converse_stream_with_cache(
            bedrock,
            model_id=AGENTIC_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": answer_context}]}],
            system=[{"text": ANSWER_STREAM_SYSTEM_PROMPT}],
            inference_config={"maxTokens": 4096, "temperature": 0.0},
        )
    except Exception as exc:
        heartbeat_stop.set()
        _log(
            "answer_stream_error",
            logging.ERROR,
            query_id=query_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    # Stream text deltas to WebSocket
    answer_text = ""
    fragment_buffer = ""
    _FRAGMENT_MIN_SIZE = 30  # batch small deltas to avoid excessive WS calls

    event_stream = stream_response.get("stream")
    if event_stream:
        for event in event_stream:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                text_chunk = delta.get("text", "")
                if text_chunk:
                    answer_text += text_chunk
                    fragment_buffer += text_chunk

                    if len(fragment_buffer) >= _FRAGMENT_MIN_SIZE:
                        if ws_connection_alive[0]:
                            frag_msg = FragmentMessage(
                                query_id=query_id,
                                content=FragmentContent(fragment=fragment_buffer),
                            )
                            try:
                                ws_server.client.post_to_connection(
                                    ConnectionId=ws_server.connection_id,
                                    Data=json.dumps({"streamId": "answer", "body": frag_msg.model_dump(by_alias=True)}),
                                )
                            except Exception:
                                ws_connection_alive[0] = False
                        fragment_buffer = ""

            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                _log(
                    "answer_stream_usage",
                    usage=usage,
                    query_id=query_id,
                )

    heartbeat_stop.set()

    # Flush remaining buffer
    if fragment_buffer and ws_connection_alive[0]:
        frag_msg = FragmentMessage(
            query_id=query_id,
            content=FragmentContent(fragment=fragment_buffer),
        )
        try:
            ws_server.client.post_to_connection(
                ConnectionId=ws_server.connection_id,
                Data=json.dumps({"streamId": "answer", "body": frag_msg.model_dump(by_alias=True)}),
            )
        except Exception:
            ws_connection_alive[0] = False

    # Send answer-event: stop
    stop_msg = AnswerEventType(event="stop", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": stop_msg.model_dump(by_alias=True)})
    if ws_connection_alive[0]:
        try:
            ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)
        except Exception:
            ws_connection_alive[0] = False

    stream_latency = round((time.perf_counter() - stream_started) * 1000)
    _log(
        "answer_stream_complete",
        query_id=query_id,
        answer_chars=len(answer_text),
        stream_latency_ms=stream_latency,
    )

    return answer_text


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
            from disambiguation import CLARIFICATION_QUESTION, PROPERTY_TYPE_CHOICES, should_disambiguate

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
                    _send_resources_and_finalize(
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
                    data = json.dumps({"streamId": "choices", "body": choices_msg.model_dump(by_alias=True)})
                    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)
                save_chat_history(
                    session_id,
                    user_query.query_id,
                    user_query.query,
                    answer,
                )
                return {"successful": True}

        # === Phase A: Research Loop ===
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
                result.all_chunks, result.all_doc_ids, result.discovery,
                result.fetched_opinions, neptune_client=neptune,
            )
            faq_resource = (
                result.high_confidence_faq
                or build_cited_faq_resource(result.faq_entries, result.all_doc_ids)
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
                    _send_resources_and_finalize(
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
            cited_chunks = [
                c for c in result.all_chunks if c.get("doc_id") in cited
            ]
            cited_discovery = {
                k: v for k, v in result.discovery.items() if k in cited
            }
            for cid in cited:
                cited_discovery.setdefault(cid, "fetched")

            # Opinion backfill for case law stubs not already fetched
            cited_opinions = {
                k: v for k, v in result.fetched_opinions.items() if k in cited
            }
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

            rag_documents = build_rag_documents(
                cited_chunks, cited, cited_discovery, cited_opinions,
                neptune_client=neptune,
            )
            faq_resource = (
                result.high_confidence_faq
                or build_cited_faq_resource(result.faq_entries, cited)
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
                    _send_resources(ws_server, user_query.query_id, rag_documents, faq_resource)

                    # 3. Stream answer (Phase B)
                    answer_context = _build_answer_context(
                        user_query.query,
                        cited_chunks,
                        cited,
                        cited_discovery,
                        cited_opinions,
                        result.answer_plan,
                        chat_history=chat_history,
                        neptune_client=neptune,
                    )
                    answer = _stream_answer(
                        ws_server,
                        user_query.query_id,
                        answer_context,
                        trace_seq,
                        ws_connection_alive,
                    )
                except Exception as phase_b_exc:
                    logger.error(
                        "Phase B failed | exc_type=%s exc=%s",
                        type(phase_b_exc).__name__, phase_b_exc, exc_info=True,
                    )
                    if not answer:
                        try:
                            if not answer_context:
                                answer_context = _build_answer_context(
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
                                system=[{"text": ANSWER_STREAM_SYSTEM_PROMPT}],
                                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
                            )
                            text_blocks = [
                                block["text"]
                                for block in response["output"]["message"]["content"]
                                if "text" in block
                            ]
                            answer = "\n".join(text_blocks)
                            logger.info("Phase B fallback: generated answer via non-streaming converse()")
                        except Exception as fallback_exc:
                            logger.error(f"Phase B non-streaming fallback failed: {fallback_exc}")
                            answer = "(Answer generation failed — please retry)"
            else:
                # No WebSocket — generate answer without streaming for DB save
                answer_context = _build_answer_context(
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
                        system=[{"text": ANSWER_STREAM_SYSTEM_PROMPT}],
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
