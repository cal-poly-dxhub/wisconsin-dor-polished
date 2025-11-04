import asyncio
import logging
import os

import pydantic
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
    faqs = [
        FAQ(
            faq_id="faq-001",
            question="Example Question?",
            answer="This is an example answer.",
        ),
        FAQ(
            faq_id="faq-002",
            question="Another Example Question?",
            answer="This is another example answer.",
        ),
        FAQ(
            faq_id="faq-003",
            question="Yet Another Example Question?",
            answer="This is yet another example answer.",
        ),
    ]
    if "example" in query.lower():
        return FAQResource(faqs=faqs)

    return None


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
