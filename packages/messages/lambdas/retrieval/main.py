import asyncio
import logging
import os
import re
from typing import Literal

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

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


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


def retrieve_documents(query: str) -> DocumentQueryResult:
    """
    Retrieves knowledge base for relevant documents or FAQ based on query content.
    Returns FAQResource if "FAQ" is found in query, otherwise returns list of RAGDocuments.

    TODO: this is replaced with knowledge base querying down the road
    """

    # Check if query contains "FAQ" (case-insensitive)
    if re.search(r"\bFAQ\b", query, re.IGNORECASE):
        # Return FAQ resource
        return DocumentQueryResult(
            document_type="FAQ",
            faq=FAQResource(
                question="How do I get started with Python programming?",
                answer="Python is a beginner-friendly programming language. Start by installing Python from python.org, then try running simple scripts in the interactive shell or a text editor.",
            ),
        )
    else:
        # Return RAG documents
        return DocumentQueryResult(
            document_type="RAG",
            documents=[
                RAGDocument(
                    document_id="doc-001",
                    title="Example Document",
                    content="This is an example document relevant to the query.",
                    source="https://example.com",
                ),
                RAGDocument(
                    document_id="doc-002",
                    title="Advanced Python",
                    content=(
                        "This document delves into advanced Python programming concepts, such as decorators, generators, and context managers. "
                        "It also explores metaprogramming techniques, custom class creation, and performance optimization strategies, "
                        "helping developers write more efficient and maintainable code for complex applications."
                    ),
                    source="https://www.example.com/",
                ),
                RAGDocument(
                    document_id="doc-003",
                    title="Data Science with Python",
                    content=(
                        "Python has become a cornerstone of data science, offering powerful libraries like Pandas, NumPy, and Matplotlib. "
                        "This document provides a comprehensive guide to data manipulation, statistical analysis, and data visualization, "
                        "enabling users to extract meaningful insights from large datasets and present them effectively."
                    ),
                    source="https://www.example.com/",
                ),
                RAGDocument(
                    document_id="doc-004",
                    title="Web Development",
                    content=(
                        "Python is a popular choice for web development, thanks to frameworks like Flask and Django. "
                        "This document covers the fundamentals of building dynamic, secure, and scalable web applications, "
                        "including routing, database integration, and deploying applications to production environments."
                    ),
                    source="https://www.example.com/",
                ),
            ],
        )


def handler(event: dict, context) -> dict:
    """
    Processes a RetrieveJob, retrieves relevant documents from the knowledge
    base, and produces a RetrieveResult.
    """

    job: RetrieveJob | None = None

    try:
        job = process_event(event)
        docs = retrieve_documents(job.query)

        # Handle FAQ vs RAG documents based on document_type
        if docs.document_type == "FAQ" and docs.faq is not None:
            # FAQ case
            result = RetrieveResult(
                successful=True,
                generate_response_job=GenerateResponseJob(
                    query=job.query,
                    query_id=job.query_id,
                    session_id=job.session_id,
                    resource_type="faq",
                    resources=docs.faq,
                ),
                stream_documents_job=StreamResourcesJob(
                    query_id=job.query_id,
                    session_id=job.session_id,
                    resource_type="faq",
                    content=docs.faq,
                ),
            )
        elif docs.document_type == "RAG" and docs.documents is not None:
            # RAG documents case
            documents_resource = DocumentResource(documents=docs.documents)
            result = RetrieveResult(
                successful=True,
                generate_response_job=GenerateResponseJob(
                    query=job.query,
                    query_id=job.query_id,
                    session_id=job.session_id,
                    resource_type="documents",
                    resources=documents_resource,
                ),
                stream_documents_job=StreamResourcesJob(
                    query_id=job.query_id,
                    session_id=job.session_id,
                    resource_type="documents",
                    content=documents_resource,
                ),
            )
        else:
            raise ValueError(
                f"Invalid document query result: {docs.document_type} with no corresponding content"
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
