import asyncio
import hashlib
import logging
import os
import re
from typing import Literal

import boto3
import pydantic
from boto3.dynamodb.types import TypeDeserializer
from pydantic import BaseModel
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    DocumentResource,
    FAQResource,
    GenerateResponseJob,
    RAGDocument,
    RetrieveJob,
    RetrieveResult,
)

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))
rag_kb_id = os.environ.get("RAG_KNOWLEDGE_BASE_ID")
model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")

bedrock_ar = boto3.client("bedrock-agent-runtime")
dynamodb_client = boto3.client("dynamodb")


def get_retrieval_config() -> dict:
    """
    Load retrieval configuration from DynamoDB.

    Returns:
        Dictionary with retrieval config values, or defaults if not found
    """
    default_config = {
        "numRAGResults": 10,
        "numFAQResults": 5,
        "maxDocumentsToClient": 5,
        "sourceIdPriority": []
    }

    if not model_config_table_name:
        logger.warning("MODEL_CONFIG_TABLE_NAME not set, using defaults")
        return default_config

    try:
        response = dynamodb_client.get_item(
            TableName=model_config_table_name,
            Key={"id": {"S": "retrievalConfig"}}
        )

        item = response.get("Item")
        if not item:
            logger.warning("retrievalConfig not found in DynamoDB, using defaults")
            return default_config

        deserializer = TypeDeserializer()
        config = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Remove the 'id' field
        config.pop("id", None)

        logger.info(f"Loaded retrieval config from DynamoDB: {config}")
        return config
    except Exception as e:
        logger.error(f"Error loading retrieval config from DynamoDB: {e}", exc_info=True)
        return default_config


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
                source_id=meta.get("source_id"),
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

    # Load retrieval config from DynamoDB
    config = get_retrieval_config()
    num_results = config.get("numRAGResults", 10)

    retrieval_config = {
        "vectorSearchConfiguration": {
            "numberOfResults": int(num_results),  # Convert Decimal to int for Bedrock API
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
        )

        return result.model_dump()
    except Exception as e:
        logger.error(f"Unexpected error in retrieval lambda: {e}", exc_info=True)
        if job is not None:
            asyncio.run(report_error(e, session_id=job.session_id))

        return RetrieveResult(
            successful=False,
            generate_response_job=None,
        ).model_dump()
