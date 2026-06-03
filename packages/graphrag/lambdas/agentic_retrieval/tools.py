"""
Tool definitions for Claude's agentic retrieval loop.
Maps Neptune capabilities to Bedrock Converse tool_use format.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any

import boto3

from case_opinion import fetch_case_opinion
from neptune_client import NeptuneClient
from wpam_dedup import dedupe_wpam_chunks

logger = logging.getLogger(__name__)
LOG_TOOL_TRACE = os.environ.get("LOG_TOOL_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))

REGION = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

FAQ_KNOWLEDGE_BASE_ID = os.environ.get("FAQ_KNOWLEDGE_BASE_ID", "")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
REFINEMENT_MODEL_ID = os.environ.get(
    "AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)


def _truncate_text(value: str, max_chars: int = LOG_MAX_TEXT_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"...[truncated {len(value) - max_chars} chars]"


def _compact_log_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, dict):
        return {str(k): _compact_log_value(v) for k, v in value.items()}
    if isinstance(value, list):
        compact = [_compact_log_value(v) for v in value[:10]]
        if len(value) > 10:
            compact.append(f"...[{len(value) - 10} more]")
        return compact
    return value


def _log_tool_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    if not LOG_TOOL_TRACE and level < logging.WARNING:
        return
    payload = {
        "component": "graphrag.agentic_retrieval.tools",
        "event": event,
        **fields,
    }
    logger.log(
        level,
        json.dumps(_compact_log_value(payload), default=str, separators=(",", ":")),
    )


def _query_fields(query: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        "query_chars": len(query),
    }
    if LOG_QUERY_TEXT:
        fields["query_preview"] = _truncate_text(query)
    return fields


def _history_context(chat_history: list[dict[str, str]] | None) -> str:
    if not chat_history:
        return "(no prior conversation)"
    turns = []
    for idx, turn in enumerate(chat_history, start=1):
        turns.append(
            f"Turn {idx}\nUser: {turn.get('query', '')}\nAssistant: {turn.get('answer', '')}"
        )
    return "\n\n".join(turns)


TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "refine_query",
            "description": (
                "Rewrite the user's question into a focused search query "
                "before calling faq_search or vector_search. Call this "
                "when: (1) the user's question is a short follow-up that "
                "depends on earlier conversation (e.g., 'what about "
                "agriculture' with no other context), OR (2) the question "
                "uses casual phrasing unlikely to match document "
                "vocabulary (e.g., 'my land', 'can I'), OR (3) the "
                "question has obvious typos or is very brief. Returns a "
                "single rewritten query string you should pass to the "
                "next search tool. Do NOT call this for already-specific "
                "questions — it adds a turn for no gain."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The original user question to refine."
                            ),
                        }
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "faq_search",
            "description": (
                "Search frequently asked questions about Wisconsin DOR property "
                "assessment and taxation. Returns Q&A pairs ranked by relevance. "
                "Always try this FIRST before vector_search — if a FAQ adequately "
                "answers the user's question, use it directly via the answer tool."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant FAQs",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of FAQ results to return (default: 5, max: 10)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "vector_search",
            "description": (
                "Search for relevant document chunks using semantic similarity. "
                "Returns the most relevant text chunks from Wisconsin DOR documents. "
                "Always start with this tool to find relevant content."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant chunks",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10, max: 20)",
                            "default": 10,
                        },
                        "target_wpam_year": {
                            "type": ["integer", "null"],
                            "description": (
                                "Optional. If the user explicitly asked about a "
                                "specific WPAM edition year, pass it here so dedup "
                                "returns chunks from that edition instead of the "
                                "most recent. Use the value returned by refine_query."
                            ),
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_document",
            "description": (
                "Fetch a specific document's metadata by its ID. "
                "Use this when you have a document ID from vector_search results "
                "and need more details like title, summary, authority level."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": "The document ID to look up",
                        }
                    },
                    "required": ["doc_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_neighbors",
            "description": (
                "Traverse graph edges from a document to find related nodes. "
                "Use this to find what a document CITES, IMPLEMENTS, is PART_OF, "
                "SUPPLEMENTS, SUPERSEDES, or is RELATED_TO. "
                "Critical for finding authoritative sources and newer guidance."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node ID to get neighbors for",
                        },
                        "edge_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Filter by edge types. Options: CITES, IMPLEMENTS, PART_OF, "
                                "BELONGS_TO, DERIVED_FROM, COVERS_TOPIC, EXTRACTED_FROM, "
                                "HAS_SUBSECTION, SUPPLEMENTS, SUPERSEDES, "
                                "CONFLICTS_WITH, RELATED_TO"
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming", "both"],
                            "description": "Edge direction (default: both)",
                            "default": "both",
                        },
                        "target_wpam_year": {
                            "type": ["integer", "null"],
                            "description": (
                                "Optional. If the user explicitly asked about a "
                                "specific WPAM edition year, pass it here so dedup "
                                "returns chunks from that edition instead of the "
                                "most recent. Use the value returned by refine_query."
                            ),
                        },
                    },
                    "required": ["node_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_authority_chain",
            "description": (
                "Trace the governance hierarchy from a document up to the root authority. "
                "Returns the chain: Document -> Section -> Chapter -> Framework -> Constitution. "
                "Use this to understand what level of authority backs a "
                "particular rule or guidance."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node ID to trace authority from",
                        }
                    },
                    "required": ["node_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_framework_docs",
            "description": (
                "List all documents belonging to a framework/authority level. "
                "Framework IDs: FW-CONSTITUTION, FW-STATUTES, FW-ADMIN-RULES, "
                "FW-WPAM, FW-FAQ, FW-GOV-PUBS"
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "framework_id": {
                            "type": "string",
                            "description": "The framework ID to list documents for",
                        }
                    },
                    "required": ["framework_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "fetch_case_opinion",
            "description": (
                "Fetch the full text of a Wisconsin court opinion by citation. "
                "Use this ONLY as a LAST resort: (1) the primary sources "
                "(statutes, admin rules, WPAM, FAQs) you've already gathered "
                "are insufficient, AND (2) the case's ANNOTATION chunks "
                "already in context do not contain enough detail, AND (3) the "
                "user's question turns on the court's specific analysis or "
                "holding. Do NOT call this to 'confirm' information the "
                "annotation already shows. Case-law documents include the "
                "citation you need (e.g., '109 Wis. 2d 290'). Returns opinion "
                "text if available in our S3 archive, otherwise a Google "
                "Scholar search URL."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "citation": {
                            "type": "string",
                            "description": (
                                "Legal citation exactly as it appears on the "
                                "CaseLaw document, e.g. '109 Wis. 2d 290' or "
                                "'2000 WI App 182'."
                            ),
                        }
                    },
                    "required": ["citation"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "answer",
            "description": (
                "Provide the final answer to the user's question with citations. "
                "Call this tool when you have gathered enough information. "
                "Include specific document references and section numbers."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "response": {
                            "type": "string",
                            "description": "The complete answer with citations in Markdown",
                        },
                        "cited_doc_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of document IDs cited in the response",
                        },
                    },
                    "required": ["response", "cited_doc_ids"],
                }
            },
        }
    },
]


def embed_query(query: str, model_id: str = "amazon.titan-embed-text-v2:0") -> list[float]:
    """Embed a query string for vector search."""
    started = time.perf_counter()
    body = json.dumps({
        "inputText": query[:8000],
        "dimensions": 1024,
        "normalize": True,
    })
    try:
        response = bedrock.invoke_model(
            modelId=model_id, body=body,
            contentType="application/json", accept="application/json",
        )
        embedding = json.loads(response["body"].read())["embedding"]
    except Exception as exc:
        _log_tool_event(
            "embedding_error",
            logging.ERROR,
            model_id=model_id,
            error_type=type(exc).__name__,
            error=str(exc),
            **_query_fields(query),
        )
        raise
    _log_tool_event(
        "embedding_complete",
        model_id=model_id,
        latency_ms=round((time.perf_counter() - started) * 1000),
        embedding_dimensions=len(embedding),
        **_query_fields(query),
    )
    return embedding


def execute_tool(
    tool_name: str,
    tool_input: dict,
    neptune: NeptuneClient,
    chat_history: list[dict[str, str]] | None = None,
) -> dict:
    """Execute a tool call and return the result.

    ``chat_history`` is threaded through for ``refine_query`` only. Other
    branches ignore it.
    """
    started = time.perf_counter()
    _log_tool_event(
        "tool_execute_start",
        tool_name=tool_name,
        tool_input=tool_input,
    )

    if tool_name == "faq_search":
        if not FAQ_KNOWLEDGE_BASE_ID:
            return {"error": "FAQ knowledge base not configured"}
        top_k = min(tool_input.get("top_k", 5), 10)
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=FAQ_KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": tool_input["query"]},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": top_k,
                    "overrideSearchType": "SEMANTIC",
                }
            },
        )
        faqs = []
        for result in response.get("retrievalResults", []):
            text = result.get("content", {}).get("text", "")
            score = result.get("score", 0.0)
            source_uri = (
                result.get("location", {}).get("s3Location", {}).get("uri", "")
            )
            faqs.append({"text": text, "score": score, "source_uri": source_uri})
        _log_tool_event(
            "faq_search_complete",
            tool_name=tool_name,
            knowledge_base_id=FAQ_KNOWLEDGE_BASE_ID,
            top_k=top_k,
            faq_count=len(faqs),
            latency_ms=round((time.perf_counter() - started) * 1000),
            top_scores=[round(faq.get("score", 0.0), 4) for faq in faqs[:5]],
            **_query_fields(tool_input["query"]),
        )
        return {"faqs": faqs, "count": len(faqs)}

    elif tool_name == "refine_query":
        query = tool_input["query"]
        prompt = (
            "Rewrite the current user question as one standalone search query for "
            "Wisconsin property tax retrieval. Use the prior conversation only to "
            "resolve references or missing context.\n\n"
            "Also: if the user explicitly mentions a 4-digit year (e.g., '2018', "
            "'the 2024 manual') AND the question is about WPAM / Wisconsin Property "
            "Assessment Manual / property assessment guidance, populate "
            "target_wpam_year with that year. Otherwise, target_wpam_year is null. "
            "A year that refers only to a tax-filing deadline or a statute year is "
            "NOT a target_wpam_year.\n\n"
            "Return ONLY a JSON object on a single line, no prose, no markdown:\n"
            '{"refined_query": "<rewritten query>", "target_wpam_year": <year or null>}\n\n'
            f"Prior conversation:\n{_history_context(chat_history)}\n\n"
            f"Current question: {query}"
        )
        target_year: int | None = None
        refined = query
        try:
            response = bedrock.converse(
                modelId=REFINEMENT_MODEL_ID,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 256, "temperature": 0.0},
            )
            message = response["output"]["message"]
            raw = " ".join(
                block.get("text", "").strip()
                for block in message.get("content", [])
                if block.get("text")
            ).strip()
            try:
                parsed = json.loads(raw)
                refined = str(parsed.get("refined_query", "")).strip()
                year_value = parsed.get("target_wpam_year")
                if isinstance(year_value, int) and not isinstance(year_value, bool):
                    target_year = year_value
            except (json.JSONDecodeError, AttributeError, TypeError):
                # LLM didn't return JSON — treat output as refined query, no target year
                refined = raw
        except Exception as exc:  # noqa: BLE001
            _log_tool_event(
                "refine_query_error",
                logging.WARNING,
                tool_name=tool_name,
                error_type=type(exc).__name__,
                error=str(exc),
                **_query_fields(query),
            )
            refined = query

        if not refined:
            refined = query
        _log_tool_event(
            "refine_query_complete",
            tool_name=tool_name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            refined_query=refined,
            target_wpam_year=target_year,
            history_turns=len(chat_history or []),
            **_query_fields(query),
        )
        return {"refined_query": refined, "target_wpam_year": target_year}

    elif tool_name == "vector_search":
        embedding = embed_query(tool_input["query"])
        top_k = min(tool_input.get("top_k", 10), 20)
        vector_started = time.perf_counter()
        chunks = neptune.vector_search(embedding, top_k=top_k)
        target_year = tool_input.get("target_wpam_year")
        pre_dedup_count = len(chunks)
        chunks = dedupe_wpam_chunks(chunks, target_year=target_year)
        _log_tool_event(
            "vector_search_neptune_complete",
            tool_name=tool_name,
            top_k=top_k,
            chunk_count=len(chunks),
            pre_dedup_count=pre_dedup_count,
            target_wpam_year=target_year,
            latency_ms=round((time.perf_counter() - vector_started) * 1000),
            top_doc_ids=[chunk.get("doc_id") for chunk in chunks[:5]],
            **_query_fields(tool_input["query"]),
        )

        # Auto-enrichment: graph neighbors for top-3 distinct parent docs.
        # From docs/graphrag.md §1: gives the agent graph context for free.
        graph_context: dict[str, list[dict]] = {}
        seen: list[str] = []
        for chunk in chunks:
            doc_id = chunk.get("doc_id", "")
            if doc_id and doc_id not in seen:
                seen.append(doc_id)
                if len(seen) >= 3:
                    break

        for doc_id in seen:
            try:
                enrich_started = time.perf_counter()
                neighbors = neptune.get_neighbors(doc_id)
                if neighbors:
                    graph_context[doc_id] = neighbors
                _log_tool_event(
                    "vector_search_auto_enrichment_complete",
                    tool_name=tool_name,
                    doc_id=doc_id,
                    neighbor_count=len(neighbors),
                    latency_ms=round((time.perf_counter() - enrich_started) * 1000),
                )
            except Exception:  # noqa: BLE001 — best-effort enrichment
                _log_tool_event(
                    "vector_search_auto_enrichment_error",
                    logging.WARNING,
                    tool_name=tool_name,
                    doc_id=doc_id,
                    error="auto-enrichment failed; continuing without neighbors",
                )
                logger.warning(
                    f"auto-enrichment failed for {doc_id}; continuing without neighbors",
                    exc_info=True,
                )

        _log_tool_event(
            "vector_search_complete",
            tool_name=tool_name,
            top_k=top_k,
            chunk_count=len(chunks),
            graph_context_doc_count=len(graph_context),
            graph_context_neighbor_count=sum(len(v) for v in graph_context.values()),
            latency_ms=round((time.perf_counter() - started) * 1000),
            **_query_fields(tool_input["query"]),
        )
        return {"chunks": chunks, "graph_context": graph_context}

    elif tool_name == "get_document":
        # The model occasionally passes `node_id` (the param name used by
        # get_neighbors/get_authority_chain) instead of `doc_id`. Accept the
        # alias and a missing id gracefully — indexing tool_input["doc_id"]
        # directly raised KeyError and crashed the entire agent loop.
        requested_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        doc = neptune.get_document(requested_id) if requested_id else None
        if doc:
            _log_tool_event(
                "get_document_complete",
                tool_name=tool_name,
                status="found",
                doc_id=doc.get("id"),
                doc_type=doc.get("doc_type"),
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            return {"document": doc}
        # Fallback: vector search on the ID string itself. Handles typos
        # and format mismatches (e.g., user capitalization differences).
        try:
            matches = (
                neptune.vector_search(embed_query(requested_id), top_k=5)
                if requested_id
                else []
            )
        except Exception:  # noqa: BLE001
            matches = []
        _log_tool_event(
            "get_document_complete",
            tool_name=tool_name,
            status="miss",
            doc_id=requested_id,
            fallback_match_count=len(matches),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {
            "error": f"Document '{requested_id}' not found",
            "fallback_matches": matches,
        }

    elif tool_name == "get_neighbors":
        neighbors = neptune.get_neighbors(
            tool_input["node_id"],
            edge_types=tool_input.get("edge_types"),
            direction=tool_input.get("direction", "both"),
        )
        target_year = tool_input.get("target_wpam_year")
        neighbors = dedupe_wpam_chunks(neighbors, target_year=target_year)
        _log_tool_event(
            "get_neighbors_complete",
            tool_name=tool_name,
            node_id=tool_input["node_id"],
            edge_types=tool_input.get("edge_types"),
            direction=tool_input.get("direction", "both"),
            target_wpam_year=target_year,
            neighbor_count=len(neighbors),
            relationships=sorted({
                n.get("relationship", "") for n in neighbors if n.get("relationship")
            }),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"neighbors": neighbors}

    elif tool_name == "get_authority_chain":
        chain = neptune.get_authority_chain(tool_input["node_id"])
        _log_tool_event(
            "get_authority_chain_complete",
            tool_name=tool_name,
            node_id=tool_input["node_id"],
            chain_length=len(chain),
            chain_ids=[node.get("id") for node in chain[:10]],
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"authority_chain": chain}

    elif tool_name == "list_framework_docs":
        docs = neptune.list_framework_docs(tool_input["framework_id"])
        _log_tool_event(
            "list_framework_docs_complete",
            tool_name=tool_name,
            framework_id=tool_input["framework_id"],
            document_count=len(docs),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"documents": docs}

    elif tool_name == "fetch_case_opinion":
        if not RAW_BUCKET:
            return {"error": "Raw bucket not configured"}
        citation = tool_input.get("citation", "")
        result = fetch_case_opinion(citation, raw_bucket=RAW_BUCKET)
        _log_tool_event(
            "fetch_case_opinion_complete",
            tool_name=tool_name,
            found=result.get("found", False),
            citation=citation,
            raw_key=result.get("raw_key", ""),
            opinion_chars=len(result.get("text", "")),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return result

    elif tool_name == "answer":
        _log_tool_event(
            "answer_tool_complete",
            tool_name=tool_name,
            response_chars=len(tool_input.get("response", "")),
            cited_doc_count=len(tool_input.get("cited_doc_ids", [])),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return tool_input

    else:
        _log_tool_event(
            "tool_execute_unknown",
            logging.WARNING,
            tool_name=tool_name,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"error": f"Unknown tool: {tool_name}"}
