import asyncio
import hashlib
import logging
import os

import boto3
import pydantic
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    FAQ,
    ClassifierResult,
    FAQResource,
    RetrieveJob,
    UserQuery,
)

NUM_FAQ_RESULTS = 5

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))
faq_kb_id = os.environ.get("FAQ_KNOWLEDGE_BASE_ID")

bedrock_ar = boto3.client("bedrock-agent-runtime")


def process_query(event: dict) -> UserQuery:
    # Extract data from EventBridge event

    try:
        if "detail" in event:
            # EventBridge event structure
            detail = event["detail"]
            return UserQuery.model_validate(detail)
        else:
            return UserQuery.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Error processing query: {e}")
        raise ValidationError() from e


def parse_qa_document(document: str) -> dict | None:
    lines = document.strip().split("\n")
    if len(lines) != 2 or not lines[0].startswith("Q:") or not lines[1].startswith("A:"):
        logger.error(f"Invalid Q&A document format: {document}")
        return None

    return {"q": lines[0][2:].strip(), "a": lines[1][2:].strip()}


def process_faq_results(results: dict) -> FAQResource:
    faqs = []
    for result in results:
        content_hash = hashlib.sha256(result["content"]["text"].encode()).hexdigest()
        faq_id = content_hash[:7]
        qa = parse_qa_document(result["content"]["text"])
        if not qa:
            continue
        question = qa["q"]
        answer = qa["a"]
        faqs.append(
            FAQ(
                faq_id=faq_id,
                question=question,
                answer=answer,
            )
        )
    return FAQResource(faqs=faqs) if faqs else None


def try_match_faq(query: str) -> FAQResource | None:
    if not faq_kb_id:
        logger.error("FAQ knowledge base ID is not set; returning no FAQ resources.")
        return None

    retrieval_config = {
        "vectorSearchConfiguration": {
            "numberOfResults": NUM_FAQ_RESULTS,
            "overrideSearchType": "SEMANTIC",
        }
    }

    response = bedrock_ar.retrieve(
        knowledgeBaseId=faq_kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration=retrieval_config,
    )

    faqs = process_faq_results(response["retrievalResults"])

    return faqs


def handler(event: dict, context) -> dict:
    session_id: str | None = None

    try:
        user_query = process_query(event)
        session_id = user_query.session_id
        logger.info(f"Received user query: {user_query.model_dump()}")
        faq_resources = try_match_faq(user_query.query)

        # Trigger a retrieval job using FAQs
        return ClassifierResult(
            successful=True,
            query_class="rag",
            retrieve_job=RetrieveJob(
                query=user_query.query,
                query_id=user_query.query_id,
                faqs=faq_resources,
                session_id=user_query.session_id,
            ),
        ).model_dump()
    except Exception as e:  # Error was logged at root.
        if session_id:
            asyncio.run(report_error(e, session_id=session_id))

        return ClassifierResult(
            successful=False,
            query_class=None,
        ).model_dump()
