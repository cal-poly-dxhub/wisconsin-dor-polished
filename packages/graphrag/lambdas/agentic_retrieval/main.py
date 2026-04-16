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
from step_function_types.models import (
    DocumentResource,
    GenerateResponseJob,
    RAGDocument,
    RetrieveResult,
    StreamResourcesJob,
    UserQuery,
)
from tools import TOOL_DEFINITIONS, execute_tool

MAX_TURNS = 10

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")
s3_client = boto3.client("s3", region_name="us-west-2")
neptune = NeptuneClient()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
PRESIGNED_URL_EXPIRY = int(os.environ.get("PRESIGNED_URL_EXPIRY", "3600"))

SYSTEM_PROMPT = """You are a Wisconsin Department of Revenue property tax assistant. Answer questions about property assessment, taxation, statutes, administrative rules, and procedures using the provided tools.

WORKFLOW:
1. ALWAYS start by calling faq_search with the user's question
2. Evaluate the FAQ results:
   - If one or more FAQs directly and adequately answer the question, call the answer tool immediately with the FAQ content
   - If FAQs are partially relevant, note them and continue to step 3 for more detail
   - If FAQs are irrelevant or no results returned, proceed to step 3
3. Use vector_search to find relevant document chunks in the knowledge graph
4. Follow graph edges to find authoritative sources (get_neighbors with CITES, IMPLEMENTS edges)
5. Trace authority chains (get_authority_chain) to cite the correct level of authority
6. When you have enough information, call the answer tool

ALWAYS:
- Cite specific document IDs, section numbers, and statute references
- Distinguish between different authority levels: Constitution > Statutes > Admin Rules > WPAM > FAQs > Guides
- Note when guidance has been superseded (check SUPERSEDES edges)

NEVER:
- Make up statute references or section numbers
- Provide advice without citing sources
- Ignore SUPERSEDES relationships (always check for newer guidance)
- Skip faq_search — even if the question seems complex, FAQs may have a direct answer

When you have enough information, call the 'answer' tool with your complete response in Markdown format."""

AGENTIC_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514")


def process_event(event: dict) -> UserQuery:
    """Parse input event. Receives clean {query, query_id, session_id} from EventBridge $.detail extraction."""
    try:
        return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


def run_agentic_loop(query: str) -> tuple[str, list[str], list[RAGDocument]]:
    """
    Run Claude's agentic loop against Neptune.

    Returns:
        (answer_text, cited_doc_ids, rag_documents)
    """
    messages = [{"role": "user", "content": [{"text": query}]}]
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        logger.info(f"Agentic loop turn {turn + 1}/{MAX_TURNS}")

        response = bedrock.converse(
            modelId=AGENTIC_MODEL_ID,
            messages=messages,
            system=[{"text": SYSTEM_PROMPT}],
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

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
                    all_chunks.append(chunk)

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = result.get("cited_doc_ids", [])
                all_doc_ids.update(cited)
                rag_docs = _build_rag_documents(all_chunks, all_doc_ids)
                return answer, list(all_doc_ids), rag_docs

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                }
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        answer = "I was unable to find a complete answer within the allowed number of search steps. Please try rephrasing your question."

    rag_docs = _build_rag_documents(all_chunks, all_doc_ids)
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


def _build_rag_documents(chunks: list[dict], doc_ids: set[str]) -> list[RAGDocument]:
    """Build RAGDocument list from collected chunks."""
    docs_by_id: dict[str, RAGDocument] = {}

    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text", "")

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
            )
        else:
            existing = docs_by_id[doc_id]
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source or _generate_source_url(chunk, None),
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

        result = RetrieveResult(
            successful=True,
            generate_response_job=GenerateResponseJob(
                query=user_query.query,
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                documents=documents,
                faqs=None,
            ),
            stream_documents_job=StreamResourcesJob(
                query_id=user_query.query_id,
                session_id=user_query.session_id,
                faqs=None,
                documents=documents,
            ),
        )

        return result.model_dump()

    except Exception as e:
        logger.error(f"Agentic retrieval failed: {e}", exc_info=True)
        if session_id:
            asyncio.run(report_error(e, session_id=session_id))

        return RetrieveResult(
            successful=False,
            generate_response_job=None,
            stream_documents_job=None,
        ).model_dump()
