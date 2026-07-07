"""Phase B: answer generation and token-by-token streaming.

Builds the answer context from Phase A's cited docs/chunks, then calls
converse_stream() with NO tools — pure text output streamed over WebSocket.
"""

import json
import logging
import re
import time

from graph.neptune_client import NeptuneClient
from prompt import ANSWER_STREAM_SYSTEM_PROMPT, PERSONA_PROMPTS
from streaming.bedrock import converse_stream_with_cache
from tracing.runtime import emit as _emit
from tracing.runtime import log_event as _log
from websocket_utils.models import AnswerEventType, FragmentContent, FragmentMessage
from websocket_utils.utils import WebSocketServer

from config import AGENTIC_MODEL_ID, bedrock

from .heartbeat import start_heartbeat

logger = logging.getLogger(__name__)

_PERSONA_KEY = {
    "government": "personaGovernment",
    "citizen": "personaCitizen",
}


def apply_persona(base_prompt: str, persona: str | None) -> str:
    key = _PERSONA_KEY.get(persona or "")
    suffix = PERSONA_PROMPTS.get(key or "") if key else ""
    return base_prompt + suffix if suffix else base_prompt


def build_answer_context(
    query: str,
    cited_chunks: list[dict],
    cited_doc_ids: set[str],
    discovery: dict[str, str],
    fetched_opinions: dict[str, dict],
    answer_plan: str,
    chat_history: list[dict] | None = None,
    neptune_client: NeptuneClient | None = None,
) -> str:
    """Build the context message for Phase B answer generation."""
    parts = []

    if chat_history:
        parts.append("## Prior Conversation")
        for turn in chat_history[-3:]:  # last 3 turns for context
            parts.append(f"User: {turn.get('query', '')}")
            parts.append(f"Assistant: {turn.get('answer', '')[:500]}")
        parts.append("")

    parts.append(f"## User Question\n{query}\n")

    if answer_plan:
        parts.append(f"## Answer Plan\n{answer_plan}\n")

    parts.append("## Retrieved Documents and Chunks\n")

    # Group chunks by document
    chunks_by_doc: dict[str, list[dict]] = {}
    for chunk in cited_chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunks_by_doc.setdefault(doc_id, []).append(chunk)

    for doc_id in sorted(cited_doc_ids):
        doc_chunks = chunks_by_doc.get(doc_id, [])
        # Get document metadata
        doc_info = None
        if neptune_client:
            try:
                doc_info = neptune_client.get_document(doc_id)
            except Exception:
                pass

        title = (doc_info or {}).get("title", doc_id)
        authority = (doc_info or {}).get("authority_level", "")

        parts.append(f"### [{title}](doc:{doc_id})")
        if authority:
            parts.append(f"Authority level: {authority}")

        if doc_chunks:
            # Sub-group by heading so chapter boundaries are unambiguous
            chunks_by_heading: dict[str, list[dict]] = {}
            for chunk in doc_chunks:
                h = chunk.get("heading", "")
                chunks_by_heading.setdefault(h, []).append(chunk)

            for heading, h_chunks in chunks_by_heading.items():
                if heading:
                    pages = sorted({c.get("start_page") for c in h_chunks if c.get("start_page")})
                    page_range = (
                        f" (pages {pages[0]}-{pages[-1]})"
                        if len(pages) > 1
                        else (f" (page {pages[0]})" if pages else "")
                    )
                    parts.append(f"\n#### {heading}{page_range}")
                for chunk in h_chunks:
                    page = chunk.get("start_page")
                    page_ref = f" (page {page})" if page else ""
                    parts.append(f"\n**Chunk{page_ref}:**")
                    parts.append(chunk.get("text", "")[:2000])

        # For statute docs, include a section→page index so the model
        # can look up correct page numbers for sections not in the
        # retrieved chunks.
        if doc_id.startswith("statutes-") and neptune_client:
            try:
                sections = neptune_client.list_document_sections(doc_id)
                if sections:
                    # Extract numeric chapter (e.g. "70" from "statutes-70")
                    chapter_match = re.match(r"statutes-(\d+)", doc_id)
                    chapter = (
                        chapter_match.group(1) if chapter_match else doc_id.replace("statutes-", "")
                    )
                    # Same pattern the statute chunker uses to identify
                    # canonical section headings (rejects cross-references
                    # like "70.32 (2) (a) 6..." that appear inside other
                    # sections).
                    section_pattern = re.compile(
                        rf"^({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)"
                    )
                    # Fallback: any heading starting with the section number
                    # (used when no canonical heading exists)
                    loose_pattern = re.compile(rf"^{re.escape(chapter)}\.\d+")
                    index_lines = []
                    seen_sections: dict[str, int] = {}
                    fallback_sections: dict[str, int] = {}
                    for sec in sections:
                        heading = sec.get("heading", "")
                        first_page = sec.get("first_page")
                        if not heading or first_page is None:
                            continue
                        m = section_pattern.match(heading)
                        if m:
                            sec_num = m.group(1)
                            if sec_num not in seen_sections:
                                seen_sections[sec_num] = first_page
                        else:
                            m2 = loose_pattern.match(heading)
                            if m2:
                                sec_num = m2.group(0)
                                if sec_num not in fallback_sections:
                                    fallback_sections[sec_num] = first_page
                    merged = {**fallback_sections, **seen_sections}
                    # Search ALL cited chunks for statute section references,
                    # not just chunks from this statute. Other documents (guides,
                    # WPAM, admin rules) frequently reference statute sections,
                    # and the model needs page numbers for those references.
                    all_text_blob = " ".join(c.get("text", "") for c in cited_chunks)
                    referenced = set(
                        re.findall(rf"{re.escape(chapter)}\.\d+[A-Za-z\-]*", all_text_blob)
                    )
                    for sec_num, page in merged.items():
                        if sec_num in referenced:
                            index_lines.append(f"- § {sec_num} → page {page}")
                    if index_lines:
                        parts.append(
                            "\n**Section Page Index** (use these page numbers for `#page=N` citations; subsections like 70.32(2)(c)1g use the parent section's page, e.g. § 70.32 → page 23 means all 70.32(...) subsections start at page 23):"
                        )
                        parts.extend(index_lines)
            except Exception:
                pass

        # Include case opinion text if available
        if doc_id in fetched_opinions:
            opinion = fetched_opinions[doc_id]
            parts.append(f"\n**Case Opinion ({opinion.get('citation', '')}):**")
            parts.append(opinion.get("text", "")[:3000])

        parts.append("")

    # Fallback: build section page indexes for statute chapters referenced
    # in cited chunks but not explicitly in cited_doc_ids. The agent often
    # discovers statutes via get_neighbors but doesn't fetch them directly.
    if neptune_client and cited_chunks:
        all_text_blob = " ".join(c.get("text", "") for c in cited_chunks)
        # Match patterns like "70.32", "73.03", "74.485" — real statute refs.
        # Also handles OCR artifacts with spaces: "70. 32", "73. 03".
        raw_refs = re.findall(r"(\d+)\.\s*(\d+)", all_text_blob)
        referenced_chapters: set[str] = set()
        for chap, _sec in raw_refs:
            doc_id_candidate = f"statutes-{chap}"
            if doc_id_candidate not in cited_doc_ids and int(chap) >= 70:
                referenced_chapters.add(chap)

        for chapter in sorted(referenced_chapters):
            stat_doc_id = f"statutes-{chapter}"
            try:
                sections = neptune_client.list_document_sections(stat_doc_id)
                if not sections:
                    continue
                section_pattern = re.compile(
                    rf"^({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)"
                )
                loose_pattern = re.compile(rf"^{re.escape(chapter)}\.\d+")
                seen_sections: dict[str, int] = {}
                fallback_sections: dict[str, int] = {}
                for sec in sections:
                    heading = sec.get("heading", "")
                    first_page = sec.get("first_page")
                    if not heading or first_page is None:
                        continue
                    m = section_pattern.match(heading)
                    if m:
                        sec_num = m.group(1)
                        if sec_num not in seen_sections:
                            seen_sections[sec_num] = first_page
                    else:
                        m2 = loose_pattern.match(heading)
                        if m2:
                            sec_num = m2.group(0)
                            if sec_num not in fallback_sections:
                                fallback_sections[sec_num] = first_page
                merged = {**fallback_sections, **seen_sections}
                # Filter to sections actually referenced in chunk text
                referenced = set(
                    re.findall(rf"{re.escape(chapter)}\.\s*\d+[A-Za-z\-]*", all_text_blob)
                )
                # Normalize OCR spaces: "70. 32" → "70.32"
                referenced_normalized = {r.replace(" ", "") for r in referenced}
                index_lines = []
                for sec_num, page in merged.items():
                    if sec_num in referenced_normalized:
                        index_lines.append(f"- § {sec_num} → page {page}")
                if index_lines:
                    parts.append(f"### Statute Chapter {chapter} — Section Page Index")
                    parts.append(
                        "(Link directly with `doc:statutes-"
                        + chapter
                        + "#page=N`; subsections use the parent section's page)"
                    )
                    parts.extend(index_lines)
                    parts.append("")
            except Exception:
                pass

    parts.append(
        f"\n## Documents to Cite\nYou MUST cite these document IDs: {sorted(cited_doc_ids)}"
    )

    return "\n".join(parts)


def stream_answer(
    ws_server: WebSocketServer,
    query_id: str,
    answer_context: str,
    trace_seq,
    ws_connection_alive: list[bool],
    persona: str | None = None,
) -> str:
    """Phase B: Stream the answer token-by-token via converse_stream().

    Returns the full accumulated answer text.
    """
    _emit(
        ws_server,
        trace_seq,
        query_id=query_id,
        kind="phase",
        payload={"phase": "answer_streaming"},
    )

    # Send answer-event: start
    start_msg = AnswerEventType(event="start", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": start_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

    # Start heartbeat for the streaming phase
    heartbeat_stop = start_heartbeat(
        ws_server, ws_connection_alive, label="answer stream heartbeat"
    )

    # Start streaming answer with NO tools — pure text output
    stream_started = time.perf_counter()
    try:
        stream_response = converse_stream_with_cache(
            bedrock,
            model_id=AGENTIC_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": answer_context}]}],
            system=[{"text": apply_persona(ANSWER_STREAM_SYSTEM_PROMPT, persona)}],
            inference_config={"maxTokens": 4096, "temperature": 0.0},
        )
    except Exception as exc:
        heartbeat_stop.set()
        _log(
            "answer_stream_error",
            logging.ERROR,
            query_id=query_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    # Stream text deltas to WebSocket
    answer_text = ""
    fragment_buffer = ""
    _FRAGMENT_MIN_SIZE = 30  # batch small deltas to avoid excessive WS calls

    event_stream = stream_response.get("stream")
    if event_stream:
        for event in event_stream:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                text_chunk = delta.get("text", "")
                if text_chunk:
                    answer_text += text_chunk
                    fragment_buffer += text_chunk

                    if len(fragment_buffer) >= _FRAGMENT_MIN_SIZE:
                        if ws_connection_alive[0]:
                            frag_msg = FragmentMessage(
                                query_id=query_id,
                                content=FragmentContent(fragment=fragment_buffer),
                            )
                            try:
                                ws_server.client.post_to_connection(
                                    ConnectionId=ws_server.connection_id,
                                    Data=json.dumps(
                                        {
                                            "streamId": "answer",
                                            "body": frag_msg.model_dump(by_alias=True),
                                        }
                                    ),
                                )
                            except Exception:
                                ws_connection_alive[0] = False
                        fragment_buffer = ""

            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                _log(
                    "answer_stream_usage",
                    usage=usage,
                    query_id=query_id,
                )

    heartbeat_stop.set()

    # Flush remaining buffer
    if fragment_buffer and ws_connection_alive[0]:
        frag_msg = FragmentMessage(
            query_id=query_id,
            content=FragmentContent(fragment=fragment_buffer),
        )
        try:
            ws_server.client.post_to_connection(
                ConnectionId=ws_server.connection_id,
                Data=json.dumps({"streamId": "answer", "body": frag_msg.model_dump(by_alias=True)}),
            )
        except Exception:
            ws_connection_alive[0] = False

    # Send answer-event: stop
    stop_msg = AnswerEventType(event="stop", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": stop_msg.model_dump(by_alias=True)})
    if ws_connection_alive[0]:
        try:
            ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)
        except Exception:
            ws_connection_alive[0] = False

    stream_latency = round((time.perf_counter() - stream_started) * 1000)
    _log(
        "answer_stream_complete",
        query_id=query_id,
        answer_chars=len(answer_text),
        stream_latency_ms=stream_latency,
    )

    return answer_text
