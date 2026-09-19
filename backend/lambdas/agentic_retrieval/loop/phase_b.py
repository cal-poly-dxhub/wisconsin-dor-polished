"""Phase B: answer generation and token-by-token streaming.

Builds the answer context from Phase A's cited docs/chunks, then calls
converse_stream() with NO tools — pure text output streamed over WebSocket.
"""

import json
import logging
import re
import time
from typing import TYPE_CHECKING

from graph.neptune_client import NeptuneClient
from prompt import ANSWER_STREAM_SYSTEM_PROMPT, PERSONA_PROMPTS
from streaming.bedrock import converse_stream_with_cache
from tracing.runtime import emit as _emit
from tracing.runtime import log_event as _log
from websocket_utils.models import AnswerEventType, FragmentContent, FragmentMessage
from websocket_utils.utils import WebSocketServer

from config import AGENTIC_MODEL_ID, bedrock

from .heartbeat import start_heartbeat
from .link_repair import KNOWN_STATUTE_CHAPTERS, has_open_link, repair_citation_links

if TYPE_CHECKING:  # pragma: no cover — import only for the type annotation
    from adequacy_judge import Finding

logger = logging.getLogger(__name__)

# Streaming fragments are held back while a `[label](doc:...)` link is still
# open so each link is repaired whole before it is sent; this cap guarantees
# a stray unmatched "[" cannot stall the stream.
_FRAGMENT_HOLD_MAX = 600


# Section listings for statute chapters are static for the life of a container;
# cache them so the page index can be built once for the context and once for
# the link repair without a second round of Neptune queries.
_SECTIONS_CACHE: dict[str, list[dict]] = {}


def _list_sections_cached(neptune_client: NeptuneClient, doc_id: str) -> list[dict]:
    if doc_id not in _SECTIONS_CACHE:
        try:
            _SECTIONS_CACHE[doc_id] = neptune_client.list_document_sections(doc_id) or []
        except Exception:
            return []
    return _SECTIONS_CACHE[doc_id]


def _section_pages_for_chapter(chapter: str, sections: list[dict]) -> dict[str, int]:
    """Map canonical section numbers ("70.32") to their first page.

    Uses the same heading test as the statute chunker (rejects cross
    references like "70.32 (2) (a) 6..." that appear inside other sections),
    with a loose fallback for headings that only start with the number.
    """
    section_pattern = re.compile(rf"^({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)")
    loose_pattern = re.compile(rf"^{re.escape(chapter)}\.\d+")
    canonical: dict[str, int] = {}
    loose: dict[str, int] = {}
    for sec in sections:
        heading = sec.get("heading", "")
        first_page = sec.get("first_page")
        if not heading or first_page is None:
            continue
        m = section_pattern.match(heading)
        if m:
            canonical.setdefault(m.group(1), first_page)
        else:
            m2 = loose_pattern.match(heading)
            if m2:
                loose.setdefault(m2.group(0), first_page)
    return {**loose, **canonical}


def _referenced_sections(chapter: str, blob: str) -> set[str]:
    """Section numbers of `chapter` mentioned anywhere in `blob` ("70. 32" OCR
    spacing normalised)."""
    found = re.findall(rf"{re.escape(chapter)}\.\s*\d+[A-Za-z\-]*", blob)
    return {f.replace(" ", "") for f in found}


def statute_section_pages(
    cited_chunks: list[dict],
    cited_doc_ids: set[str],
    neptune_client: NeptuneClient | None,
    answer_plan: str = "",
) -> dict[str, dict[str, int]]:
    """chapter -> {section -> first page} for every statute chapter the answer
    may cite: chapters in `cited_doc_ids` plus any known corpus chapter whose
    sections are referenced in the cited chunk text or the answer plan.

    Feeds both the "Section Page Index" shown to the writer and the
    deterministic page fill in link_repair, so a `[§ 74.37](doc:statutes-74)`
    written without a page still lands on the right page.
    """
    if not neptune_client:
        return {}
    blob = " ".join(c.get("text", "") for c in cited_chunks) + " " + (answer_plan or "")
    chapters: set[str] = set()
    for doc_id in cited_doc_ids:
        m = re.match(r"^statutes-(\d+)$", doc_id)
        if m:
            chapters.add(m.group(1))
    for chap, _sec in re.findall(r"(\d+)\.\s*(\d+)", blob):
        if chap in KNOWN_STATUTE_CHAPTERS:
            chapters.add(chap)
    out: dict[str, dict[str, int]] = {}
    for chapter in sorted(chapters):
        pages = _section_pages_for_chapter(
            chapter, _list_sections_cached(neptune_client, f"statutes-{chapter}")
        )
        if pages:
            out[chapter] = pages
    return out


def group_chunks_by_doc(chunks: list[dict]) -> dict[str, list[dict]]:
    by_doc: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.get("doc_id", "unknown"), []).append(chunk)
    return by_doc


def finalize_answer_links(
    answer: str,
    query_id: str,
    retrieved_doc_ids: set[str] | None,
    chunks: list[dict] | None,
    section_pages: dict[str, dict[str, int]] | None = None,
) -> str:
    """Repair conflated citation links in a complete answer and log the result.

    Idempotent — safe to run on text that was already repaired fragment-by-
    fragment during streaming (it then logs nothing). Used as the single
    pre-persist pass so the fallback (non-streaming) paths are covered too.
    """
    if not answer or retrieved_doc_ids is None:
        return answer
    repaired, stats = repair_citation_links(
        answer, retrieved_doc_ids, group_chunks_by_doc(chunks or []), section_pages=section_pages
    )
    if stats["repointed"] or stats["stripped"] or stats.get("paged"):
        _log(
            "answer_link_repaired",
            query_id=query_id,
            repointed=stats["repointed"],
            stripped=stats["stripped"],
            paged=stats.get("paged", 0),
            changes=stats["changes"],
            stage="final",
        )
    return repaired


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
    finding: "Finding | None" = None,
) -> str:
    """Build the context message for Phase B answer generation.

    ``finding`` is the adequacy judge's verdict on ``answer_plan`` (see
    ``adequacy_judge``). When present it is rendered as a delimited
    ``## RETRIEVAL FINDING`` block directly after the answer plan, and the
    ``answerStream`` prompt tells the model the finding wins wherever the two
    conflict. When absent the context is byte-for-byte what it was before the
    judge existed.
    """
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

    if finding is not None:
        # Imported lazily so nothing in the judge's import chain loads when the
        # feature is off (ADEQUACY_JUDGE_ENABLED=false leaves finding None).
        from adequacy_judge import render_finding_block

        parts.append(render_finding_block(finding))

    parts.append("## Retrieved Documents and Chunks\n")

    # Group chunks by document
    chunks_by_doc: dict[str, list[dict]] = {}
    for chunk in cited_chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunks_by_doc.setdefault(doc_id, []).append(chunk)

    all_text_blob = " ".join(c.get("text", "") for c in cited_chunks) + " " + (answer_plan or "")
    section_pages = statute_section_pages(cited_chunks, cited_doc_ids, neptune_client, answer_plan)

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

        # For statute docs, include a section->page index so the model can
        # look up correct page numbers for sections not in the retrieved
        # chunks (built once via statute_section_pages, cached per container).
        chapter_match = re.match(r"^statutes-(\d+)$", doc_id)
        if chapter_match and chapter_match.group(1) in section_pages:
            chapter = chapter_match.group(1)
            referenced = _referenced_sections(chapter, all_text_blob)
            index_lines = [
                f"- § {sec_num} -> page {page}"
                for sec_num, page in section_pages[chapter].items()
                if sec_num in referenced
            ]
            if index_lines:
                parts.append(
                    "\n**Section Page Index** (use these page numbers for `#page=N` citations; subsections like 70.32(2)(c)1g use the parent section's page, e.g. § 70.32 -> page 23 means all 70.32(...) subsections start at page 23):"
                )
                parts.extend(index_lines)

        # Include case opinion text if available
        if doc_id in fetched_opinions:
            opinion = fetched_opinions[doc_id]
            parts.append(f"\n**Case Opinion ({opinion.get('citation', '')}):**")
            parts.append(opinion.get("text", "")[:3000])

        parts.append("")

    # Section page indexes for statute chapters referenced in the cited
    # chunks or the plan but not in cited_doc_ids. The agent often learns of
    # a statute from a guide or a case stub without fetching the chapter.
    for chapter, pages in section_pages.items():
        if f"statutes-{chapter}" in cited_doc_ids:
            continue
        referenced = _referenced_sections(chapter, all_text_blob)
        index_lines = [f"- § {s_} -> page {p_}" for s_, p_ in pages.items() if s_ in referenced]
        if index_lines:
            parts.append(f"### Statute Chapter {chapter}: Section Page Index")
            parts.append(
                "(Link directly with `doc:statutes-"
                + chapter
                + "#page=N`; subsections use the parent section's page)"
            )
            parts.extend(index_lines)
            parts.append("")

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
    retrieved_doc_ids: set[str] | None = None,
    cited_chunks: list[dict] | None = None,
    section_pages: dict[str, dict[str, int]] | None = None,
) -> str:
    """Phase B: Stream the answer token-by-token via converse_stream().

    When `retrieved_doc_ids` is given, every fragment is passed through
    repair_citation_links() before it is sent, and fragments are held back
    while a markdown link is still open so a link is never split across two
    fragments. The streamed text and the returned (persisted) text are
    therefore identical and both repaired.

    Returns the full accumulated answer text.
    """
    repair_enabled = retrieved_doc_ids is not None
    chunks_by_doc = group_chunks_by_doc(cited_chunks or []) if repair_enabled else {}
    repair_totals = {"repointed": 0, "stripped": 0}
    repair_changes: list[dict] = []

    def _prepare_fragment(raw: str) -> str:
        if not repair_enabled:
            return raw
        fixed, stats = repair_citation_links(
            raw, retrieved_doc_ids, chunks_by_doc, section_pages=section_pages
        )
        repair_totals["repointed"] += stats["repointed"]
        repair_totals["stripped"] += stats["stripped"]
        repair_totals["paged"] = repair_totals.get("paged", 0) + stats.get("paged", 0)
        repair_changes.extend(stats["changes"])
        return fixed

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
                    fragment_buffer += text_chunk

                    ready = len(fragment_buffer) >= _FRAGMENT_MIN_SIZE and (
                        not repair_enabled
                        or not has_open_link(fragment_buffer)
                        or len(fragment_buffer) >= _FRAGMENT_HOLD_MAX
                    )
                    if ready:
                        fragment_buffer = _prepare_fragment(fragment_buffer)
                        answer_text += fragment_buffer
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
    if fragment_buffer:
        fragment_buffer = _prepare_fragment(fragment_buffer)
        answer_text += fragment_buffer
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
    if repair_totals["repointed"] or repair_totals["stripped"] or repair_totals.get("paged"):
        _log(
            "answer_link_repaired",
            query_id=query_id,
            repointed=repair_totals["repointed"],
            stripped=repair_totals["stripped"],
            paged=repair_totals.get("paged", 0),
            changes=repair_changes,
            stage="stream",
        )
    _log(
        "answer_stream_complete",
        query_id=query_id,
        answer_chars=len(answer_text),
        stream_latency_ms=stream_latency,
    )

    return answer_text
