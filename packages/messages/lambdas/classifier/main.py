import asyncio
import logging
import os

import pydantic
from step_function_types.errors import ValidationError, report_error
from step_function_types.models import (
    ClassifierResult,
    FAQResource,
    GenerateResponseJob,
    RetrieveJob,
    StreamResourcesJob,
    UserQuery,
)

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


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


def try_match_faq(query: str) -> FAQResource | None:
    # TODO: populate this with FAQ retrieval logic from the knowledge base
    if "example" in query.lower():
        return FAQResource(question="Example Question?", answer="This is an example answer.")
    return None


def handler(event: dict, context) -> dict:
    session_id: str | None = None

    try:
        user_query = process_query(event)
        session_id = user_query.session_id
        logger.info(f"Received user query: {user_query.model_dump()}")
        faq_resource = try_match_faq(user_query.query)

        if faq_resource:
            # Trigger streaming and generation jobs if we found a matching FAQ
            logger.info("FAQ match found, preparing response jobs.")
            return ClassifierResult(
                query_class="faq",
                stream_documents_job=StreamResourcesJob(
                    query_id=user_query.query_id,
                    session_id=user_query.session_id,
                    resource_type="faq",
                    content=faq_resource,
                ),
                generate_response_job=GenerateResponseJob(
                    query=user_query.query,
                    query_id=user_query.query_id,
                    session_id=user_query.session_id,
                    resource_type="faq",
                    resources=faq_resource,
                ),
            ).model_dump()

        else:
            # Otherwise trigger a retrieval job
            logger.info("No FAQ match found, preparing retrieval job.")
            return ClassifierResult(
                query_class="rag",
                retrieve_job=RetrieveJob(
                    query=user_query.query,
                    query_id=user_query.query_id,
                    session_id=user_query.session_id,
                ),
            ).model_dump()
    except Exception as e: # Error was logged at root.
        if session_id:
            asyncio.run(report_error(e, session_id))

        return ClassifierResult(query_class=None).model_dump()
