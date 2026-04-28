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
from typing import Any

import boto3
import pydantic
from neptune_client import NeptuneClient
from step_function_types.errors import ValidationError, report_error
from prompt import SYSTEM_PROMPT
from step_function_types.models import (
    DocumentResource,
    RAGDocument,
    UserQuery,
)
from tools import TOOL_DEFINITIONS, execute_tool

MAX_TURNS = 10

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
    """Parse input event. Receives clean {query, query_id, session_id} from EventBridge $.detail extraction."""
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

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        logger.info(f"Agentic loop turn {turn + 1}/{MAX_TURNS}")

        # Turn-8 warning injection (docs/graphrag.md §7)
        if turn == 7:
            messages.append({
                "role": "user",
                "content": [{"text": "You are running low on turns. Call the answer tool NOW with your best answer from the context gathered so far."}],
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
                for neighbor_doc_id in result.get("graph_context", {}):
                    all_doc_ids.add(neighbor_doc_id)
                    discovery.setdefault(neighbor_doc_id, "graph-neighbor")

            if tool_name == "get_neighbors" and "neighbors" in result:
                for n in result["neighbors"]:
                    if n.get("id"):
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

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = result.get("cited_doc_ids", [])
                all_doc_ids.update(cited)
                for cid in cited:
                    discovery.setdefault(cid, "fetched")
                rag_docs = _build_rag_documents(all_chunks, all_doc_ids, discovery)
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
            answer = "I was unable to find a complete answer within the allowed number of search steps. Please try rephrasing your question."

    rag_docs = _build_rag_documents(all_chunks, all_doc_ids, discovery)
    return answer, list(all_doc_ids), rag_docs


def _generate_source_url(chunk: dict, doc_info: dict | None) -> str:
    """Generate the best available source URL for a chunk.

    For PDFs in S3: presigned URL with #page=N fragment.
    For web pages: original source_url (gov website link).
    """
    s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key") or ""
    start_page = chunk.get("start_page")

    if RAW_BUCKET and s3_key and s3_key.endswith(".pdf"):
        try:
            presigned = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": RAW_BUCKET, "Key": s3_key},
                ExpiresIn=PRESIGNED_URL_EXPIRY,
            )
            if start_page:
                return f"{presigned}#page={start_page}"
            return presigned
        except Exception:
            logger.warning(f"Failed to generate presigned URL for {s3_key}", exc_info=True)

    source_url = chunk.get("source_url", "")
    if source_url:
        return source_url

    return (doc_info or {}).get("source_url", "")


def _build_rag_documents(
    chunks: list[dict],
    doc_ids: set[str],
    discovery: dict[str, str] | None = None,
) -> list[RAGDocument]:
    """Build RAGDocument list from collected chunks, tagged by how discovered."""
    discovery = discovery or {}
    docs_by_id: dict[str, RAGDocument] = {}

    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text", "")
        tag = discovery.get(doc_id, "unknown")

        if doc_id not in docs_by_id:
            doc_info = neptune.get_document(doc_id)
            title = doc_info["title"] if doc_info else doc_id
            content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
            source = _generate_source_url(chunk, doc_info)

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=source,
                discovery_tag=tag,
            )
        else:
            existing = docs_by_id[doc_id]
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source or _generate_source_url(chunk, None),
                discovery_tag=existing.discovery_tag,
            )

    # Include cited docs that had no chunks (e.g., fetched-only)
    for doc_id in doc_ids - docs_by_id.keys():
        doc_info = neptune.get_document(doc_id)
        if not doc_info:
            continue
        content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
        tag = discovery.get(doc_id, "unknown")
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title", doc_id),
            content=doc_info.get("summary", ""),
            source=_generate_source_url({}, doc_info),
            discovery_tag=tag,
        )

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
