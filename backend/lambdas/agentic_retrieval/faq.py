"""FAQ search, parsing, and URL resolution."""

import logging
import os
import re

import boto3
from step_function_types.models import FAQ, FAQResource

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
FAQ_URL_TABLE = os.environ.get("FAQ_URL_TABLE_NAME", "")
MAX_FAQS = 3

dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)

_FAQ_QA_RE = re.compile(r"^Q:\s*(.*?)\s*\nA:\s*(.*)$", re.DOTALL)


def _faq_url_table():
    """Return the FAQ-URL DynamoDB Table resource (separate fn so tests can patch)."""
    return dynamodb_resource.Table(FAQ_URL_TABLE)


def normalize_faq_question(text: str) -> str:
    """Canonical FAQ question key — must match scripts/graphrag/faq_url_map.py."""
    if not text:
        return ""
    cleaned = text.replace("​", "").replace("\xa0", " ").replace("﻿", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned.rstrip("?.").strip()


def lookup_faq_url(question: str) -> str | None:
    """Resolve a FAQ's public source URL by normalized question; None on miss/error."""
    if not FAQ_URL_TABLE:
        return None
    try:
        resp = _faq_url_table().get_item(
            Key={"normalized_question": normalize_faq_question(question)}
        )
        item = resp.get("Item")
        return item.get("source_url") if item else None
    except Exception:  # noqa: BLE001
        logger.warning("FAQ URL lookup failed", exc_info=True)
        return None


def parse_faq_text(text: str) -> tuple[str, str] | None:
    """Split a KB chunk like 'Q: ...\\nA: ...' into (question, answer).

    Returns None when the chunk doesn't match the expected Q/A shape.
    """
    match = _FAQ_QA_RE.match(text.strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def faq_id_from_uri(uri: str) -> str:
    """Derive a stable FAQ id from its S3 source URI."""
    if not uri:
        return "faq"
    stem = uri.rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] or uri


def build_faq_resource(faq_results: list[dict]) -> FAQResource | None:
    """Convert raw `faq_search` results into a FAQResource.

    Returns None when nothing parses so downstream jobs treat the query as FAQ-less.
    """
    faqs: list[FAQ] = []
    for entry in faq_results[:MAX_FAQS]:
        parsed = parse_faq_text(entry.get("text", ""))
        if not parsed:
            continue
        question, answer = parsed
        faqs.append(
            FAQ(
                faq_id=faq_id_from_uri(entry.get("source_uri", "")),
                question=question,
                answer=answer,
                source_url=lookup_faq_url(question),
            )
        )
    return FAQResource(faqs=faqs) if faqs else None


def build_cited_faq_resource(
    faq_results: list[dict],
    cited_doc_ids: set[str],
) -> FAQResource | None:
    """Convert cited FAQ KB chunks into FAQResource for downstream synthesis."""
    cited_faq_results = [
        entry
        for entry in faq_results[:MAX_FAQS]
        if faq_id_from_uri(entry.get("source_uri", "")) in cited_doc_ids
    ]
    return build_faq_resource(cited_faq_results)


def faq_search_direct(query: str, neptune_client, execute_tool_fn) -> dict:
    """Run the `faq_search` tool with the user's verbatim query.

    Bypasses Claude to ensure turn 0 is a deterministic, single-tool call.
    """
    return execute_tool_fn("faq_search", {"query": query}, neptune_client)
