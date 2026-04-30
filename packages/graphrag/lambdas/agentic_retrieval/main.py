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
from typing import Any

import boto3
import pydantic
from case_opinion import citation_to_raw_slug
from neptune_client import NeptuneClient
from prompt import SYSTEM_PROMPT
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    RAGDocument,
    UserQuery,
)
from tools import TOOL_DEFINITIONS, execute_tool

MAX_TURNS = 10

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


def process_event(event: dict) -> UserQuery:
    """Parse input event.

    Receives a clean {query, query_id, session_id} from EventBridge $.detail extraction.
    """
    try:
        return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


def run_agentic_loop(query: str) -> tuple[str, list[str], list[RAGDocument]]:
    """Run Claude's agentic loop against Neptune.

    Returns:
        (answer_text, cited_doc_ids, rag_documents)
    """
    messages = [{"role": "user", "content": [{"text": query}]}]
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []
    discovery: dict[str, str] = {}  # doc_id -> tag
    # citation -> fetched-opinion payload. Keyed by the stub doc_id we'd
    # otherwise emit, so _build_rag_documents can replace the stub with the
    # richer opinion card.
    fetched_opinions: dict[str, dict] = {}

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        logger.info(f"Agentic loop turn {turn + 1}/{MAX_TURNS}")

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

        response = bedrock.converse(
            modelId=AGENTIC_MODEL_ID,
            messages=messages,
            system=[{"text": SYSTEM_PROMPT}],
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)
        stop_reason = response.get("stopReason", "")

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
            break

        tool_results = []
        for tool_use in tool_uses:
            tool = tool_use["toolUse"]
            tool_name = tool["name"]
            tool_input = tool["input"]
            tool_use_id = tool["toolUseId"]

            logger.info(f"  Tool call: {tool_name}({json.dumps(tool_input)[:200]})")

            result = execute_tool(tool_name, tool_input, neptune)

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

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = result.get("cited_doc_ids", [])
                all_doc_ids.update(cited)
                for cid in cited:
                    discovery.setdefault(cid, "fetched")
                rag_docs = _build_rag_documents(
                    all_chunks, all_doc_ids, discovery, fetched_opinions
                )
                return answer, list(all_doc_ids), rag_docs

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

    rag_docs = _build_rag_documents(
        all_chunks, all_doc_ids, discovery, fetched_opinions
    )
    return answer, list(all_doc_ids), rag_docs


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

    try:
        user_query = process_event(event)
        session_id = user_query.session_id
        logger.info(f"Agentic retrieval for query: {user_query.query[:200]}")

        answer, cited_doc_ids, rag_documents = run_agentic_loop(user_query.query)

        documents = DocumentResource(documents=rag_documents)
        logger.info(f"Returning {len(rag_documents)} docs to Step Functions")

        # Return a flat payload; Step Functions Pass states build both
        # generate_response_job and stream_documents_job from shared fields
        # to avoid duplicating documents (keeps payload under 256KB limit).
        return {
            "successful": True,
            "query": user_query.query,
            "query_id": user_query.query_id,
            "session_id": user_query.session_id,
            "faqs": None,
            "documents": documents.model_dump(),
        }

    except Exception as e:
        logger.error(f"Agentic retrieval failed: {e}", exc_info=True)
        if session_id:
            asyncio.run(report_error(e, session_id=session_id))

        return {"successful": False}
