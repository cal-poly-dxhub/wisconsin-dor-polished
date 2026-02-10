import asyncio
import hashlib
import logging
import os

import boto3
import pydantic
from boto3.dynamodb.types import TypeDeserializer
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    FAQ,
    ClassifierResult,
    FAQResource,
    RetrieveJob,
    UserQuery,
)

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))
faq_kb_id = os.environ.get("FAQ_KNOWLEDGE_BASE_ID")
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
    """
    Parse a Q&A document that may have multi-line answers.

    Expected format:
    Q: <question text>
    A: <answer text, possibly spanning multiple lines>
    """
    lines = document.strip().split("\n")

    # Find the line starting with "Q:"
    question_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Q:"):
            question_idx = i
            break

    # Find the line starting with "A:"
    answer_idx = None
    for i, line in enumerate(lines):
        if line.startswith("A:"):
            answer_idx = i
            break

    # Validate that we found both Q and A
    if question_idx is None or answer_idx is None:
        logger.error(f"Invalid Q&A document format - missing Q: or A: prefix: {document[:100]}...")
        return None

    if question_idx >= answer_idx:
        logger.error(f"Invalid Q&A document format - Q: must come before A:: {document[:100]}...")
        return None

    # Extract question (from Q: line)
    question = lines[question_idx][2:].strip()

    # Extract answer (from A: line and all subsequent lines)
    answer_lines = [lines[answer_idx][2:].strip()]  # First line after "A:"
    answer_lines.extend(lines[answer_idx + 1:])  # All remaining lines
    answer = "\n".join(answer_lines).strip()

    if not question or not answer:
        logger.error(f"Invalid Q&A document format - empty question or answer: {document[:100]}...")
        return None

    return {"q": question, "a": answer}


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

    # Load retrieval config from DynamoDB
    config = get_retrieval_config()
    num_results = config.get("numFAQResults", 5)

    retrieval_config = {
        "vectorSearchConfiguration": {
            "numberOfResults": int(num_results),  # Convert Decimal to int for Bedrock API
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
