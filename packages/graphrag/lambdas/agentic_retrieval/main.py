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
import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import boto3
import pydantic
from case_opinion import citation_to_raw_slug
from neptune_client import NeptuneClient
from prompt import SYSTEM_PROMPT
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    FAQ,
    DocumentResource,
    FAQResource,
    RAGDocument,
    UserQuery,
)
from tools import TOOL_DEFINITIONS, execute_tool

MAX_TURNS = 10

# Bedrock KB relevance scores range 0-1. A well-matched FAQ typically scores
# 0.75+; loosely related hits land around 0.6-0.7. 0.70 is strict enough to
# avoid false short-circuits on tangential FAQs while still catching paraphrases.
FAQ_SCORE_THRESHOLD = 0.70

# Cap on FAQ entries passed to downstream synthesis. Prevents low-relevance
# hits below the top match from diluting the prompt.
MAX_FAQS = 3

# Neptune node labels that aren't user-citable documents. Graph traversals
# return these alongside Document nodes (Chunks link back via EXTRACTED_FROM,
# Topics via TAGGED_WITH, Frameworks via BELONGS_TO). They have no title or
# summary so they can't satisfy the RAGDocument schema downstream.
_NON_DOCUMENT_LABELS = frozenset({"Chunk", "Topic", "Framework"})


def _is_document_neighbor(neighbor: dict) -> bool:
    """True when this neighbor node represents a citable document."""
    labels = neighbor.get("labels") or []
    return not any(label in _NON_DOCUMENT_LABELS for label in labels)

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

REGION = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3_client = boto3.client("s3", region_name=REGION)
neptune = NeptuneClient()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
PRESIGNED_URL_EXPIRY = int(os.environ.get("PRESIGNED_URL_EXPIRY", "3600"))

AGENTIC_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
LOG_AGENT_TRACE = os.environ.get("LOG_AGENT_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))


def _redact_text(text: str) -> str:
    """Remove common accidental PII before writing query/tool text to logs."""
    redacted = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", redacted)
    redacted = re.sub(
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "[REDACTED_PHONE]",
        redacted,
    )
    return redacted


def _truncate_text(value: str, max_chars: int = LOG_MAX_TEXT_CHARS) -> str:
    text = _redact_text(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...[truncated {len(text) - max_chars} chars]"


def _compact_log_value(value: Any, max_chars: int = LOG_MAX_TEXT_CHARS) -> Any:
    """Bound nested log fields so CloudWatch events stay queryable."""
    if isinstance(value, str):
        return _truncate_text(value, max_chars)
    if isinstance(value, dict):
        return {str(k): _compact_log_value(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        compact = [_compact_log_value(v, max_chars) for v in value[:10]]
        if len(value) > 10:
            compact.append(f"...[{len(value) - 10} more]")
        return compact
    return value


def _log_agent_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    if not LOG_AGENT_TRACE and level < logging.WARNING:
        return
    payload = {
        "component": "graphrag.agentic_retrieval",
        "event": event,
        **fields,
    }
    logger.log(
        level,
        json.dumps(_compact_log_value(payload), default=str, separators=(",", ":")),
    )


def _query_log_fields(query: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        "query_chars": len(query),
    }
    if LOG_QUERY_TEXT:
        fields["query_preview"] = _truncate_text(query)
    return fields


def _summarize_assistant_message(message: dict) -> dict[str, Any]:
    content = message.get("content") or []
    text_blocks = [block.get("text", "") for block in content if "text" in block]
    tool_names = [
        block["toolUse"].get("name", "")
        for block in content
        if "toolUse" in block
    ]
    return {
        "content_blocks": len(content),
        "text_block_count": len(text_blocks),
        "text_preview": _truncate_text("\n".join(text_blocks)) if text_blocks else "",
        "tool_use_count": len(tool_names),
        "tool_names": tool_names,
    }


def _summarize_bedrock_response(response: dict) -> dict[str, Any]:
    usage = response.get("usage") or {}
    metrics = response.get("metrics") or {}
    return {
        "stop_reason": response.get("stopReason", ""),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "total_tokens": usage.get("totalTokens"),
        "model_latency_ms": metrics.get("latencyMs"),
    }


def _summarize_tool_result(tool_name: str, result: dict) -> dict[str, Any]:
    if "error" in result:
        return {
            "tool_name": tool_name,
            "status": "error",
            "error": result.get("error"),
            "fallback_match_count": len(result.get("fallback_matches", [])),
        }

    if tool_name == "faq_search":
        scores = [round(faq.get("score", 0.0), 4) for faq in result.get("faqs", [])[:5]]
        return {
            "tool_name": tool_name,
            "status": "ok",
            "faq_count": result.get("count", 0),
            "top_scores": scores,
        }

    if tool_name == "vector_search":
        chunks = result.get("chunks", [])
        graph_context = result.get("graph_context", {})
        return {
            "tool_name": tool_name,
            "status": "ok",
            "chunk_count": len(chunks),
            "top_doc_ids": [chunk.get("doc_id") for chunk in chunks[:5]],
            "graph_context_doc_count": len(graph_context),
            "graph_context_neighbor_count": sum(len(v) for v in graph_context.values()),
        }

    if tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        return {
            "tool_name": tool_name,
            "status": "ok",
            "neighbor_count": len(neighbors),
            "relationships": sorted({
                n.get("relationship", "") for n in neighbors if n.get("relationship")
            }),
            "neighbor_ids": [n.get("id") for n in neighbors[:10]],
        }

    if tool_name == "get_document":
        doc = result.get("document")
        return {
            "tool_name": tool_name,
            "status": "ok" if doc else "miss",
            "document_id": (doc or {}).get("id"),
            "document_type": (doc or {}).get("doc_type"),
            "authority_level": (doc or {}).get("authority_level"),
        }

    if tool_name == "get_authority_chain":
        chain = result.get("authority_chain", [])
        return {
            "tool_name": tool_name,
            "status": "ok",
            "chain_length": len(chain),
            "chain_ids": [node.get("id") for node in chain[:10]],
        }

    if tool_name == "list_framework_docs":
        docs = result.get("documents", [])
        return {
            "tool_name": tool_name,
            "status": "ok",
            "document_count": len(docs),
            "document_ids": [doc.get("id") for doc in docs[:10]],
        }

    if tool_name == "fetch_case_opinion":
        return {
            "tool_name": tool_name,
            "status": "ok" if result.get("found") else "miss",
            "citation": result.get("citation"),
            "raw_key": result.get("raw_key", ""),
            "opinion_chars": len(result.get("text", "")),
        }

    if tool_name == "answer":
        return {
            "tool_name": tool_name,
            "status": "terminal",
            "response_chars": len(result.get("response", "")),
            "cited_doc_count": len(result.get("cited_doc_ids", [])),
            "cited_doc_ids": result.get("cited_doc_ids", [])[:20],
        }

    return {"tool_name": tool_name, "status": "ok", "result_keys": sorted(result.keys())}


def _discovery_summary(discovery: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tag in discovery.values():
        counts[tag] = counts.get(tag, 0) + 1
    return counts

CHAT_HISTORY_TABLE = os.environ.get("CHAT_HISTORY_TABLE_NAME", "")
# Cap history passed to Claude. Long histories bloat the context window and
# older turns rarely help resolve a current follow-up.
MAX_HISTORY_TURNS = 5
dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)


def get_chat_history(session_id: str) -> list[dict[str, str]]:
    """Fetch prior {query, answer} pairs for a session, oldest first.

    Returns an empty list if the table isn't configured or the query fails;
    history is an enrichment, not a correctness requirement.
    """
    if not CHAT_HISTORY_TABLE or not session_id:
        return []
    try:
        table = dynamodb_resource.Table(CHAT_HISTORY_TABLE)
        response = table.query(
            IndexName="sessionIdKey",
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
            ScanIndexForward=True,
        )
        items = response.get("Items", [])
        history = [
            {"query": item["query"], "answer": item["answer"]}
            for item in items
            if item.get("query") and item.get("answer")
        ]
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
        logger.info(
            f"Loaded {len(history)} history turn(s) for session {session_id}"
        )
        return history
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch chat history for session {session_id}",
            exc_info=True,
        )
        return []


def process_event(event: dict) -> UserQuery:
    """Parse input event.

    Receives a clean {query, query_id, session_id} from EventBridge $.detail extraction.
    """
    try:
        return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


_FAQ_QA_RE = re.compile(r"^Q:\s*(.*?)\s*\nA:\s*(.*)$", re.DOTALL)


def _parse_faq_text(text: str) -> tuple[str, str] | None:
    """Split a KB chunk like 'Q: ...\\nA: ...' into (question, answer).

    Returns None when the chunk doesn't match the expected Q/A shape so callers
    can skip it instead of emitting an FAQ with empty fields.
    """
    match = _FAQ_QA_RE.match(text.strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _faq_id_from_uri(uri: str) -> str:
    """Derive a stable FAQ id from its S3 source URI.

    The bucket layout is `s3://.../faq_N.txt`, so the file stem works as a
    human-readable id. Falls back to the full URI when the shape is unexpected.
    """
    if not uri:
        return "faq"
    stem = uri.rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] or uri


def _build_faq_resource(faq_results: list[dict]) -> FAQResource | None:
    """Convert raw `faq_search` results into a FAQResource.

    Keeps only parseable Q/A chunks (some KB chunks may have been edited to
    plain text and would fail the regex). Returns None when nothing parses so
    downstream jobs treat the query as FAQ-less.
    """
    faqs: list[FAQ] = []
    for entry in faq_results[:MAX_FAQS]:
        parsed = _parse_faq_text(entry.get("text", ""))
        if not parsed:
            continue
        question, answer = parsed
        faqs.append(
            FAQ(
                faq_id=_faq_id_from_uri(entry.get("source_uri", "")),
                question=question,
                answer=answer,
            )
        )
    return FAQResource(faqs=faqs) if faqs else None


def _faq_search_direct(query: str) -> dict:
    """Run the `faq_search` tool with the user's verbatim query.

    Bypasses Claude to ensure turn 0 is a deterministic, single-tool call
    with the exact user phrasing (Claude tends to paraphrase, which hurts
    KB retrieval quality).
    """
    return execute_tool("faq_search", {"query": query}, neptune)


def run_agentic_loop(
    query: str,
    chat_history: list[dict] | None = None,
    *,
    query_id: str = "",
    session_id: str = "",
    request_id: str = "",
) -> tuple[str, list[str], list[RAGDocument], FAQResource | None]:
    """Run Claude's agentic loop against Neptune.

    Turn 0 is hardcoded: run `faq_search` with the verbatim user query. If the
    top FAQ score clears FAQ_SCORE_THRESHOLD, short-circuit — return the FAQs
    and let downstream ResponseStreaming synthesize the answer. Otherwise seed
    the conversation with the FAQ result and hand off to Claude for graph work.

    When ``chat_history`` is provided, prior {query, answer} pairs are
    prepended to the message list so Claude can resolve follow-up questions
    ("what about agriculture") against earlier context, and ``refine_query``
    can reach the same history via execute_tool.

    Returns:
        (answer_text, cited_doc_ids, rag_documents, faq_resource)

    answer_text is only meaningful when we fall through to the loop; when we
    short-circuit on FAQs it's a placeholder since downstream re-synthesizes.
    """
    chat_history = chat_history or []
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []
    discovery: dict[str, str] = {}  # doc_id -> tag
    # citation -> fetched-opinion payload. Keyed by the stub doc_id we'd
    # otherwise emit, so _build_rag_documents can replace the stub with the
    # richer opinion card.
    fetched_opinions: dict[str, dict] = {}

    # Turn 0 refinement: when prior turns exist, the raw query is often a
    # context-dependent follow-up ("what about agriculture") that will match
    # nothing on its own. Rewrite it against history before the FAQ search
    # so turn 0 has a fighting chance of short-circuiting. On fresh sessions
    # we skip this — the first query is usually self-contained and refining
    # it just burns a Bedrock call.
    search_query = query
    if chat_history:
        refine_result = execute_tool(
            "refine_query", {"query": query}, neptune, chat_history=chat_history
        )
        refined = refine_result.get("refined_query") or query
        if refined and refined != query:
            logger.info(
                f"Turn-0 refine: '{query[:80]}' -> '{refined[:80]}'"
            )
            search_query = refined

    # Turn 0: deterministic FAQ search (using the refined query when we have one).
    faq_result = _faq_search_direct(search_query)
    faq_entries = faq_result.get("faqs", [])
    top_score = faq_entries[0].get("score", 0.0) if faq_entries else 0.0
    logger.info(
        f"FAQ turn-0: {len(faq_entries)} hits, top_score={top_score:.3f}, "
        f"threshold={FAQ_SCORE_THRESHOLD}"
    )

    if top_score >= FAQ_SCORE_THRESHOLD:
        faq_resource = _build_faq_resource(faq_entries)
        if faq_resource:
            logger.info(
                f"FAQ short-circuit: returning {len(faq_resource.faqs)} FAQ(s) "
                "without entering agentic loop"
            )
            # Empty document list — answer is fully grounded in FAQs.
            return "", [], [], faq_resource
        logger.warning(
            "FAQ score cleared threshold but no entries parsed; falling through to graph"
        )

    # Prepend prior turns so Claude can resolve pronouns and short follow-ups
    # against the conversation, not just the current query. We replay them as
    # synthetic user/assistant pairs rather than stuffing into the system
    # prompt — this keeps Claude's turn-taking natural and lets it cite back
    # to what it said before if relevant.
    messages: list[dict] = []
    for turn in chat_history:
        messages.append({"role": "user", "content": [{"text": turn["query"]}]})
        messages.append(
            {"role": "assistant", "content": [{"text": turn["answer"]}]}
        )

    # Seed the conversation with the FAQ result as if Claude had called it,
    # so the loop sees the FAQ context on turn 1 without re-invoking the tool.
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

    tool_config = {"tools": TOOL_DEFINITIONS}
    trace_context = {
        "query_id": query_id,
        "session_id": session_id,
        "request_id": request_id,
    }
    loop_started = time.perf_counter()

    _log_agent_event(
        "agent_loop_start",
        **trace_context,
        model_id=AGENTIC_MODEL_ID,
        max_turns=MAX_TURNS,
        **_query_log_fields(query),
    )

    for turn in range(MAX_TURNS):
        turn_number = turn + 1
        _log_agent_event(
            "agent_turn_start",
            **trace_context,
            turn=turn_number,
            max_turns=MAX_TURNS,
            message_count=len(messages),
            discovered_doc_count=len(all_doc_ids),
            chunk_count=len(all_chunks),
            discovery=_discovery_summary(discovery),
        )

        # Turn-8 warning injection (docs/graphrag.md §7)
        if turn == 7:
            warning = (
                "You are running low on turns. Call the answer tool NOW with your "
                "best answer from the context gathered so far."
            )
            messages.append({
                "role": "user",
                "content": [{"text": warning}],
            })
            _log_agent_event(
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
            _log_agent_event(
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
        _log_agent_event(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            **_summarize_bedrock_response(response),
            assistant=_summarize_assistant_message(assistant_message),
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
            _log_agent_event(
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

            _log_agent_event(
                "agent_tool_call",
                **trace_context,
                turn=turn_number,
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                tool_input=tool_input,
            )

            tool_started = time.perf_counter()
            try:
                result = execute_tool(
                    tool_name, tool_input, neptune, chat_history=chat_history
                )
            except Exception as exc:
                _log_agent_event(
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
                raise
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

            _log_agent_event(
                "agent_tool_result",
                **trace_context,
                turn=turn_number,
                tool_use_id=tool_use_id,
                tool_latency_ms=tool_latency_ms,
                discovered_doc_count=len(all_doc_ids),
                chunk_count=len(all_chunks),
                discovery=_discovery_summary(discovery),
                **_summarize_tool_result(tool_name, result),
            )

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = set(result.get("cited_doc_ids", []))
                # Restrict the sidebar to exactly what the agent cited.
                # Tool results (esp. vector_search's graph-neighbor auto-
                # enrichment) pull in far more docs than the answer uses —
                # case-law stubs CITES-linked to statutes end up as cards
                # the user never asked about. The prompt asks the agent
                # to err toward including MORE in cited_doc_ids, so this
                # is the right authoritative list.
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
                rag_docs = _build_rag_documents(
                    cited_chunks, cited, cited_discovery, cited_opinions
                )
                _log_agent_event(
                    "agent_loop_complete",
                    **trace_context,
                    terminal_reason="answer_tool",
                    turns_used=turn_number,
                    elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
                    answer_chars=len(answer),
                    cited_doc_count=len(cited),
                    discovered_doc_count=len(all_doc_ids),
                    rag_document_count=len(rag_docs),
                    discovery=_discovery_summary(cited_discovery),
                )
                return answer, list(cited), rag_docs, None

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                }
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        # Turn budget exhausted without an answer tool call — extract last text
        # from the most recent assistant message as a degraded fallback.
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
        _log_agent_event(
            "agent_turn_budget_exhausted",
            logging.WARNING,
            **trace_context,
            answer_chars=len(answer),
            discovered_doc_count=len(all_doc_ids),
            chunk_count=len(all_chunks),
            discovery=_discovery_summary(discovery),
        )

    rag_docs = _build_rag_documents(
        all_chunks, all_doc_ids, discovery, fetched_opinions
    )
    _log_agent_event(
        "agent_loop_complete",
        **trace_context,
        terminal_reason="assistant_text_or_fallback",
        elapsed_ms=round((time.perf_counter() - loop_started) * 1000),
        answer_chars=len(answer),
        discovered_doc_count=len(all_doc_ids),
        rag_document_count=len(rag_docs),
        discovery=_discovery_summary(discovery),
    )
    return answer, list(all_doc_ids), rag_docs, None


def _generate_source_links(chunk: dict, doc_info: dict | None) -> tuple[str, str]:
    """Return (display_label, clickable_url) for a chunk.

    - clickable_url: presigned S3 URL with #page=N when a PDF is in S3; otherwise the
      original source_url from Neptune (gov website link); otherwise empty.
    - display_label: the gov source_url as a short label, or the doc title, or "".
      Used as the badge text so users don't see a raw presigned URL.
    """
    s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key") or ""
    start_page = chunk.get("start_page")
    gov_source_url = chunk.get("source_url") or (doc_info or {}).get("source_url") or ""

    clickable_url = ""
    if RAW_BUCKET and s3_key and s3_key.endswith(".pdf"):
        try:
            presigned = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": RAW_BUCKET, "Key": s3_key},
                ExpiresIn=PRESIGNED_URL_EXPIRY,
            )
            # Always include #page so the browser opens the cited page; default to 1
            # when start_page is missing so the fragment form stays consistent.
            page = start_page if start_page else 1
            clickable_url = f"{presigned}#page={page}"
        except Exception:
            logger.warning(f"Failed to generate presigned URL for {s3_key}", exc_info=True)

    if not clickable_url:
        clickable_url = gov_source_url

    display_label = gov_source_url or (doc_info or {}).get("title", "")
    return display_label, clickable_url


_CASE_LAW_STUB_PREFIX = "case-law-"


def _is_case_law_stub(doc_id: str) -> bool:
    """True when a doc_id is a case-law citation stub.

    Stubs are produced by upload_local_docs.make_doc_id("case-law", citation),
    which is the exact inverse of citation_to_raw_slug used by fetch_case_opinion.
    Prefix check is sufficient and avoids an extra Neptune round-trip.
    """
    return doc_id.startswith(_CASE_LAW_STUB_PREFIX)


# Priority for merging discovery tags across parallel-citation stubs. Earlier
# = stronger signal. Ensures a title-merged card keeps the most informative
# provenance (e.g., "opinion-fetched" wins over "vector-search").
_DISCOVERY_TAG_PRIORITY = [
    "opinion-fetched",
    "fetched",
    "vector-search",
    "graph-neighbor",
    "framework-list",
    "unknown",
]


# Separators that introduce a non-case-name suffix in LLM-classified titles:
# em-dash, en-dash, colon, or " - ". Also a comma followed by a 4-digit
# year (matches reporter citations like ", 2022 WI 17"). Everything before
# the earliest match is the canonical case name.
_CASE_NAME_SUFFIX_RE = re.compile(
    r"\s*(?:[–—:]|-\s)|,\s*(?=\d{4}\b)"
)

# 4-digit year appearing anywhere in the title. Used alongside the case
# name to disambiguate reused names (e.g., "Smith v. Jones" in 2001 vs 2015).
_YEAR_RE = re.compile(r"\b(\d{4})\b")


def _extract_case_name(title: str) -> str:
    """Canonical case-name portion of a title, lowercased and whitespace-collapsed.

    Cuts at the first case-name/suffix separator so descriptive tails
    written by the LLM classifier don't defeat the merge:
      "Fee and Fogarty v. Town of Florence Board of Review – Court of ..."
      "Fee and Fogarty v. Town of Florence Board of Review – Property ..."
    both reduce to "fee and fogarty v. town of florence board of review".
    """
    match = _CASE_NAME_SUFFIX_RE.search(title)
    core = title[: match.start()] if match else title
    return " ".join(core.lower().split())


def _extract_year(title: str) -> str | None:
    """First 4-digit year in the title, or None if absent.

    Used alongside the case name to avoid over-merging reused names
    decided in different years. A title without a year is treated as
    compatible with any year (the LLM classifier sometimes drops the
    citation suffix, so one citation's title may have a year and its
    parallel citation's title may not).
    """
    match = _YEAR_RE.search(title)
    return match.group(1) if match else None


def _collapse_case_law_by_title(
    docs_by_id: dict[str, RAGDocument],
) -> dict[str, RAGDocument]:
    """Merge case-law RAGDocuments that share a normalized title.

    Parallel citations of the same decision (e.g., '401 Wis. 2d 27' and
    '972 N.W.2d 544') become separate Neptune Document nodes during ingest,
    so the agent sees them as distinct IDs. The LLM classifier gives them
    the same title (the case name), which lets us collapse them back into
    one sidebar card at query time without touching the graph.

    Non-case-law documents and stubs with unique titles pass through
    untouched.
    """
    # Bucket by case-name core, then subdivide by year. A bucket with no
    # year contributes to every year-bucket for the same case name, which
    # handles the case where one ingest got "Case, 2018 WI 45" but a
    # parallel citation's title lost the year suffix.
    by_name: dict[str, dict[str | None, list[tuple[str, RAGDocument]]]] = {}
    passthrough: dict[str, RAGDocument] = {}

    for doc_id, rag_doc in docs_by_id.items():
        if not _is_case_law_stub(doc_id):
            passthrough[doc_id] = rag_doc
            continue
        name_key = _extract_case_name(rag_doc.title)
        year_key = _extract_year(rag_doc.title)
        by_name.setdefault(name_key, {}).setdefault(year_key, []).append(
            (doc_id, rag_doc)
        )

    # Resolve each case-name's year buckets into final groups. Rules:
    # - Docs with a specific year merge only with other docs of that year
    #   or docs with no year at all.
    # - Docs with no year attach to the "most popular" year bucket for
    #   that case name so they don't form a parallel untagged group that
    #   stays un-merged.
    groups: list[list[tuple[str, RAGDocument]]] = []
    for year_buckets in by_name.values():
        yearless = year_buckets.pop(None, [])
        if not year_buckets:
            # Every doc for this case name lacks a year → single group.
            if yearless:
                groups.append(yearless)
            continue

        # Attach yearless docs to the largest year bucket. Ties resolved
        # by lexicographic year (stable — deterministic across runs).
        dominant_year = max(
            year_buckets.keys(),
            key=lambda y: (len(year_buckets[y]), y),
        )
        year_buckets[dominant_year].extend(yearless)

        for bucket in year_buckets.values():
            groups.append(bucket)

    merged: dict[str, RAGDocument] = dict(passthrough)
    for group in groups:
        if len(group) == 1:
            doc_id, rag_doc = group[0]
            merged[doc_id] = rag_doc
            continue

        # Pick the best-tagged doc as the card's identity; fall back to the
        # longest content when tags tie (richer preview wins).
        def sort_key(item: tuple[str, RAGDocument]) -> tuple[int, int]:
            _, doc = item
            tag_rank = (
                _DISCOVERY_TAG_PRIORITY.index(doc.discovery_tag)
                if doc.discovery_tag in _DISCOVERY_TAG_PRIORITY
                else len(_DISCOVERY_TAG_PRIORITY)
            )
            return (tag_rank, -len(doc.content or ""))

        group.sort(key=sort_key)
        primary_id, primary_doc = group[0]

        # Concatenate distinct content chunks; parallel citations often
        # reference the case from different host PDFs, so merging gives
        # a richer preview than picking one.
        seen_texts: set[str] = set()
        merged_parts: list[str] = []
        for _, doc in group:
            text = (doc.content or "").strip()
            if text and text not in seen_texts:
                seen_texts.add(text)
                merged_parts.append(text)
        merged_content = "\n\n".join(merged_parts)

        merged[primary_id] = RAGDocument(
            document_id=primary_doc.document_id,
            title=primary_doc.title,
            content=merged_content or primary_doc.content,
            source=primary_doc.source,
            source_url=primary_doc.source_url,
            discovery_tag=primary_doc.discovery_tag,
        )
        logger.info(
            f"Collapsed {len(group)} case-law parallel citations into "
            f"'{primary_doc.title[:60]}' (ids: {[g[0] for g in group]})"
        )

    return merged


def _build_opinion_card(stub_doc_id: str, payload: dict) -> RAGDocument:
    """Build a RAGDocument for a fetched full court opinion.

    Supersedes the one-chunk case-law stub card for this citation. Links
    directly to the opinion .txt in S3 via presigned URL; falls back to
    Google Scholar when S3 presigning fails.
    """
    citation = payload.get("citation", "")
    raw_key = payload.get("raw_key", "")
    opinion_text = payload.get("text", "")
    scholar_url = payload.get("scholar_url", "")

    doc_info = neptune.get_document(stub_doc_id) or {}
    title = doc_info.get("title") or citation or stub_doc_id
    content_hash = hashlib.sha256(stub_doc_id.encode()).hexdigest()[:7]

    clickable_url = ""
    if RAW_BUCKET and raw_key:
        try:
            clickable_url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": RAW_BUCKET, "Key": raw_key},
                ExpiresIn=PRESIGNED_URL_EXPIRY,
            )
        except Exception:
            logger.warning(f"Failed to presign opinion URL for {raw_key}", exc_info=True)
    if not clickable_url:
        clickable_url = scholar_url

    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        source_url=clickable_url,
        discovery_tag="opinion-fetched",
    )


def _build_rag_documents(
    chunks: list[dict],
    doc_ids: set[str],
    discovery: dict[str, str] | None = None,
    fetched_opinions: dict[str, dict] | None = None,
) -> list[RAGDocument]:
    """Build RAGDocument list from collected chunks, tagged by how discovered.

    When the agent called fetch_case_opinion, the fetched opinion supersedes
    the one-chunk case-law stub for that citation, and other case-law stubs
    that came in as graph/framework noise are suppressed — the agent is
    clearly working on a specific case, not surveying the case-law corpus.
    """
    discovery = discovery or {}
    fetched_opinions = fetched_opinions or {}
    docs_by_id: dict[str, RAGDocument] = {}

    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text") or ""
        tag = discovery.get(doc_id, "unknown")

        if doc_id not in docs_by_id:
            doc_info = neptune.get_document(doc_id)
            title = (doc_info.get("title") if doc_info else None) or doc_id
            content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
            source, source_url = _generate_source_links(chunk, doc_info)

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=source,
                source_url=source_url,
                discovery_tag=tag,
            )
        else:
            existing = docs_by_id[doc_id]
            fallback_source, fallback_url = _generate_source_links(chunk, None)
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source or fallback_source,
                source_url=existing.source_url or fallback_url,
                discovery_tag=existing.discovery_tag,
            )

    # Include cited docs that had no chunks (e.g., fetched-only).
    # Skip any node that isn't a real document: get_document matches on id
    # only, so a Chunk/Topic/Framework id would otherwise come back with
    # None title/summary and fail RAGDocument validation.
    for doc_id in doc_ids - docs_by_id.keys():
        doc_info = neptune.get_document(doc_id)
        if not doc_info:
            continue
        labels = doc_info.get("labels") or []
        if any(label in _NON_DOCUMENT_LABELS for label in labels):
            continue
        content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
        tag = discovery.get(doc_id, "unknown")
        source, source_url = _generate_source_links({}, doc_info)
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title") or doc_id,
            content=doc_info.get("summary") or "",
            source=source,
            source_url=source_url,
            discovery_tag=tag,
        )

    if fetched_opinions:
        # Replace stub cards for fetched citations with richer opinion cards,
        # and drop other case-law stubs that leaked in as graph/framework noise.
        for stub_doc_id, payload in fetched_opinions.items():
            docs_by_id[stub_doc_id] = _build_opinion_card(stub_doc_id, payload)

        fetched_ids = set(fetched_opinions.keys())
        noise_tags = {"graph-neighbor", "framework-list"}
        docs_by_id = {
            doc_id: rag_doc
            for doc_id, rag_doc in docs_by_id.items()
            if doc_id in fetched_ids
            or not (_is_case_law_stub(doc_id) and rag_doc.discovery_tag in noise_tags)
        }

    docs_by_id = _collapse_case_law_by_title(docs_by_id)

    return list(docs_by_id.values())


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
        _log_agent_event(
            "agentic_retrieval_request_received",
            request_id=request_id,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            **_query_log_fields(user_query.query),
        )

        chat_history = get_chat_history(session_id)
        answer, cited_doc_ids, rag_documents, faq_resource = run_agentic_loop(
            user_query.query,
            chat_history=chat_history,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            request_id=request_id,
        )

        documents = DocumentResource(documents=rag_documents)
        _log_agent_event(
            "agentic_retrieval_response_ready",
            request_id=request_id,
            query_id=user_query.query_id,
            session_id=user_query.session_id,
            answer_chars=len(answer),
            cited_doc_count=len(cited_doc_ids),
            rag_document_count=len(rag_documents),
            faq_count=len(faq_resource.faqs) if faq_resource else 0,
        )

        # Return a flat payload; Step Functions Pass states build both
        # generate_response_job and stream_documents_job from shared fields
        # to avoid duplicating documents (keeps payload under 256KB limit).
        return {
            "successful": True,
            "query": user_query.query,
            "query_id": user_query.query_id,
            "session_id": user_query.session_id,
            "faqs": faq_resource.model_dump() if faq_resource else None,
            "documents": documents.model_dump(),
        }

    except Exception as e:
        _log_agent_event(
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
