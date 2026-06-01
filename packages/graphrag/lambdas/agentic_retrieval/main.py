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
import itertools
import json
import logging
import os
import re
import time
from typing import Any

import boto3
import pydantic
from case_opinion import citation_to_raw_slug, scholar_url as _scholar_url_fn
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
from websocket_utils.models import AgentEventMessage
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
neptune = NeptuneClient()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "")

AGENTIC_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
LOG_AGENT_TRACE = os.environ.get("LOG_AGENT_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))
EMIT_AGENT_TRACE = os.environ.get("EMIT_AGENT_TRACE", "true").lower() == "true"


def _truncate_text(value: str, max_chars: int = LOG_MAX_TEXT_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"...[truncated {len(value) - max_chars} chars]"


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


def _build_tool_call_summary(tool_name: str, tool_input: dict) -> str:
    """Short prose describing a tool call for the UI trace.

    Returns an empty string for unknown tools — the UI then shows just
    the verb, which is still informative.
    """
    if tool_name in ("vector_search", "faq_search", "refine_query"):
        query = tool_input.get("query", "")
        return f'"{query}"' if query else ""
    if tool_name == "get_neighbors":
        doc_id = tool_input.get("doc_id", "")
        return f"doc {doc_id}" if doc_id else ""
    if tool_name == "get_document":
        doc_id = tool_input.get("doc_id", "")
        return doc_id
    if tool_name == "get_authority_chain":
        doc_id = tool_input.get("doc_id", "")
        return f"doc {doc_id}" if doc_id else ""
    if tool_name == "list_framework_docs":
        framework = tool_input.get("framework_name", "")
        return framework
    if tool_name == "fetch_case_opinion":
        citation = tool_input.get("citation", "")
        return citation
    if tool_name == "answer":
        cited = tool_input.get("cited_doc_ids", []) or []
        return f"with {len(cited)} cited doc(s)"
    return ""


# Allow-list for keys that may appear in tool_result.payload.metadata.
# Anything outside this set is dropped at emission so future contributors
# can't accidentally surface raw user text (or other free-form content)
# in the frontend trace. New keys must be added here *and* to the
# matching frontend allow-list in trace-metadata.ts.
ALLOWED_METADATA_KEYS = frozenset({
    "chunkCount",
    "docCount",
    "neighborCount",
    "topScore",
    "faqCount",
    "documentCount",
    "chainLength",
    "opinionChars",
    "refined",
    "citedDocCount",
    "latencyMs",
})


def _filter_metadata(metadata: Any) -> dict[str, Any]:
    """Drop any keys not in ALLOWED_METADATA_KEYS, log on drops."""
    if not isinstance(metadata, dict):
        return {}
    dropped = [k for k in metadata if k not in ALLOWED_METADATA_KEYS]
    if dropped:
        logger.warning(
            "trace metadata dropped disallowed key(s): %s",
            ", ".join(sorted(dropped)),
        )
    return {k: v for k, v in metadata.items() if k in ALLOWED_METADATA_KEYS}


def _build_tool_result_summary(tool_name: str, result: dict) -> dict:
    """Build a UI-friendly summary of a tool result.

    Returns a dict with:
      - status: 'ok' | 'error' | 'miss' | 'terminal'
      - summary_text: one-line human-readable string
      - doc_ids: list of up to 10 document IDs referenced in the result
      - doc_titles: list aligned with doc_ids (each entry is the title or the
        doc_id on failure, so lengths always match)
      - metadata: camelCase dict of counts/scores for the UI subtitle
      - raw: output of _summarize_tool_result (dev-mode payload)
    """
    raw = _summarize_tool_result(tool_name, result)
    status = raw.get("status", "ok")
    doc_ids: list[str] = []
    summary_text = ""
    metadata: dict[str, Any] = {}

    if "error" in result:
        return {
            "status": "error",
            "summary_text": str(result.get("error") or "tool error"),
            "doc_ids": [],
            "doc_titles": [],
            "metadata": {},
            "raw": raw,
        }

    if tool_name == "vector_search":
        chunks = result.get("chunks", [])
        seen_docs: set[str] = set()
        ordered_docs: list[str] = []
        for chunk in chunks:
            doc_id = chunk.get("doc_id")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                ordered_docs.append(doc_id)
        doc_ids = ordered_docs[:10]
        summary_text = (
            f"Found {len(chunks)} chunks across {len(ordered_docs)} doc(s)"
        )
        top_score = max(
            (float(c.get("score", 0.0)) for c in chunks),
            default=0.0,
        )
        graph_context = result.get("graph_context", {}) or {}
        metadata = {
            "chunkCount": len(chunks),
            "docCount": len(ordered_docs),
            "neighborCount": sum(len(v) for v in graph_context.values()),
            "topScore": round(top_score, 4),
        }

    elif tool_name == "faq_search":
        faqs = result.get("faqs", [])
        top = faqs[0].get("score", 0.0) if faqs else 0.0
        summary_text = (
            f"FAQ top score {top:.2f} ({len(faqs)} hit(s))"
            if faqs
            else "No FAQ matches"
        )
        metadata = {"faqCount": len(faqs), "topScore": round(float(top), 4)}

    elif tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        doc_ids = [n["id"] for n in neighbors if n.get("id")][:10]
        summary_text = f"Pulled {len(neighbors)} neighbor(s)"
        metadata = {"neighborCount": len(neighbors)}

    elif tool_name == "get_document":
        doc = result.get("document")
        if doc:
            doc_ids = [doc.get("id")] if doc.get("id") else []
            summary_text = f"Fetched {doc.get('doc_type', 'document')} {doc.get('id', '')}"
            metadata = {"documentCount": 1}
        else:
            summary_text = "Document not found"
            status = "miss"
            metadata = {"documentCount": 0}

    elif tool_name == "get_authority_chain":
        chain = result.get("authority_chain", [])
        doc_ids = [n["id"] for n in chain if n.get("id")][:10]
        summary_text = f"Walked authority chain ({len(chain)} node(s))"
        metadata = {"chainLength": len(chain)}

    elif tool_name == "list_framework_docs":
        docs = result.get("documents", [])
        doc_ids = [d["id"] for d in docs if d.get("id")][:10]
        summary_text = f"Listed {len(docs)} framework doc(s)"
        metadata = {"documentCount": len(docs)}

    elif tool_name == "fetch_case_opinion":
        citation = result.get("citation", "")
        if result.get("found"):
            summary_text = f"Fetched opinion for {citation}"
            metadata = {"opinionChars": len(result.get("text", ""))}
        else:
            summary_text = f"No opinion found for {citation}"
            status = "miss"
            metadata = {"opinionChars": 0}

    elif tool_name == "refine_query":
        refined = result.get("refined_query", "")
        summary_text = f'Refined to "{refined}"' if refined else "No refinement"
        metadata = {"refined": bool(refined)}

    elif tool_name == "answer":
        cited = result.get("cited_doc_ids", []) or []
        doc_ids = list(cited)[:10]
        summary_text = f"Answer with {len(cited)} cited doc(s)"
        status = "terminal"
        metadata = {"citedDocCount": len(cited)}

    else:
        summary_text = f"{tool_name} complete"

    # Best-effort title resolution. Neptune failures must not break the loop.
    doc_titles: list[str] = []
    for doc_id in doc_ids:
        try:
            info = neptune.get_document(doc_id)
            doc_titles.append((info or {}).get("title") or doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "title resolution failed for doc_id=%s: %s",
                doc_id,
                type(exc).__name__,
            )
            doc_titles.append(doc_id)

    return {
        "status": status,
        "summary_text": summary_text,
        "doc_ids": doc_ids,
        "doc_titles": doc_titles,
        "metadata": metadata,
        "raw": raw,
    }


def _discovery_summary(discovery: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tag in discovery.values():
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def _emit_trace(
    ws_server,
    trace_seq,
    *,
    query_id: str,
    kind: str,
    turn: int | None = None,
    payload: dict | None = None,
    dev_payload: dict | None = None,
) -> None:
    """Push an AgentEventMessage to the client. Best-effort — never raises.

    ws_server may be None (trace disabled or session lookup failed); in that
    case this is a no-op. Any WebSocket exception is logged and swallowed so
    the agentic loop is never blocked by client-side issues.
    """
    if not EMIT_AGENT_TRACE or ws_server is None:
        return
    try:
        message = AgentEventMessage(
            query_id=query_id,
            kind=kind,
            turn=turn,
            seq=trace_seq(),
            timestamp=int(time.time() * 1000),
            payload=payload or {},
            dev_payload=_compact_log_value(dev_payload or {}),
        )
        asyncio.run(ws_server.send_json(message))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit agent-trace event", exc_info=True)

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


def save_chat_history(
    session_id: str,
    query_id: str,
    query: str,
    answer: str,
    rag_documents: list[RAGDocument] | None = None,
    faq_resource: "FAQResource | None" = None,
) -> None:
    """Persist a query/answer pair (with resources) to the chat history table."""
    if not CHAT_HISTORY_TABLE or not session_id:
        return
    try:
        import datetime

        item: dict = {
            "queryId": query_id,
            "sessionId": session_id,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "query": query,
            "answer": answer,
        }

        resources: list[dict] = []
        if rag_documents:
            for doc in rag_documents:
                data: dict = {
                    "documentId": doc.document_id,
                    "title": doc.title,
                    "source": doc.source,
                    "discoveryTag": doc.discovery_tag,
                }
                if doc.source_url is not None:
                    data["sourceUrl"] = doc.source_url
                if doc.s3_key is not None:
                    data["s3Key"] = doc.s3_key
                if doc.start_page is not None:
                    data["startPage"] = doc.start_page
                if doc.end_page is not None:
                    data["endPage"] = doc.end_page
                if doc.edition_year is not None:
                    data["editionYear"] = doc.edition_year
                resources.append({"type": "document", "data": data})
        if faq_resource:
            for faq in faq_resource.faqs:
                resources.append({
                    "type": "faq",
                    "data": {
                        "faqId": faq.faq_id,
                        "question": faq.question,
                        "answer": faq.answer,
                    },
                })
        if resources:
            item["resources"] = resources

        table = dynamodb_resource.Table(CHAT_HISTORY_TABLE)
        table.put_item(Item=item)
        logger.info(f"Saved chat history for session {session_id}, query {query_id}")
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to save chat history for session {session_id}",
            exc_info=True,
        )


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


def _build_cited_faq_resource(
    faq_results: list[dict],
    cited_doc_ids: set[str],
) -> FAQResource | None:
    """Convert cited FAQ KB chunks into FAQResource for downstream synthesis.

    The agent may cite IDs from the seeded FAQ search result. Those IDs are not
    Neptune Document nodes, so they would otherwise be dropped from
    RAGDocument construction and leave ResponseStreaming with no context.
    """
    cited_faq_results = [
        entry
        for entry in faq_results[:MAX_FAQS]
        if _faq_id_from_uri(entry.get("source_uri", "")) in cited_doc_ids
    ]
    return _build_faq_resource(cited_faq_results)


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
    ws_server=None,
    trace_seq=None,
) -> tuple[str, list[str], list[RAGDocument], FAQResource | None]:
    """Run Claude's agentic loop against Neptune.

    Turn 0 is hardcoded: run `faq_search` with the verbatim user query. The
    result is always seeded into the conversation and Claude is handed off to
    graph work. When the top FAQ score clears FAQ_SCORE_THRESHOLD, the FAQ is
    flagged as a high-confidence match: the system message tells Claude to
    treat the FAQ as the primary source of truth and use graph traversal to
    supplement/ground it, and the FAQResource is returned regardless of
    whether the agent's cited_doc_ids reference the FAQ explicitly.

    When ``chat_history`` is provided, prior {query, answer} pairs are
    prepended to the message list so Claude can resolve follow-up questions
    ("what about agriculture") against earlier context, and ``refine_query``
    can reach the same history via execute_tool.

    Returns:
        (answer_text, cited_doc_ids, rag_documents, faq_resource)
    """
    chat_history = chat_history or []
    if trace_seq is None:
        trace_seq = itertools.count(1).__next__
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
        _emit_trace(
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
        refine_summary = _build_tool_result_summary("refine_query", refine_result)
        _emit_trace(
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
                "metadata": _filter_metadata(refine_summary["metadata"]),
            },
            dev_payload={"raw": refine_summary["raw"]},
        )

    trace_context = {
        "query_id": query_id,
        "session_id": session_id,
        "request_id": request_id,
    }
    # Emit loop_start before the FAQ turn 0 so the UI sees a consistent
    # open-event regardless of whether we short-circuit or enter the loop.
    loop_started = time.perf_counter()
    _log_agent_event(
        "agent_loop_start",
        **trace_context,
        model_id=AGENTIC_MODEL_ID,
        max_turns=MAX_TURNS,
        **_query_log_fields(query),
    )
    _emit_trace(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="loop_start",
        payload={"maxTurns": MAX_TURNS},
    )

    # Turn 0: deterministic FAQ search (using the refined query when we have one).
    _emit_trace(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="tool_call",
        turn=0,
        payload={
            "toolName": "faq_search",
            "summary": _build_tool_call_summary("faq_search", {"query": search_query}),
            "status": "pending",
        },
    )
    faq_result = _faq_search_direct(search_query)
    faq_entries = faq_result.get("faqs", [])
    top_score = faq_entries[0].get("score", 0.0) if faq_entries else 0.0
    logger.info(
        f"FAQ turn-0: {len(faq_entries)} hits, top_score={top_score:.3f}, "
        f"threshold={FAQ_SCORE_THRESHOLD}"
    )
    faq_summary = _build_tool_result_summary("faq_search", faq_result)
    _emit_trace(
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
            "metadata": _filter_metadata(faq_summary["metadata"]),
        },
        dev_payload={"raw": faq_summary["raw"]},
    )

    # When the top FAQ scores above threshold, build the resource up front so
    # we can return it unconditionally — even if Claude doesn't include the
    # FAQ ID in cited_doc_ids, the high-confidence FAQ should anchor the
    # downstream synthesis prompt as the primary source of truth.
    high_confidence_faq: FAQResource | None = None
    if top_score >= FAQ_SCORE_THRESHOLD:
        high_confidence_faq = _build_faq_resource(faq_entries)
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

    # When the seeded FAQ is a strong match, tell Claude explicitly to anchor
    # on it. The system prompt covers the general policy, but a per-turn note
    # with the actual score and FAQ ids gives the agent something concrete to
    # latch onto and reduces drift toward graph-only synthesis.
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
        _log_agent_event(
            "agent_turn_start",
            **trace_context,
            turn=turn_number,
            max_turns=MAX_TURNS,
            message_count=len(messages),
            discovered_doc_count=len(all_doc_ids),
            accumulated_chunk_count=len(all_chunks),
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
        assistant_summary = _summarize_assistant_message(assistant_message)
        _log_agent_event(
            "agent_turn_model_response",
            **trace_context,
            turn=turn_number,
            bedrock_latency_ms=converse_latency_ms,
            **_summarize_bedrock_response(response),
            assistant=assistant_summary,
        )
        if assistant_summary["text_preview"]:
            _emit_trace(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="reasoning",
                turn=turn_number,
                payload={"text": assistant_summary["text_preview"]},
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
            _emit_trace(
                ws_server,
                trace_seq,
                query_id=query_id,
                kind="tool_call",
                turn=turn_number,
                payload={
                    "toolName": tool_name,
                    "summary": _build_tool_call_summary(tool_name, tool_input),
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

            tool_result_summary = _build_tool_result_summary(tool_name, result)
            _log_agent_event(
                "agent_tool_result",
                **trace_context,
                turn=turn_number,
                tool_use_id=tool_use_id,
                tool_latency_ms=tool_latency_ms,
                discovered_doc_count=len(all_doc_ids),
                accumulated_chunk_count=len(all_chunks),
                discovery=_discovery_summary(discovery),
                tool_result_summary=tool_result_summary["raw"],
            )
            if tool_name != "answer":
                result_metadata = dict(tool_result_summary["metadata"])
                if tool_latency_ms is not None:
                    result_metadata["latencyMs"] = tool_latency_ms
                result_metadata = _filter_metadata(result_metadata)
                _emit_trace(
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
                # When the seeded FAQ was high-confidence, surface it
                # unconditionally — the prompt asked Claude to cite it, but
                # downstream synthesis must see the FAQ as primary truth even
                # if the agent's cited_doc_ids forgot to include the FAQ id.
                cited_faq_resource = (
                    high_confidence_faq
                    or _build_cited_faq_resource(faq_entries, cited)
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
                    faq_count=len(cited_faq_resource.faqs) if cited_faq_resource else 0,
                    discovery=_discovery_summary(cited_discovery),
                )
                _emit_trace(
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
            accumulated_chunk_count=len(all_chunks),
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
    _emit_trace(
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


def _generate_source_label(chunk: dict, doc_info: dict | None) -> str:
    """Return the display label shown on the citation badge.

    Replaces _generate_source_links: URL construction now happens at click
    time in the citation_resolver Lambda. Cards carry stable s3_key /
    start_page on the RAGDocument; the badge label still uses the gov
    source_url (when present) so users see something semantically
    meaningful, not the doc title.
    """
    gov_source_url = chunk.get("source_url") or (doc_info or {}).get("source_url") or ""
    return gov_source_url or (doc_info or {}).get("title", "")


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
            s3_key=primary_doc.s3_key,
            start_page=primary_doc.start_page,
            end_page=primary_doc.end_page,
            discovery_tag=primary_doc.discovery_tag,
            authority_level=primary_doc.authority_level,
            edition_year=primary_doc.edition_year,
        )
        logger.info(
            f"Collapsed {len(group)} case-law parallel citations into "
            f"'{primary_doc.title[:60]}' (ids: {[g[0] for g in group]})"
        )

    return merged


def _build_opinion_card(stub_doc_id: str, payload: dict) -> RAGDocument:
    """Build a RAGDocument for a fetched full court opinion.

    Supersedes the one-chunk case-law stub card for this citation. The
    resolver mints the presigned URL to the .txt at click time; this
    function only carries the stable s3 reference. scholar_url remains
    available on chunk metadata as a public fallback when the bot
    surfaces the case but the .txt isn't in S3.
    """
    citation = payload.get("citation", "")
    raw_key = payload.get("raw_key", "")
    opinion_text = payload.get("text", "")
    scholar_url = payload.get("scholar_url", "")

    doc_info = neptune.get_document(stub_doc_id) or {}
    title = doc_info.get("title") or citation or stub_doc_id
    content_hash = hashlib.sha256(stub_doc_id.encode()).hexdigest()[:7]

    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        # Always link the user to Google Scholar for the citation, even when
        # the opinion .txt is archived in S3. The S3 object is a flat text
        # blob with no page anchor, so linking to the public opinion loses
        # nothing and gives a properly formatted, citable source. The opinion
        # text still rides in `content` to inform synthesis.
        source_url=scholar_url or (_scholar_url_fn(citation) if citation else None),
        s3_key=None,
        start_page=None,
        end_page=None,
        discovery_tag="opinion-fetched",
        authority_level=doc_info.get("authority_level") if doc_info else 3,
        edition_year=None,
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
            label = _generate_source_label(chunk, doc_info)
            gov_url = chunk.get("source_url") or (doc_info or {}).get("source_url")
            s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key")

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=label,
                # source_url now only carries public gov URLs. S3 references
                # ride on s3_key / start_page / end_page; the resolver mints
                # the presigned URL at click time.
                source_url=gov_url,
                s3_key=s3_key,
                start_page=chunk.get("start_page"),
                end_page=chunk.get("end_page"),
                discovery_tag=tag,
                authority_level=(doc_info or {}).get("authority_level"),
                edition_year=chunk.get("edition_year"),
            )
        else:
            existing = docs_by_id[doc_id]
            # First chunk that supplied an s3_key wins, and start_page/end_page
            # come from the SAME chunk — pairing a key from one chunk with a
            # page from another would point the resolver at the wrong page.
            if existing.s3_key:
                merged_s3_key = existing.s3_key
                merged_start_page = existing.start_page
                merged_end_page = existing.end_page
            else:
                merged_s3_key = chunk.get("s3_key")
                merged_start_page = chunk.get("start_page")
                merged_end_page = chunk.get("end_page")
            merged_edition_year = existing.edition_year or chunk.get("edition_year")
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source,
                source_url=existing.source_url,
                s3_key=merged_s3_key,
                start_page=merged_start_page,
                end_page=merged_end_page,
                discovery_tag=existing.discovery_tag,
                authority_level=existing.authority_level,
                edition_year=merged_edition_year,
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

        # Section-level Statute stubs (e.g., WIS-STAT-70.32) carry id/title
        # only — no summary, no chunks. A card built from this would render
        # empty. Promote to the parent Document (e.g., statutes-70) whose
        # chunks CITE this stub, keeping the stub's title for prose-link
        # continuity but borrowing content + s3_key + page range so the
        # card is informative and clickable.
        if not doc_info.get("summary"):
            promotion = neptune.find_stub_promotion(doc_id)
            if promotion:
                doc_info = {
                    **doc_info,
                    "summary": promotion.get("summary"),
                    "source_url": promotion.get("source_url") or doc_info.get("source_url"),
                    "s3_key": promotion.get("s3_key") or doc_info.get("s3_key"),
                    "authority_level": doc_info.get("authority_level")
                    or promotion.get("authority_level"),
                    "_promoted_start_page": promotion.get("start_page"),
                    "_promoted_end_page": promotion.get("end_page"),
                }

        content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
        tag = discovery.get(doc_id, "unknown")
        label = _generate_source_label({}, doc_info)
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title") or doc_id,
            content=doc_info.get("summary") or "",
            source=label,
            source_url=doc_info.get("source_url"),
            s3_key=doc_info.get("s3_key"),
            start_page=doc_info.get("_promoted_start_page"),
            end_page=doc_info.get("_promoted_end_page"),
            discovery_tag=tag,
            authority_level=doc_info.get("authority_level"),
            edition_year=doc_info.get("edition_year"),
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

        ws_server = None
        if session_id:
            try:
                ws_server = get_ws_connection_from_session(session_id)
            except Exception:  # noqa: BLE001
                # Trace emission is best-effort; the loop must still run.
                logger.warning(
                    "Could not look up WebSocket connection; trace events will be skipped",
                    exc_info=True,
                )
                ws_server = None
        trace_seq = itertools.count(1).__next__

        _emit_trace(
            ws_server,
            trace_seq,
            query_id=user_query.query_id,
            kind="phase",
            payload={"phase": "request_received"},
        )
        if chat_history:
            _emit_trace(
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
