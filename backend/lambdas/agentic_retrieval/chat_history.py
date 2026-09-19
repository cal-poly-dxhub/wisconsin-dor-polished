"""Chat history persistence (DynamoDB get/save)."""

import logging
import os
import re

import boto3
from step_function_types.models import FAQResource, RAGDocument

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
CHAT_HISTORY_TABLE = os.environ.get("CHAT_HISTORY_TABLE_NAME", "")
MAX_HISTORY_TURNS = 5

dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)

# Markdown-stripping patterns applied to prior answers when they are replayed
# as conversation context (see sanitize_answer_for_history). The full DynamoDB
# answer keeps its rendered markdown; only the in-context copy is flattened.
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:doc:|https?:)[^)]*\)")  # [text](doc:..|http..) -> text
_MD_HEADING = re.compile(r"^#{1,6}[ \t]*", flags=re.M)  # ## Heading -> Heading
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")  # **x** -> x
_MD_ITALIC = re.compile(r"\*([^*]+)\*")  # *x* -> x
_MD_CODE = re.compile(r"`([^`]+)`")  # `x` -> x
_MD_HR = re.compile(r"^[ \t]*-{3,}[ \t]*$", flags=re.M)  # --- -> (blank)
_MD_BULLET = re.compile(r"^[ \t]*[-*][ \t]+", flags=re.M)  # normalize bullets to "- "
_EMOJI = re.compile(r"[\U0001f300-\U0001faff☀-➿]")  # pictographs/symbols
_BLANK_LINES = re.compile(r"\n{3,}")


def sanitize_answer_for_history(answer: str) -> str:
    """Flatten a rendered markdown answer to plain text for conversation replay.

    Prior answers are re-sent as ``assistant`` turns on every follow-up (and on
    every loop turn within it). The model needs the substance, not the
    presentation layer, so this strips citation-link syntax, headings, bold /
    italic / code markers, horizontal rules, and emoji while preserving all
    prose. Deterministic: same input always yields the same output. The full
    markdown answer stays untouched in DynamoDB for the UI and triage.
    """
    if not answer:
        return answer
    s = _MD_LINK.sub(r"\1", answer)
    s = _MD_HEADING.sub("", s)
    s = _MD_BOLD.sub(r"\1", s)
    s = _MD_ITALIC.sub(r"\1", s)
    s = _MD_CODE.sub(r"\1", s)
    s = _MD_HR.sub("", s)
    s = _MD_BULLET.sub("- ", s)
    s = _EMOJI.sub("", s)
    s = _BLANK_LINES.sub("\n\n", s)
    return s.strip()


def get_chat_history(session_id: str) -> list[dict]:
    """Fetch prior {query, answer} pairs for a session, oldest first.

    A turn on which the adequacy judge asked a clarifying question also carries
    a ``clarification`` dict ({axis, question, options, original_query}).

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
        history = []
        for item in items:
            if not (item.get("query") and item.get("answer")):
                continue
            turn: dict = {
                "query": item["query"],
                "answer": sanitize_answer_for_history(item["answer"]),
            }
            # A clarification the adequacy judge asked on this turn, if any.
            # resolve_pending_clarification() keys the next message off it, and
            # the judge sees it in history so it never asks twice. Turns written
            # before this field existed simply omit it.
            pending = item.get("clarification")
            if isinstance(pending, dict) and pending.get("options"):
                turn["clarification"] = {
                    "axis": str(pending.get("axis") or ""),
                    "question": str(pending.get("question") or ""),
                    "options": [str(o) for o in pending.get("options") or []],
                    "original_query": str(pending.get("original_query") or item["query"]),
                }
            history.append(turn)
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
        logger.info(f"Loaded {len(history)} history turn(s) for session {session_id}")
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
    seeded_flowchart: dict | None = None,
    clarification: dict | None = None,
) -> None:
    """Persist a query/answer pair (with resources, flowchart, and trace) to history.

    ``clarification`` — {axis, question, options, original_query} — records a
    question the adequacy judge put to the user on this turn, so the next
    message can be folded back into the original question deterministically
    (``adequacy_judge.resolve_pending_clarification``). Optional and additive:
    turns without one are stored exactly as before.
    """
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

        # Persist the seeded decision flowchart so the "Walk the flowchart"
        # banner/card survive a page reload / session resume. Stored as a JSON
        # string (nested nodes/edges are deep — DynamoDB maps get unwieldy and
        # this mirrors how `trace` is stored).
        if seeded_flowchart:
            item["flowchart"] = json.dumps(seeded_flowchart)

        # Stored as a native DynamoDB map (unlike trace/flowchart, which are
        # deep): four shallow string/list-of-string fields the next turn reads
        # back directly.
        if clarification and clarification.get("options"):
            item["clarification"] = {
                "axis": str(clarification.get("axis") or ""),
                "question": str(clarification.get("question") or ""),
                "options": [str(o) for o in clarification.get("options") or []],
                "original_query": str(clarification.get("original_query") or query),
            }

        table = dynamodb_resource.Table(CHAT_HISTORY_TABLE)
        table.put_item(Item=item)
        logger.info(f"Saved chat history for session {session_id}, query {query_id}")
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to save chat history for session {session_id}",
            exc_info=True,
        )
