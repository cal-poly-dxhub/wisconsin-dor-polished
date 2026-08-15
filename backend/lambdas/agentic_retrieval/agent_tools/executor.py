"""Tool execution for Claude's agentic retrieval loop.

Maps Neptune capabilities to Bedrock Converse tool_use results.
"""

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import boto3
from case_law import fetch_case_opinion
from graph.neptune_client import NeptuneClient
from worksheets import get_worksheet as load_worksheet
from worksheets import list_worksheets as list_worksheet_registry
from wpam_dedup import dedupe_wpam_chunks

# Regex patterns for Wisconsin case citations.
# Matches: "45 Wis. 2d 683", "45 Wis.2d 683", "985 N.W.2d 69", "2025 WI App 43", "2019 WI 23"
_CITATION_PATTERNS = [
    re.compile(r"\d+\s+Wis\.?\s*2d\s+\d+"),
    re.compile(r"\d+\s+N\.W\.(?:2d|3d)\s+\d+"),
    re.compile(r"\d{4}\s+WI(?:\s+App)?\s+\d+"),
]


def extract_citations(text: str) -> list[str]:
    """Extract unique normalized citation strings from text."""
    raw: set[str] = set()
    for pattern in _CITATION_PATTERNS:
        for match in pattern.finditer(text):
            raw.add(match.group())
    normalized: set[str] = set()
    for c in raw:
        # Normalize "Wis.2d" -> "Wis. 2d", collapse whitespace
        norm = re.sub(r"Wis\.?\s*2d", "Wis. 2d", c)
        norm = re.sub(r"\s+", " ", norm).strip()
        normalized.add(norm)
    return sorted(normalized)


logger = logging.getLogger(__name__)
LOG_TOOL_TRACE = os.environ.get("LOG_TOOL_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))

REGION = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

FAQ_KNOWLEDGE_BASE_ID = os.environ.get("FAQ_KNOWLEDGE_BASE_ID", "")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
REFINEMENT_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6")


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


def embed_query(query: str, model_id: str = "amazon.titan-embed-text-v2:0") -> list[float]:
    """Embed a query string for vector search."""
    started = time.perf_counter()
    body = json.dumps(
        {
            "inputText": query[:8000],
            "dimensions": 1024,
            "normalize": True,
        }
    )
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
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


def _rank_chunks_by_relevance(
    chunks: list[dict],
    query_embedding: list[float],
    top_k: int,
) -> dict:
    """Rank section chunks by cosine similarity + z-score filtering.

    Returns a dict with:
      - chunks: up to top_k chunks that pass the z-score threshold
      - ranking_stats: per-chunk scores and aggregate stats for trace UI
    """
    import math

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    scored = []
    for chunk in chunks:
        emb = chunk.pop("embedding", None)
        if emb is None:
            scored.append((0.0, chunk))
            continue
        score = _cosine(query_embedding, emb)
        scored.append((score, chunk))

    scores = [s for s, _ in scored]
    n = len(scores)
    if n == 0:
        return {"chunks": [], "ranking_stats": None}

    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    std = math.sqrt(variance)

    _Z_THRESHOLD = 0.5

    if std < 1e-6:
        scored.sort(key=lambda x: x[0], reverse=True)
        result_chunks = [chunk for _, chunk in scored[:top_k]]
        chunk_scores = [
            {
                "chunkId": chunk.get("id", ""),
                "cosine": round(score, 4),
                "zScore": None,
                "heading": chunk.get("heading", ""),
                "subheading": chunk.get("subheading", ""),
                "startPage": chunk.get("start_page"),
                "endPage": chunk.get("end_page"),
            }
            for score, chunk in scored[:top_k]
        ]
        return {
            "chunks": result_chunks,
            "ranking_stats": {
                "sectionChunkCount": n,
                "returnedChunkCount": len(result_chunks),
                "mean": round(mean, 4),
                "std": 0.0,
                "zThreshold": _Z_THRESHOLD,
                "flatDistribution": True,
                "chunkScores": chunk_scores,
            },
        }

    ranked = []
    for score, chunk in scored:
        z = (score - mean) / std
        ranked.append((z, score, chunk))
    ranked.sort(key=lambda x: x[0], reverse=True)

    results = []
    all_chunk_scores = []
    for _i, (z, score, chunk) in enumerate(ranked):
        included = (len(results) < top_k) and (z >= _Z_THRESHOLD or len(results) == 0)
        if included:
            chunk["relevance_score"] = round(score, 4)
            results.append(chunk)
        all_chunk_scores.append(
            {
                "chunkId": chunk.get("id", ""),
                "cosine": round(score, 4),
                "zScore": round(z, 2),
                "heading": chunk.get("heading", ""),
                "subheading": chunk.get("subheading", ""),
                "startPage": chunk.get("start_page"),
                "endPage": chunk.get("end_page"),
                "included": included,
            }
        )

    return {
        "chunks": results,
        "ranking_stats": {
            "sectionChunkCount": n,
            "returnedChunkCount": len(results),
            "mean": round(mean, 4),
            "std": round(std, 4),
            "zThreshold": _Z_THRESHOLD,
            "flatDistribution": False,
            "chunkScores": all_chunk_scores,
        },
    }


_REFINE_PROMPT = (
    "Lightly rewrite the current user question into ONE standalone search "
    "query for retrieving Wisconsin property-tax source documents. Use the "
    "prior conversation only to resolve references or missing context. Make "
    "the SMALLEST change that improves retrieval.\n\n"
    "Rewrite rules:\n"
    "1. Replace clearly casual/colloquial wording with the standard "
    "assessment term ONLY when the mapping is obvious and certain: "
    '"my land"/"my property" -> "real property" (or "agricultural land" for '
    'farming topics); "fight/appeal my assessment" -> "board of review '
    'objection"; "what my property is worth for taxes" -> "assessed value" / '
    '"fair market value".\n'
    "2. DO NOT expand or guess the meaning of any acronym, abbreviation, "
    "program name, form number, or term you are not certain about. Leave it "
    "EXACTLY as written. Keeping an unknown term verbatim is far better than "
    "inventing an expansion.\n"
    "3. DO NOT append any statute chapter or section number.\n"
    "4. DO NOT add topic words, synonyms, or context the user did not imply.\n"
    "5. If the question is already clear and specific, return it essentially "
    'unchanged (add "Wisconsin" only if it is missing and the question is not '
    "obviously Wisconsin-specific).\n\n"
    "Also: if the user explicitly mentions a 4-digit year (e.g., '2018', "
    "'the 2024 manual') AND the question is about WPAM / Wisconsin Property "
    "Assessment Manual / property assessment guidance, populate "
    "target_wpam_year with that year. Otherwise, target_wpam_year is null. "
    "A year that refers only to a tax-filing deadline or a statute year is "
    "NOT a target_wpam_year.\n\n"
    "Return ONLY a JSON object on a single line, no prose, no markdown:\n"
    '{"refined_query": "<rewritten query>", "target_wpam_year": <year or null>}'
)


def _auto_refine(query: str, chat_history: list[dict[str, str]] | None) -> tuple[str, int | None]:
    """Refine a query for vector_search. Returns (refined_query, target_wpam_year).

    Always called inside vector_search. On any error, returns the original query
    unchanged so retrieval still proceeds.
    """
    refine_started = time.perf_counter()
    prompt = (
        f"{_REFINE_PROMPT}\n\n"
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
            refined = raw
    except Exception as exc:  # noqa: BLE001
        _log_tool_event(
            "auto_refine_error",
            logging.WARNING,
            tool_name="vector_search",
            error_type=type(exc).__name__,
            error=str(exc),
            **_query_fields(query),
        )
        refined = query

    if not refined:
        refined = query
    _log_tool_event(
        "auto_refine_complete",
        tool_name="vector_search",
        latency_ms=round((time.perf_counter() - refine_started) * 1000),
        original_query=query[:200],
        refined_query=refined,
        target_wpam_year=target_year,
        history_turns=len(chat_history or []),
        **_query_fields(refined),
    )
    return refined, target_year


def execute_tool(
    tool_name: str,
    tool_input: dict,
    neptune: NeptuneClient,
    chat_history: list[dict[str, str]] | None = None,
    original_user_query: str | None = None,
) -> dict:
    """Execute a tool call and return the result.

    ``chat_history`` is threaded through for auto-refine inside vector_search.
    ``original_user_query`` is the user's verbatim question — used by
    vector_search's broad discovery arm to catch practitioner-oriented docs
    (news, guides) that keyword-heavy model queries miss.
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
            source_uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
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

    elif tool_name == "vector_search":
        # Delegates to the composable pipeline (agent_tools/pipeline.py +
        # agent_tools/stages/*). Kept as a thin call here — rather than
        # importing pipeline.run_vector_search directly into this module's
        # namespace — so tests that patch this module's attributes
        # (_auto_refine, embed_query, bedrock, RAW_BUCKET, extract_citations,
        # _log_tool_event, _query_fields, _rank_chunks_by_relevance) keep
        # intercepting calls exactly as before: the pipeline stages call back
        # into `agent_tools.executor` at run time to resolve these names, so
        # a monkeypatch on this module is honored regardless of which stage
        # ends up invoking it.
        from agent_tools import pipeline as _pipeline

        raw_query = tool_input["query"]
        top_k = min(tool_input.get("top_k", 10), 25)
        return _pipeline.run_vector_search(
            query=raw_query,
            neptune=neptune,
            chat_history=chat_history,
            original_user_query=original_user_query,
            top_k=top_k,
            tool_name=tool_name,
        )

    elif tool_name == "search_document":
        target_doc_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        sub_query = tool_input.get("query", "")
        top_k = min(tool_input.get("top_k", 5), 10)
        if not target_doc_id or not sub_query:
            return {"error": "Both doc_id and query are required"}
        embedding = embed_query(sub_query)
        # Over-fetch globally and filter to target doc — Neptune's
        # topKByEmbedding doesn't support WHERE after YIELD.
        search_doc_fetch_k = int(os.environ.get("SEARCH_DOCUMENT_FETCH_K", "800"))
        raw_chunks = neptune.vector_search(embedding, top_k=search_doc_fetch_k)
        matched = [c for c in raw_chunks if c.get("doc_id") == target_doc_id][:top_k]
        _log_tool_event(
            "search_document_complete",
            tool_name=tool_name,
            doc_id=target_doc_id,
            top_k=top_k,
            fetch_k=search_doc_fetch_k,
            matched_count=len(matched),
            total_scanned=len(raw_chunks),
            latency_ms=round((time.perf_counter() - started) * 1000),
            **_query_fields(sub_query),
        )
        keyword_fallback = False
        if not matched:
            max_fallback_chunks = int(os.environ.get("SEARCH_DOC_FALLBACK_MAX", "150"))
            all_chunks = neptune.get_chunks_for_doc(target_doc_id)
            if all_chunks and len(all_chunks) <= max_fallback_chunks:
                terms = set(sub_query.lower().split())
                scored = []
                for chunk in all_chunks:
                    text_lower = (chunk.get("text") or "").lower()
                    score = sum(1 for t in terms if t in text_lower)
                    if score > 0:
                        scored.append((score, chunk))
                scored.sort(key=lambda x: x[0], reverse=True)
                matched = [chunk for _, chunk in scored[:top_k]]
                keyword_fallback = bool(matched)
                _log_tool_event(
                    "search_document_keyword_fallback",
                    tool_name=tool_name,
                    doc_id=target_doc_id,
                    total_chunks=len(all_chunks),
                    keyword_matches=len(scored),
                    returned=len(matched),
                )
            if not matched:
                return {
                    "error": f"No chunks found for document '{target_doc_id}' matching this query",
                    "suggestion": (
                        "Try a different sub-query or use vector_search with broader terms"
                    ),
                }
        return {"chunks": matched, "doc_id": target_doc_id, "keyword_fallback": keyword_fallback}

    elif tool_name == "list_sections":
        doc_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        if not doc_id:
            return {"error": "doc_id is required"}
        sections = neptune.list_document_sections(doc_id)
        _log_tool_event(
            "list_sections_complete",
            tool_name=tool_name,
            doc_id=doc_id,
            section_count=len(sections),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        if not sections:
            return {"error": f"No sections found for document '{doc_id}'"}
        return {"doc_id": doc_id, "sections": sections}

    elif tool_name == "get_section":
        doc_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        heading = tool_input.get("heading", "")
        query = tool_input.get("query", "")
        top_k = min(tool_input.get("top_k", 5), 10)
        if not doc_id or not heading:
            return {"error": "Both doc_id and heading are required"}

        if query:
            chunks = neptune.get_section_chunks_with_embeddings(doc_id, heading)
        else:
            chunks = neptune.get_section_chunks(doc_id, heading)

        section_chunk_count = len(chunks)
        _log_tool_event(
            "get_section_complete",
            tool_name=tool_name,
            doc_id=doc_id,
            heading=heading,
            chunk_count=section_chunk_count,
            query_provided=bool(query),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        if not chunks:
            return {
                "error": f"No chunks found for heading '{heading}' in document '{doc_id}'",
                "suggestion": "Use list_sections to see available headings for this document",
            }

        ranking_stats = None
        if query:
            query_embedding = embed_query(query)
            rank_result = _rank_chunks_by_relevance(chunks, query_embedding, top_k)
            chunks = rank_result["chunks"]
            ranking_stats = rank_result["ranking_stats"]

        result = {"chunks": chunks, "doc_id": doc_id, "heading": heading}
        if ranking_stats:
            result["ranking_stats"] = ranking_stats
            result["query"] = query
        return result

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
                neptune.vector_search(embed_query(requested_id), top_k=5) if requested_id else []
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
        node_id = tool_input["node_id"]
        edge_types = tool_input.get("edge_types")
        direction = tool_input.get("direction", "both")
        query = tool_input.get("query", "")
        top_k = min(tool_input.get("top_k", 5), 10)

        # When a query is provided, attempt semantic ranking on neighbor chunks.
        # Works best for statute stubs with CITES→CaseLaw (summary embeddings),
        # but applies to any node whose neighbors have embedded chunks.
        if query and node_id.startswith("WIS-STAT-"):
            try:
                case_summaries = neptune.get_neighbor_case_summaries_with_embeddings(
                    node_id, direction=direction
                )
                if case_summaries:
                    query_embedding = embed_query(query)
                    rank_result = _rank_chunks_by_relevance(
                        [
                            {
                                "id": cs.get("case_id"),
                                "text": cs.get("summary", ""),
                                "heading": cs.get("title", ""),
                                "subheading": cs.get("citation", ""),
                                "start_page": None,
                                "end_page": None,
                                "embedding": cs.get("embedding"),
                                "source_url": cs.get("source_url", ""),
                                "case_id": cs.get("case_id"),
                                "citation": cs.get("citation"),
                            }
                            for cs in case_summaries
                        ],
                        query_embedding,
                        top_k,
                    )
                    ranked_cases = rank_result["chunks"]
                    _log_tool_event(
                        "get_neighbors_ranked_cases",
                        tool_name=tool_name,
                        node_id=node_id,
                        query=query[:200],
                        total_cases=len(case_summaries),
                        returned=len(ranked_cases),
                        latency_ms=round((time.perf_counter() - started) * 1000),
                    )
                    return {
                        "neighbors": ranked_cases,
                        "ranking_stats": rank_result.get("ranking_stats"),
                        "total_cases": len(case_summaries),
                        "query": query,
                        "top_k": top_k,
                    }
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Ranked case-law neighbors failed; falling back to unranked",
                    exc_info=True,
                )

        neighbors = neptune.get_neighbors(
            node_id,
            edge_types=edge_types,
            direction=direction,
            title_filter=tool_input.get("title_filter"),
        )
        target_year = tool_input.get("target_wpam_year")
        neighbors = dedupe_wpam_chunks(
            neighbors,
            target_year=target_year,
            current_wpam_year=neptune.current_wpam_year,
        )
        pre_filter_count = len(neighbors)
        neighbors = [n for n in neighbors if "Chunk" not in (n.get("labels") or [])]
        _log_tool_event(
            "get_neighbors_complete",
            tool_name=tool_name,
            node_id=node_id,
            edge_types=edge_types,
            direction=direction,
            target_wpam_year=target_year,
            neighbor_count=len(neighbors),
            filtered_chunk_count=pre_filter_count - len(neighbors),
            relationships=sorted(
                {n.get("relationship", "") for n in neighbors if n.get("relationship")}
            ),
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

    elif tool_name == "find_case_law":
        search_text = tool_input.get("search_text", "")
        statute_id = tool_input.get("statute_id")
        # Try citation-based lookup first (most reliable)
        citations = extract_citations(search_text)
        cases: list[dict] = []
        if citations:
            cases = neptune.resolve_case_citations(citations)
        # Fall back to title substring search
        if not cases and search_text:
            cases = neptune.find_case_law(search_text, statute_id=statute_id, limit=10)
        _log_tool_event(
            "find_case_law_complete",
            tool_name=tool_name,
            search_text=search_text,
            statute_id=statute_id,
            citations_extracted=len(citations),
            case_count=len(cases),
            case_ids=[c.get("id") for c in cases[:10]],
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"cases": cases}

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

    elif tool_name == "list_worksheets":
        worksheets = list_worksheet_registry()
        _log_tool_event(
            "list_worksheets_complete",
            tool_name=tool_name,
            worksheet_count=len(worksheets),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return {"worksheets": worksheets}

    elif tool_name == "get_worksheet":
        worksheet_id = tool_input.get("worksheet_id", "")
        if not worksheet_id:
            return {"error": "worksheet_id is required"}
        result = load_worksheet(
            worksheet_id,
            raw_bucket=RAW_BUCKET,
            sheet=tool_input.get("sheet"),
        )
        _log_tool_event(
            "get_worksheet_complete",
            tool_name=tool_name,
            worksheet_id=worksheet_id,
            sheet=tool_input.get("sheet"),
            sheet_count=len(result.get("sheets", [])) if "sheets" in result else 0,
            has_error=bool(result.get("error")),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return result

    elif tool_name == "clarify":
        _log_tool_event(
            "clarify_tool_complete",
            tool_name=tool_name,
            question_chars=len(tool_input.get("question", "")),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return tool_input

    elif tool_name == "prepare_answer":
        _log_tool_event(
            "prepare_answer_tool_complete",
            tool_name=tool_name,
            cited_doc_count=len(tool_input.get("cited_doc_ids", [])),
            has_plan=bool(tool_input.get("answer_plan")),
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
