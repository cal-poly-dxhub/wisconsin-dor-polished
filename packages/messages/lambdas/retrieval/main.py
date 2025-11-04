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


def retrieve_documents(query: str) -> DocumentResource:
    """
    Retrieves documents from the knowledge base based on the query.

    TODO: this is replaced with knowledge base querying down the road
    """

    return DocumentResource(
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
