"""Phase A: Claude's agentic research loop against Neptune.

1. Turn 0 is hardcoded: faq_search with the verbatim user query (plus an
   optional refine_query rewrite when chat history exists).
2. Claude decides which tools to call (vector_search, get_neighbors, etc.)
3. Tools execute against Neptune Analytics.
4. The loop exits when Claude calls prepare_answer (declaring cited docs and
   an answer plan) — the answer text itself is generated in Phase B.
"""

import itertools
import logging
import time
from dataclasses import dataclass, field

from agent_tools import TOOL_DEFINITIONS, execute_tool
from case_law import citation_to_doc_id
from faq import build_faq_resource, faq_search_direct
from prompt import SYSTEM_PROMPT
from step_function_types.models import FAQResource
from streaming.bedrock import converse_with_cache
from tracing.emitter import filter_metadata
from tracing.runtime import (
    assistant_summary as _assistant_summary,
)
from tracing.runtime import (
    emit as _emit,
)
from tracing.runtime import (
    log_event as _log,
)
from tracing.runtime import (
    query_fields as _query_fields,
)
from tracing.runtime import (
    tool_call_summary,
)
from tracing.runtime import (
    tool_result_summary as _tool_result_summary,
)
from tracing.summaries import discovery_summary

from config import AGENTIC_MODEL_ID, FAQ_SCORE_THRESHOLD, MAX_TURNS, bedrock, neptune

from .heartbeat import start_heartbeat

logger = logging.getLogger(__name__)

# Neptune node labels that aren't user-citable documents.
_NON_DOCUMENT_LABELS = frozenset({"Chunk", "Topic", "Framework"})


def _is_document_neighbor(neighbor: dict) -> bool:
    """True when this neighbor node represents a citable document."""
    labels = neighbor.get("labels") or []
    return not any(label in _NON_DOCUMENT_LABELS for label in labels)


_CHUNK_FIELDS_FOR_MODEL = frozenset(
    {
        "chunk_id",
        "text",
        "doc_id",
        "heading",
        "subheading",
        "start_page",
        "end_page",
        "authority_level",
        "doc_title",
    }
)

_NEIGHBOR_FIELDS_FOR_MODEL = frozenset(
    {
        "id",
        "title",
        "relationship",
        "labels",
        "authority_level",
        "framework_id",
    }
)


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
                {k: v for k, v in chunk.items() if k in _CHUNK_FIELDS_FOR_MODEL and v is not None}
                for chunk in value
            ]
        elif key == "graph_context":
            compacted["graph_context"] = {
                doc_id: [
                    {
                        k: v
                        for k, v in n.items()
                        if k in _NEIGHBOR_FIELDS_FOR_MODEL and v is not None
                    }
                    for n in neighbors
                ]
                for doc_id, neighbors in value.items()
            }
        elif key in ("score", "pre_dedup_count", "ranking_stats"):
            continue
        else:
            compacted[key] = value
    return compacted


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
            logger.info(f"Turn-0 refine: '{query[:80]}' -> '{refined[:80]}'")
            search_query = refined
        refine_summary = _tool_result_summary("refine_query", refine_result)
        _record_trace(
            "tool_result",
            turn=0,
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
            "summary": tool_call_summary("faq_search", {"query": search_query}),
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
        "tool_result",
        turn=0,
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
        messages.append({"role": "assistant", "content": [{"text": turn["answer"]}]})

    # Seed the conversation with the FAQ result as if Claude had called it.
    seed_tool_use_id = "faq_search_turn0"
    messages.extend(
        [
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
        ]
    )

    if high_confidence_faq:
        faq_ids = [faq.faq_id for faq in high_confidence_faq.faqs]
        messages.append(
            {
                "role": "user",
                "content": [
                    {
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
                    }
                ],
            }
        )

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
            messages.append(
                {
                    "role": "user",
                    "content": [{"text": warning}],
                }
            )
            _log(
                "agent_turn_budget_warning_injected",
                **trace_context,
                turn=turn_number,
            )

        converse_started = time.perf_counter()

        heartbeat_stop = start_heartbeat(ws_server, ws_connection_alive)

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

        tool_uses = [block for block in assistant_message["content"] if "toolUse" in block]

        if not tool_uses:
            text_blocks = [
                block["text"] for block in assistant_message["content"] if "text" in block
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
                    "summary": tool_call_summary(tool_name, tool_input),
                    "status": "pending",
                },
                dev_payload={
                    "toolInput": tool_input,
                    "toolUseId": tool_use_id,
                },
            )

            tool_started = time.perf_counter()
            try:
                result = execute_tool(tool_name, tool_input, neptune, chat_history=chat_history)
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
                result = {"error": f"{tool_name} failed: {type(exc).__name__}: {exc}"}
            tool_latency_ms = round((time.perf_counter() - tool_started) * 1000)

            if tool_name == "vector_search" and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                        discovery.setdefault(doc_id, "vector-search")
                    all_chunks.append(chunk)
                # Auto-enrichment neighbors are no longer surfaced (Direction 1,
                # Option A): a doc becomes citable only when the model actually
                # retrieves it (vector chunk, explicit get_neighbors,
                # get_document, or a case-law path). vector_search no longer
                # returns graph_context, so there are no enrichment neighbors to
                # first-class as discovery here.

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
                "tool_result",
                turn=turn_number,
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
                    "loop_complete",
                    turn=turn_number,
                    terminalReason="clarify_tool",
                    turnsUsed=turn_number,
                    elapsedMs=round((time.perf_counter() - loop_started) * 1000),
                )
                _emit_safe(
                    ws_server,
                    trace_seq,
                    query_id=query_id,
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
                # Per-source breakdown of the CITED docs (not all discovered).
                # Lets us query "what fraction of cited docs came from each
                # discovery path" directly from logs — the forward-measurement
                # signal for the Direction 1 enrichment retarget.
                cited_discovery = discovery_summary(
                    {doc_id: discovery.get(doc_id, "unknown") for doc_id in cited}
                )
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
                    cited_discovery=cited_discovery,
                )
                _record_trace(
                    "loop_complete",
                    turn=turn_number,
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

            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"json": _compact_for_model(result, tool_name)}],
                    }
                }
            )

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
