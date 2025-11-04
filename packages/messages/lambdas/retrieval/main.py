import asyncio
import hashlib
import logging
import os
import re
from typing import Literal

import boto3
import pydantic
from pydantic import BaseModel
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    GenerateResponseJob,
    RAGDocument,
    RetrieveJob,
    RetrieveResult,
    StreamResourcesJob,
)

# Max number of documents to retrieve on a RAG query.
NUM_RAG_RESULTS = 10

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))
rag_kb_id = os.environ.get("RAG_KNOWLEDGE_BASE_ID")

bedrock_ar = boto3.client("bedrock-agent-runtime")


def process_event(event: dict) -> RetrieveJob:
    """
    Parses the input event.
    """

    try:
        return RetrieveJob.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error while validating event: {e}", exc_info=True)
        raise ValidationError() from e


class DocumentQueryResult(BaseModel):
    document_type: Literal["FAQ", "RAG"]
    documents: list[RAGDocument] | None = None
    faq: FAQResource | None = None


def process_retrieve_results(results: dict) -> DocumentResource:
    """
    Defines a data transformation from the Bedrock knowledge base
    metadata type to RAGDocuments wrapped in a DocumentResource
    (expected by streaming step).
    """

    documents = []
    for result in results:
        meta = result["metadata"]
        content_hash = hashlib.sha256(result["content"]["text"].encode()).hexdigest()

        # Trim file extension from doc_id and append first 7 chars of hash
        document_id = meta["doc_id"].rsplit(".", 1)[0] + content_hash[:7]

        documents.append(
            RAGDocument(
                document_id=document_id,
                title=meta["doc_id"],
                source=meta["source_url"],
                content=result["content"]["text"],
            )
        )

    return DocumentResource(documents=documents)


def retrieve_documents(query: str) -> DocumentResource:
    """
    Retrieves documents from the knowledge base based on the query.
    """

    if not rag_kb_id:
        logger.error("RAG knowledge base ID is not set; returning no documents.")
        return DocumentResource(documents=[])

    retrieval_config = {
        "vectorSearchConfiguration": {
            "numberOfResults": NUM_RAG_RESULTS,
            "overrideSearchType": "SEMANTIC",
        }
    }

    response = bedrock_ar.retrieve(
        knowledgeBaseId=rag_kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration=retrieval_config,
    )

    docs = process_retrieve_results(response["retrievalResults"])

    return docs


def handler(event: dict, context) -> dict:
    """
    Processes a RetrieveJob, retrieves relevant documents from the knowledge
    base, and produces a RetrieveResult.
    """

    job: RetrieveJob | None = None

    try:
        job = process_event(event)
        docs = retrieve_documents(job.query)
        result = RetrieveResult(
            successful=True,
            generate_response_job=GenerateResponseJob(
                query=job.query,
                query_id=job.query_id,
                session_id=job.session_id,
                documents=docs,
                faqs=job.faqs,
            ),
            stream_documents_job=StreamResourcesJob(
                query_id=job.query_id,
                session_id=job.session_id,
                faqs=job.faqs,
                documents=docs,
            ),
        )

        return result.model_dump()
    except Exception as e:
        logger.error(f"Unexpected error in retrieval lambda: {e}", exc_info=True)
        if job is not None:
            asyncio.run(report_error(e, session_id=job.session_id))

        return RetrieveResult(
            successful=False,
            generate_response_job=None,
            stream_documents_job=None,
        ).model_dump()
