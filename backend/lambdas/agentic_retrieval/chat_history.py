"""Chat history persistence (DynamoDB get/save)."""

import logging
import os

import boto3
from step_function_types.models import FAQ, FAQResource, RAGDocument

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
CHAT_HISTORY_TABLE = os.environ.get("CHAT_HISTORY_TABLE_NAME", "")
MAX_HISTORY_TURNS = 5

dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)


def get_chat_history(session_id: str) -> list[dict[str, str]]:
    """Fetch prior {query, answer} pairs for a session, oldest first.

    Returns an empty list if the table isn't configured or the query fails;
    history is an enrichment, not a correctness requirement.
    """
    if not CHAT_HISTORY_TABLE or not session_id:
        return []
    try:
        table = dynamodb_resource.Table(CHAT_HISTORY_TABLE)
        response = table.query(
            IndexName="sessionIdKey",
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
            ScanIndexForward=True,
        )
        items = response.get("Items", [])
        history = [
            {"query": item["query"], "answer": item["answer"]}
            for item in items
            if item.get("query") and item.get("answer")
        ]
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
        logger.info(
            f"Loaded {len(history)} history turn(s) for session {session_id}"
        )
        return history
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch chat history for session {session_id}",
            exc_info=True,
        )
        return []


def save_chat_history(
    session_id: str,
    query_id: str,
    query: str,
    answer: str,
    rag_documents: list[RAGDocument] | None = None,
    faq_resource: "FAQResource | None" = None,
    trace_log: list[dict] | None = None,
) -> None:
    """Persist a query/answer pair (with resources and trace) to the chat history table."""
    if not CHAT_HISTORY_TABLE or not session_id:
        return
    try:
        import datetime
        import json

        item: dict = {
            "queryId": query_id,
            "sessionId": session_id,
            "gsi1pk": "ALL",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "query": query,
            "answer": answer,
        }

        if trace_log:
            item["trace"] = json.dumps(trace_log)

        resources: list[dict] = []
        if rag_documents:
            for doc in rag_documents:
                data: dict = {
                    "documentId": doc.document_id,
                    "title": doc.title,
                    "content": doc.content,
                    "source": doc.source,
                    "discoveryTag": doc.discovery_tag,
                }
                if doc.authority_level is not None:
                    data["authorityLevel"] = doc.authority_level
                if doc.source_url is not None:
                    data["sourceUrl"] = doc.source_url
                if doc.s3_key is not None:
                    data["s3Key"] = doc.s3_key
                if doc.start_page is not None:
                    data["startPage"] = doc.start_page
                if doc.end_page is not None:
                    data["endPage"] = doc.end_page
                if doc.edition_year is not None:
                    data["editionYear"] = doc.edition_year
                resources.append({"type": "document", "data": data})
        if faq_resource:
            for faq in faq_resource.faqs:
                faq_data: dict = {
                    "faqId": faq.faq_id,
                    "question": faq.question,
                    "answer": faq.answer,
                }
                if faq.source_url is not None:
                    faq_data["sourceUrl"] = faq.source_url
                resources.append({"type": "faq", "data": faq_data})
        if resources:
            item["resources"] = resources

        table = dynamodb_resource.Table(CHAT_HISTORY_TABLE)
        table.put_item(Item=item)
        logger.info(f"Saved chat history for session {session_id}, query {query_id}")
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to save chat history for session {session_id}",
            exc_info=True,
        )
