"""Human-readable summaries of tool calls and results for UI trace."""

from typing import Any

from .logger import truncate_text


def summarize_assistant_message(message: dict, max_chars: int) -> dict[str, Any]:
    content = message.get("content") or []
    text_blocks = [block.get("text", "") for block in content if "text" in block]
    tool_names = [block["toolUse"].get("name", "") for block in content if "toolUse" in block]
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
            "relationships": sorted(
                {n.get("relationship", "") for n in neighbors if n.get("relationship")}
            ),
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

    if tool_name == "prepare_answer":
        return {
            "tool_name": tool_name,
            "status": "terminal",
            "cited_doc_count": len(result.get("cited_doc_ids", [])),
            "cited_doc_ids": result.get("cited_doc_ids", [])[:20],
            "has_plan": bool(result.get("answer_plan")),
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
    if tool_name == "list_sections":
        doc_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        return title
    if tool_name == "get_section":
        doc_id = tool_input.get("doc_id") or tool_input.get("node_id") or ""
        heading = tool_input.get("heading", "")
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        if heading:
            return f'"{heading}" from {title}'
        return title
    if tool_name == "get_neighbors":
        doc_id = tool_input.get("doc_id", "") or tool_input.get("node_id", "")
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
        doc_id = tool_input.get("doc_id", "") or tool_input.get("node_id", "")
        title = doc_id
        if doc_id and neptune_client:
            try:
                info = neptune_client.get_document(doc_id)
                title = (info or {}).get("title") or doc_id
            except Exception:
                pass
        return title
    if tool_name == "list_framework_docs":
        framework = tool_input.get("framework_name", "") or tool_input.get("framework_id", "")
        return framework
    if tool_name == "fetch_case_opinion":
        citation = tool_input.get("citation", "")
        return citation
    if tool_name == "clarify":
        question = tool_input.get("question", "")
        return f'"{question[:60]}"' if question else ""
    if tool_name == "prepare_answer":
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
        doc_chunks: dict[str, int] = {}
        for chunk in chunks:
            did = chunk.get("doc_id", "unknown")
            doc_chunks[did] = doc_chunks.get(did, 0) + 1
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        # Statute backfill: statute-text chunks pulled in because a top-ranked
        # retrieved chunk CITES them. Each carries `cited_by_source_rank`
        # (1-based rank into the retrieved chunks) so the visualizer can anchor
        # the backfill dot's edge to the source chunk it was cited from.
        statute_backfill = result.get("statute_backfill", []) or []
        backfill_meta = [
            {
                "chunkId": b.get("chunk_id"),
                "docId": b.get("doc_id"),
                "sourceRank": b.get("cited_by_source_rank"),
                "heading": b.get("heading", ""),
            }
            for b in statute_backfill
            if b.get("chunk_id")
        ]
        caselaw_backfill = result.get("caselaw_backfill", []) or []
        caselaw_backfill_meta = [
            {
                "caseId": b.get("doc_id"),
                "title": b.get("heading", ""),
                "citation": b.get("subheading", ""),
                "summary": (b.get("text") or "")[:150],
                "relevanceScore": b.get("relevance_score"),
                "contentRole": b.get("content_role", ""),
                "citedStubs": b.get("cited_stub_ids") or [],
            }
            for b in caselaw_backfill
        ]
        broad_discovery = result.get("broad_discovery", []) or []
        broad_meta = [
            {
                "docId": b.get("doc_id"),
                "score": round(float(b.get("score", 0)), 4),
            }
            for b in broad_discovery
            if b.get("doc_id")
        ]
        broad_additive_doc_chunks: dict[str, int] = result.get("broad_additive_doc_chunks") or {}
        if not broad_additive_doc_chunks:
            for chunk in broad_discovery:
                did = chunk.get("doc_id", "unknown")
                broad_additive_doc_chunks[did] = broad_additive_doc_chunks.get(did, 0) + 1
        broad_chunk_count = len(broad_discovery)
        metadata = {
            "chunkCount": n_chunks,
            "broadChunkCount": broad_chunk_count,
            "totalChunkCount": n_chunks + broad_chunk_count,
            "docCount": n_docs,
            "autoEnrichedCount": auto_enriched_count,
            "topScore": round(top_score, 4),
            "preDedupCount": pre_dedup_count,
            "authorityBreakdown": authority_breakdown,
            "caseLawCount": len(related_case_law),
            "scoreBuckets": score_buckets,
            "docChunks": doc_chunks,
            "chunkIds": chunk_ids,
        }
        if backfill_meta:
            metadata["statuteBackfill"] = backfill_meta
        if caselaw_backfill_meta:
            metadata["caselawBackfill"] = caselaw_backfill_meta
        caselaw_bf_meta = result.get("caselaw_backfill_meta") or {}
        if caselaw_bf_meta:
            metadata["caselawBackfillMeta"] = caselaw_bf_meta
        if broad_meta:
            metadata["broadDiscovery"] = broad_meta
        if broad_additive_doc_chunks:
            metadata["broadDocChunks"] = broad_additive_doc_chunks
        broad_full_doc_chunks = result.get("broad_full_doc_chunks") or {}
        if broad_full_doc_chunks:
            metadata["broadFullDocChunks"] = broad_full_doc_chunks
        broad_pre_dedup = result.get("broad_pre_dedup_count")
        if broad_pre_dedup is not None:
            metadata["broadPreDedupCount"] = broad_pre_dedup
        broad_kept = result.get("broad_kept_count")
        if broad_kept is not None:
            metadata["broadKeptCount"] = broad_kept
        broad_authority = result.get("broad_authority_breakdown") or {}
        if broad_authority:
            metadata["broadAuthorityBreakdown"] = broad_authority
        broad_score_buckets = result.get("broad_score_buckets") or {}
        if broad_score_buckets:
            metadata["broadScoreBuckets"] = broad_score_buckets
        if result.get("broad_top_score") is not None:
            metadata["broadTopScore"] = result["broad_top_score"]
        refined_query = result.get("refined_query", "")
        if refined_query:
            metadata["refinedQuery"] = refined_query
        broad_query = result.get("broad_query", "")
        if broad_query:
            metadata["broadQuery"] = broad_query
        if "broad_skipped" in result:
            metadata["broadSkipped"] = bool(result["broad_skipped"])
        top_k = result.get("top_k")
        if top_k is not None:
            metadata["topK"] = top_k
        diversity_cap = result.get("diversity_cap_per_doc")
        if diversity_cap is not None:
            metadata["diversityCapPerDoc"] = diversity_cap
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
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        metadata = {
            "chunkCount": len(chunks),
            "docId": target_doc,
            "docTitle": doc_title,
            "chunkIds": chunk_ids,
        }
        if fallback_used:
            metadata["keywordFallback"] = True

    elif tool_name == "faq_search":
        faqs = result.get("faqs", [])
        top = faqs[0].get("score", 0.0) if faqs else 0.0
        faq_scores = [round(float(f.get("score", 0.0)), 4) for f in faqs]
        top_faq_text = ""
        if faqs:
            raw_text = faqs[0].get("text", "")
            top_faq_text = raw_text[:120]
        summary_text = f"FAQ semantic match score {top:.2f}" if faqs else "No FAQ matches"
        metadata = {
            "faqCount": len(faqs),
            "topScore": round(float(top), 4),
            "faqScoreThreshold": 0.70,
            "faqScores": faq_scores,
            "topFaqSnippet": top_faq_text,
        }

    elif tool_name == "list_sections":
        sections = result.get("sections", [])
        target_doc = result.get("doc_id", "")
        doc_ids = [target_doc] if target_doc else []
        doc_title = target_doc
        if target_doc:
            try:
                info = neptune_client.get_document(target_doc)
                doc_title = (info or {}).get("title") or target_doc
            except Exception:
                pass
        n = len(sections)
        summary_text = f"Found {n} {'section' if n == 1 else 'sections'} in {doc_title}"
        section_headings = [s.get("heading", "") for s in sections[:12]]
        metadata = {
            "sectionCount": n,
            "docId": target_doc,
            "docTitle": doc_title,
            "sectionHeadings": section_headings,
        }

    elif tool_name == "get_section":
        chunks = result.get("chunks", [])
        target_doc = result.get("doc_id", "")
        heading = result.get("heading", "")
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
        n = len(chunks)
        summary_text = f'Got "{heading}" ({n} chunks) from {doc_title}'
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        ranking_stats = result.get("ranking_stats")
        # `filtered` tells the trace UI whether z-score ranking was applied
        # (query passed) or chunks came back in document order (no query).
        # Counts are always sent so unranked calls don't render as "0 of 0".
        metadata = {
            "chunkCount": n,
            "docId": target_doc,
            "docTitle": doc_title,
            "heading": heading,
            "chunkIds": chunk_ids,
            "filtered": bool(ranking_stats),
            "sectionChunkCount": (ranking_stats["sectionChunkCount"] if ranking_stats else n),
            "returnedChunkCount": (ranking_stats["returnedChunkCount"] if ranking_stats else n),
        }
        if ranking_stats:
            metadata["query"] = result.get("query", "")
            metadata["mean"] = ranking_stats["mean"]
            metadata["std"] = ranking_stats["std"]
            metadata["zThreshold"] = ranking_stats["zThreshold"]
            metadata["flatDistribution"] = ranking_stats["flatDistribution"]
            metadata["chunkScores"] = ranking_stats["chunkScores"]

    elif tool_name == "get_neighbors":
        neighbors = result.get("neighbors", [])
        doc_ids = [n["id"] for n in neighbors if n.get("id")][:10]
        n = len(neighbors)
        summary_text = f"Retrieved {n} related {'document' if n == 1 else 'documents'} from graph"
        ranking_stats = result.get("ranking_stats")
        ranked = bool(ranking_stats)
        score_by_id = {
            score.get("chunkId"): score
            for score in (ranking_stats or {}).get("chunkScores", [])
            if score.get("chunkId")
        }
        relationship_counts: dict[str, int] = {}
        for neighbor in neighbors:
            rel = neighbor.get("relationship") or ("SEMANTIC_MATCH" if ranked else "unknown")
            relationship_counts[rel] = relationship_counts.get(rel, 0) + 1
        neighbor_titles = [
            nb.get("title") or nb.get("heading") or nb.get("id", "")
            for nb in neighbors[:10]
            if nb.get("title") or nb.get("heading") or nb.get("id")
        ]
        neighbor_edges = [
            {
                "id": nb.get("id", ""),
                "title": nb.get("title") or nb.get("heading") or nb.get("id", ""),
                "relationship": nb.get("relationship")
                or ("SEMANTIC_MATCH" if ranked else ""),
                "rank": rank,
                "score": (
                    score_by_id.get(nb.get("id"), {}).get("cosine")
                    if ranked
                    else None
                ),
            }
            for rank, nb in enumerate(neighbors[:10], start=1)
            if nb.get("title") or nb.get("heading") or nb.get("id")
        ]
        metadata = {
            "neighborCount": len(neighbors),
            "relationshipCounts": relationship_counts,
            "neighborTitles": neighbor_titles,
            "neighborEdges": neighbor_edges,
            "ranked": ranked,
        }
        if ranked:
            metadata["query"] = result.get("query", "")
            metadata["topK"] = result.get("top_k", len(neighbors))
            metadata["totalCandidates"] = result.get("total_cases", len(neighbors))

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
        metadata = {"refined": bool(refined), "refinedQuery": refined}

    elif tool_name == "clarify":
        question = result.get("question", "")
        summary_text = f"Asking user: {question[:80]}"
        status = "terminal"
        metadata = {"questionChars": len(question)}

    elif tool_name == "prepare_answer":
        cited = result.get("cited_doc_ids", []) or []
        doc_ids = list(cited)[:10]
        n = len(cited)
        summary_text = f"Preparing answer with {n} {'source' if n == 1 else 'sources'}"
        status = "terminal"
        metadata = {"citedDocCount": len(cited), "hasPlan": bool(result.get("answer_plan"))}

    elif tool_name == "list_worksheets":
        worksheets = result.get("worksheets", [])
        n = len(worksheets)
        summary_text = f"Listed {n} {'worksheet' if n == 1 else 'worksheets'}"
        metadata = {"worksheetCount": n}

    elif tool_name == "get_worksheet":
        worksheet_id = result.get("worksheet_id", "")
        sheets = result.get("sheets", [])
        if result.get("error"):
            status = "miss"
            summary_text = result["error"][:80]
            metadata = {"worksheetId": worksheet_id}
        else:
            title = result.get("title") or worksheet_id
            n = len(sheets)
            summary_text = f"Got {title} ({n} {'sheet' if n == 1 else 'sheets'})"
            metadata = {
                "worksheetId": worksheet_id,
                "sheetCount": n,
                "sheetNames": [s.get("sheet") for s in sheets][:12],
            }

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
