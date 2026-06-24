"""Human-readable summaries of tool calls and results for UI trace."""

from typing import Any

from tracing import truncate_text


def summarize_assistant_message(message: dict, max_chars: int) -> dict[str, Any]:
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
        "text_preview": truncate_text("\n".join(text_blocks), max_chars) if text_blocks else "",
        "tool_use_count": len(tool_names),
        "tool_names": tool_names,
    }


def summarize_bedrock_response(response: dict) -> dict[str, Any]:
    usage = response.get("usage") or {}
    metrics = response.get("metrics") or {}
    return {
        "stop_reason": response.get("stopReason", ""),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "total_tokens": usage.get("totalTokens"),
        "model_latency_ms": metrics.get("latencyMs"),
    }


def summarize_tool_result(tool_name: str, result: dict) -> dict[str, Any]:
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

    if tool_name == "search_document":
        chunks = result.get("chunks", [])
        return {
            "tool_name": tool_name,
            "status": "ok",
            "doc_id": result.get("doc_id"),
            "chunk_count": len(chunks),
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

    if tool_name == "clarify":
        return {
            "tool_name": tool_name,
            "status": "terminal",
            "question_chars": len(result.get("question", "")),
        }

    if tool_name == "cite_documents":
        return {
            "tool_name": tool_name,
            "status": "terminal",
            "cited_doc_count": len(result.get("cited_doc_ids", [])),
            "cited_doc_ids": result.get("cited_doc_ids", [])[:20],
        }

    return {"tool_name": tool_name, "status": "ok", "result_keys": sorted(result.keys())}


def build_tool_call_summary(tool_name: str, tool_input: dict, neptune_client=None) -> str:
    """Short prose describing a tool call for the UI trace."""
    if tool_name in ("vector_search", "faq_search", "refine_query"):
        query = tool_input.get("query", "")
        return f'"{query}"' if query else ""
    if tool_name == "search_document":
        doc_id = tool_input.get("doc_id", "")
        query = tool_input.get("query", "")
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        if query:
            return f'"{query}" in {title}'
        return title
    if tool_name == "get_neighbors":
        doc_id = tool_input.get("doc_id", "")
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        return title
    if tool_name == "get_document":
        doc_id = tool_input.get("doc_id", "")
        return doc_id
    if tool_name == "get_authority_chain":
        doc_id = tool_input.get("doc_id", "")
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        return title
    if tool_name == "list_framework_docs":
        framework = tool_input.get("framework_name", "")
        return framework
    if tool_name == "fetch_case_opinion":
        citation = tool_input.get("citation", "")
        return citation
    if tool_name == "clarify":
        question = tool_input.get("question", "")
        return f'"{question[:60]}"' if question else ""
    if tool_name == "cite_documents":
        cited = tool_input.get("cited_doc_ids", []) or []
        n = len(cited)
        return f"with {n} cited {'source' if n == 1 else 'sources'}"
    return ""


def build_tool_result_summary(tool_name: str, result: dict, neptune_client) -> dict:
    """Build a UI-friendly summary of a tool result.

    Returns a dict with:
      - status: 'ok' | 'error' | 'miss' | 'terminal'
      - summary_text: one-line human-readable string
      - doc_ids: list of up to 10 document IDs referenced in the result
      - doc_titles: list aligned with doc_ids
      - metadata: camelCase dict of counts/scores for the UI subtitle
      - raw: output of summarize_tool_result (dev-mode payload)
    """
    import logging

    raw = summarize_tool_result(tool_name, result)
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
        n_chunks = len(chunks)
        n_docs = len(ordered_docs)
        summary_text = (
            f"Found {n_chunks} {'chunk' if n_chunks == 1 else 'chunks'} "
            f"across {n_docs} {'source' if n_docs == 1 else 'sources'}"
        )
        top_score = max(
            (float(c.get("score", 0.0)) for c in chunks),
            default=0.0,
        )
        graph_context = result.get("graph_context", {}) or {}
        auto_enriched_count = sum(len(v) for v in graph_context.values())
        pre_dedup_count = result.get("pre_dedup_count", n_chunks)
        target_wpam_year = result.get("target_wpam_year")
        authority_breakdown: dict[str, int] = {}
        for chunk in chunks:
            level = chunk.get("authority_level")
            if level is not None:
                key = str(int(level))
                authority_breakdown[key] = authority_breakdown.get(key, 0) + 1
        related_case_law = result.get("related_case_law", [])
        scores = [float(c.get("score", 0.0)) for c in chunks]
        score_buckets: dict[str, int] = {"0.9+": 0, "0.8-0.9": 0, "0.7-0.8": 0, "<0.7": 0}
        for s in scores:
            if s >= 0.9:
                score_buckets["0.9+"] += 1
            elif s >= 0.8:
                score_buckets["0.8-0.9"] += 1
            elif s >= 0.7:
                score_buckets["0.7-0.8"] += 1
            else:
                score_buckets["<0.7"] += 1
        score_buckets = {k: v for k, v in score_buckets.items() if v > 0}
        metadata = {
            "chunkCount": n_chunks,
            "docCount": n_docs,
            "autoEnrichedCount": auto_enriched_count,
            "topScore": round(top_score, 4),
            "preDedupCount": pre_dedup_count,
            "authorityBreakdown": authority_breakdown,
            "caseLawCount": len(related_case_law),
            "scoreBuckets": score_buckets,
        }
        if target_wpam_year is not None:
            metadata["targetWpamYear"] = target_wpam_year

    elif tool_name == "search_document":
        chunks = result.get("chunks", [])
        target_doc = result.get("doc_id", "")
        doc_ids = [target_doc] if target_doc else []
        doc_title = target_doc
        if target_doc:
            try:
                info = neptune_client.get_document(target_doc)
                doc_title = (info or {}).get("title") or target_doc
            except Exception:
                pass
        if not chunks:
            status = "miss"
        fallback_used = result.get("keyword_fallback", False)
        if fallback_used:
            summary_text = f"Keyword fallback: found {len(chunks)} chunks in {doc_title}"
        else:
            summary_text = f"Searched {doc_title}"
        metadata = {"chunkCount": len(chunks), "docId": target_doc}
        if fallback_used:
            metadata["keywordFallback"] = True

    elif tool_name == "faq_search":
        faqs = result.get("faqs", [])
        top = faqs[0].get("score", 0.0) if faqs else 0.0
        faq_scores = [round(float(f.get("score", 0.0)), 4) for f in faqs]
        summary_text = (
            f"FAQ semantic match score {top:.2f}"
            if faqs
            else "No FAQ matches"
        )
        metadata = {
            "faqCount": len(faqs),
            "topScore": round(float(top), 4),
            "faqScoreThreshold": 0.70,
            "faqScores": faq_scores,
        }

    elif tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        doc_ids = [n["id"] for n in neighbors if n.get("id")][:10]
        n = len(neighbors)
        summary_text = f"Retrieved {n} related {'document' if n == 1 else 'documents'} from graph"
        relationship_counts: dict[str, int] = {}
        for neighbor in neighbors:
            rel = neighbor.get("relationship", "unknown")
            relationship_counts[rel] = relationship_counts.get(rel, 0) + 1
        metadata = {"neighborCount": len(neighbors), "relationshipCounts": relationship_counts}

    elif tool_name == "get_document":
        doc = result.get("document")
        if doc:
            doc_ids = [doc.get("id")] if doc.get("id") else []
            doc_id = doc.get("id", "")
            doc_type = doc.get("doc_type") or (
                "statute" if doc_id.startswith("WIS-STAT-") else "document"
            )
            summary_text = f"Fetched {doc_type} {doc_id}"
            metadata = {"documentCount": 1}
        else:
            summary_text = "Document not found"
            status = "miss"
            metadata = {"documentCount": 0}

    elif tool_name == "get_authority_chain":
        chain = result.get("authority_chain", [])
        doc_ids = [n["id"] for n in chain if n.get("id")][:10]
        n = len(chain)
        summary_text = f"Traced {n} authority {'step' if n == 1 else 'steps'}"
        metadata = {"chainLength": len(chain)}

    elif tool_name == "list_framework_docs":
        docs = result.get("documents", [])
        doc_ids = [d["id"] for d in docs if d.get("id")][:10]
        n = len(docs)
        summary_text = f"Listed {n} framework {'document' if n == 1 else 'documents'}"
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

    elif tool_name == "clarify":
        question = result.get("question", "")
        summary_text = f"Asking user: {question[:80]}"
        status = "terminal"
        metadata = {"questionChars": len(question)}

    elif tool_name == "cite_documents":
        cited = result.get("cited_doc_ids", []) or []
        doc_ids = list(cited)[:10]
        n = len(cited)
        summary_text = f"Citing {n} {'source' if n == 1 else 'sources'}"
        status = "terminal"
        metadata = {"citedDocCount": len(cited)}

    else:
        summary_text = f"{tool_name} complete"

    doc_titles: list[str] = []
    for doc_id in doc_ids:
        try:
            info = neptune_client.get_document(doc_id)
            doc_titles.append((info or {}).get("title") or doc_id)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
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


def discovery_summary(discovery: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tag in discovery.values():
        counts[tag] = counts.get(tag, 0) + 1
    return counts
