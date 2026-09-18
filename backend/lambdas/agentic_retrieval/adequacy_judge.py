"""Post-retrieval adequacy judge for the property assessment chatbot.

The pre-loop classifier (``disambiguation.classify_query``) decides whether to
retrieve at all, from the question text alone. That is the wrong place to make
the call: a question like "what is the Markarian case" carries no topic words,
so the classifier refuses it OUT_OF_SCOPE even though the corpus holds the
case. The classifier cannot see the corpus.

The adequacy judge runs in the opposite order. Every query runs the Phase A
research loop first. Then — before Phase B streams a word — the judge reads the
question, the conversation, the agent's answer plan, and the *text of the cited
chunks*, and records one short structured ``Finding``:

- ``ANSWER``   — the plan rests on the retrieved material (possibly with gaps
                 named in ``unsupported``). An honest "this is not in my
                 materials" plan is an ANSWER, not a failure.
- ``CLARIFY``  — the answer turns on a fact the user did not give AND the
                 sources genuinely diverge on it. The clarification (axis,
                 question, options) is derived FROM THE SOURCES, never from a
                 fixed menu.
- ``DECLINE``  — nothing retrieved bears on the question at all. This is the
                 only path to a refusal.

The finding is injected into the Phase B context (see
``loop.phase_b.build_answer_context``); Phase B always streams, so there is a
single prose path and the judge never writes user-facing text.

Fail-open: any error yields ``Finding(verdict="ANSWER", ...)`` so Phase B runs
exactly as it does today.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from prompt import ADEQUACY_JUDGE_PROMPT
from streaming.bedrock import converse_inference_kwargs

import config

logger = logging.getLogger(__name__)

# Haiku 4.5 by default: the judge reads a bounded slice of already-retrieved
# text and emits a handful of short fields, so a small model is the right
# trade. Override per-environment with ADEQUACY_JUDGE_MODEL_ID.
MAX_CLARIFICATION_OPTIONS = 6  # 5 real alternatives + the escape option
JUDGE_MODEL_ID = os.environ.get(
    "ADEQUACY_JUDGE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

VERDICT_ANSWER = "ANSWER"
VERDICT_CLARIFY = "CLARIFY"
VERDICT_DECLINE = "DECLINE"
_VERDICTS = (VERDICT_ANSWER, VERDICT_CLARIFY, VERDICT_DECLINE)

# Context budget. The judge MUST see evidence — that is the whole point — but
# a full cited set can run to hundreds of thousands of characters. Chunks are
# truncated individually and then the whole evidence section is capped.
MAX_CONTEXT_CHARS = int(os.environ.get("ADEQUACY_JUDGE_MAX_CONTEXT_CHARS", "40000"))
_MAX_CHUNK_CHARS = 2400
_HISTORY_MAX_TURNS = 3
_HISTORY_ANSWER_CHARS = 600
_MAX_PLAN_CHARS = 4000
_JUDGE_MAX_TOKENS = 1024

_TOOL_NAME = "record_finding"


@dataclass
class Clarification:
    """A question to put to the user, derived from the retrieved sources.

    ``options`` are the concrete alternatives the sources distinguish, with a
    natural escape hatch ("Answer in general terms") as the LAST entry — the
    resolver in ``resolve_pending_clarification`` treats the final option as
    the escape.
    """

    axis: str
    question: str
    options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"axis": self.axis, "question": self.question, "options": list(self.options)}


@dataclass
class Finding:
    """The judge's verdict on one answer plan. Never shown to the user."""

    verdict: Literal["ANSWER", "CLARIFY", "DECLINE"]
    supported: str
    unsupported: str
    clarification: Clarification | None = None
    rationale: str = ""
    model_id: str = ""
    latency_ms: int = 0


def _fail_open(reason: str = "judge unavailable") -> Finding:
    return Finding(
        verdict=VERDICT_ANSWER,
        supported="",
        unsupported="",
        clarification=None,
        rationale=reason,
    )


# ── Bedrock structured output ───────────────────────────────────────────────
# A tool schema is used instead of "reply with JSON" so the model cannot wrap
# the payload in prose. The clarification fields are flat (not a nested object)
# because small models fill flat schemas far more reliably.
FINDING_TOOL_CONFIG: dict[str, Any] = {
    "tools": [
        {
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": (
                    "Record the adequacy finding for this answer plan. Call exactly once."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "verdict": {
                                "type": "string",
                                "enum": list(_VERDICTS),
                                "description": (
                                    "ANSWER if the plan can be written from the cited "
                                    "material (gaps go in unsupported); CLARIFY if one "
                                    "missing fact genuinely forks the answer; DECLINE only "
                                    "if nothing retrieved bears on the question."
                                ),
                            },
                            "supported": {
                                "type": "string",
                                "description": (
                                    "1-3 sentences: what the cited material actually "
                                    "establishes about this question. On DECLINE, what the "
                                    "assistant does cover instead."
                                ),
                            },
                            "unsupported": {
                                "type": "string",
                                "description": (
                                    "Claims in the plan you cannot trace to a cited chunk, "
                                    "named concretely. Empty string if every claim traces."
                                ),
                            },
                            "rationale": {
                                "type": "string",
                                "description": "One sentence explaining the verdict.",
                            },
                            "clarification_axis": {
                                "type": "string",
                                "description": (
                                    "CLARIFY only. Short noun phrase for the missing fact, "
                                    "e.g. 'property classification' or 'assessment year'."
                                ),
                            },
                            "clarification_question": {
                                "type": "string",
                                "description": (
                                    "CLARIFY only. One natural sentence asking the user for "
                                    "that fact."
                                ),
                            },
                            "clarification_options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "CLARIFY only. The concrete alternatives the cited "
                                    "sources distinguish, with a natural escape option "
                                    "('Answer in general terms') LAST."
                                ),
                            },
                        },
                        "required": ["verdict", "supported", "unsupported", "rationale"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": _TOOL_NAME}},
}


# ── Context assembly ────────────────────────────────────────────────────────


def _format_history(chat_history: list[dict]) -> str:
    """Render recent turns, flagging any clarification the assistant asked.

    The flag matters for rule 4 of the judge prompt: if the previous turn asked
    a clarifying question and this message answers it, the judge must not ask
    again.
    """
    lines: list[str] = []
    for idx, turn in enumerate(chat_history[-_HISTORY_MAX_TURNS:], start=1):
        answer = (turn.get("answer") or "").strip()
        if len(answer) > _HISTORY_ANSWER_CHARS:
            answer = answer[:_HISTORY_ANSWER_CHARS] + "…"
        lines.append(f"Turn {idx}\nUser: {turn.get('query', '')}\nAssistant: {answer}")
        pending = turn.get("clarification")
        if isinstance(pending, dict):
            options = ", ".join(str(o) for o in (pending.get("options") or []))
            lines.append(
                "  [On this turn the assistant asked the user a clarifying question about "
                f"{pending.get('axis', 'a missing fact')}: "
                f"{pending.get('question', '')} Options offered: {options}]"
            )
    return "\n\n".join(lines)


def _chunk_header(chunk: dict, discovery: dict[str, str]) -> str:
    doc_id = chunk.get("doc_id", "unknown")
    bits = [f"doc_id={doc_id}"]
    heading = (chunk.get("heading") or "").strip()
    if heading:
        bits.append(f"heading={heading}")
    page = chunk.get("start_page")
    if page:
        end_page = chunk.get("end_page")
        same = not end_page or end_page == page
        bits.append(f"page={page}" if same else f"pages={page}-{end_page}")
    if chunk.get("_judge_cited") is True:
        bits.append("CITED BY THE PLAN")
    elif chunk.get("_judge_cited") is False:
        bits.append("retrieved, not cited")
    tag = discovery.get(doc_id)
    if tag:
        bits.append(f"found_via={tag}")
    return "--- " + " | ".join(bits)


def _format_evidence(cited_chunks: list[dict], discovery: dict[str, str], budget: int) -> str:
    """Render cited chunks (heading + doc_id + page + text) within ``budget``.

    Chunks are emitted document by document so the judge sees each source whole
    rather than an interleaved sample, and the budget is spent in order — an
    over-long cited set loses its tail, never its head.
    """
    if not cited_chunks:
        return (
            "(No chunks were cited. The research loop retrieved nothing it was "
            "willing to stand behind.)"
        )

    # Group by document in FIRST-SEEN order: callers put cited documents first,
    # then everything else retrieved, so the budget favours what the plan
    # stands on while still exposing the branches it did not pick.
    by_doc: dict[str, list[dict]] = {}
    for chunk in cited_chunks:
        by_doc.setdefault(chunk.get("doc_id", "unknown"), []).append(chunk)

    parts: list[str] = []
    used = 0
    dropped = 0
    for doc_id in by_doc:
        for chunk in by_doc[doc_id]:
            text = (chunk.get("text") or "").strip()
            if len(text) > _MAX_CHUNK_CHARS:
                text = text[:_MAX_CHUNK_CHARS] + "…"
            block = f"{_chunk_header(chunk, discovery)}\n{text}"
            if used + len(block) > budget:
                dropped += 1
                continue
            parts.append(block)
            used += len(block) + 1
    if dropped:
        parts.append(f"(… {dropped} further cited chunk(s) omitted for length.)")
    return "\n".join(parts)


def build_judge_context(
    query: str,
    chat_history: list[dict],
    answer_plan: str,
    cited_chunks: list[dict],
    discovery: dict[str, str],
) -> str:
    """Assemble the judge's user message: conversation, question, plan, evidence."""
    parts: list[str] = []
    if chat_history:
        parts.append(f"PRIOR CONVERSATION:\n{_format_history(chat_history)}\n")

    parts.append(f"USER QUESTION:\n{query}\n")

    plan = (answer_plan or "").strip() or "(The research loop produced no answer plan.)"
    if len(plan) > _MAX_PLAN_CHARS:
        plan = plan[:_MAX_PLAN_CHARS] + "…"
    parts.append(f"ANSWER PLAN (internal; drafted by the research agent):\n{plan}\n")

    head = "\n".join(parts)
    budget = max(2000, MAX_CONTEXT_CHARS - len(head) - 200)
    doc_count = len({c.get("doc_id") for c in cited_chunks})
    evidence = _format_evidence(cited_chunks, discovery or {}, budget)
    parts.append(
        f"CITED MATERIAL ({len(cited_chunks)} chunk(s) from {doc_count} document(s)):\n{evidence}"
    )
    return "\n".join(parts)


# ── Response parsing ────────────────────────────────────────────────────────


def _tool_input_from_response(response: dict) -> dict[str, Any]:
    """Pull the record_finding tool input out of a Converse response."""
    content = response.get("output", {}).get("message", {}).get("content", []) or []
    for block in content:
        tool_use = block.get("toolUse") if isinstance(block, dict) else None
        if tool_use and tool_use.get("name") == _TOOL_NAME:
            payload = tool_use.get("input")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                return payload
    # Some models emit the JSON as plain text when toolChoice is ignored.
    for block in content:
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            try:
                payload = json.loads(text.strip())
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
    raise ValueError("no record_finding tool use in judge response")


def parse_finding(payload: dict[str, Any]) -> Finding:
    """Build a Finding from the tool payload, normalizing anything sloppy.

    An unrecognized verdict degrades to ANSWER rather than refusing: the judge
    must never be the reason a real question goes unanswered. A CLARIFY with no
    usable options likewise degrades to ANSWER — chips with nothing in them are
    worse than no chips.
    """
    verdict = str(payload.get("verdict", "")).strip().upper()
    if verdict not in _VERDICTS:
        logger.warning("Adequacy judge returned unknown verdict %r; treating as ANSWER", verdict)
        verdict = VERDICT_ANSWER

    clarification: Clarification | None = None
    if verdict == VERDICT_CLARIFY:
        options = [
            str(o).strip() for o in (payload.get("clarification_options") or []) if str(o).strip()
        ]
        question = str(payload.get("clarification_question") or "").strip()
        axis = str(payload.get("clarification_axis") or "").strip()
        if len(options) > MAX_CLARIFICATION_OPTIONS:
            # Keep the escape option (always last) and the first N-1 real ones —
            # a long menu of chips is worse than a short one.
            options = options[: MAX_CLARIFICATION_OPTIONS - 1] + [options[-1]]
        if question and len(options) >= 2:
            clarification = Clarification(axis=axis, question=question, options=options)
        else:
            logger.warning(
                "Adequacy judge returned CLARIFY without a usable question/options; "
                "downgrading to ANSWER"
            )
            verdict = VERDICT_ANSWER

    return Finding(
        verdict=verdict,  # type: ignore[arg-type]
        supported=str(payload.get("supported") or "").strip(),
        unsupported=str(payload.get("unsupported") or "").strip(),
        clarification=clarification,
        rationale=str(payload.get("rationale") or "").strip(),
    )


# ── Public entry point ──────────────────────────────────────────────────────


def judge_answer_plan(
    query: str,
    chat_history: list[dict] | None,
    answer_plan: str,
    cited_chunks: list[dict] | None,
    discovery: dict[str, str] | None,
    *,
    model_id: str | None = None,
    cited_doc_ids: set[str] | None = None,
) -> Finding:
    """Judge one answer plan against the material it cites.

    Args:
        query: the user's question (already resolved against any pending
            clarification — see ``resolve_pending_clarification``).
        chat_history: prior ``{query, answer, clarification?}`` turns.
        answer_plan: Phase A's plan, or the fallback answer text when the loop
            short-circuited without one.
        cited_chunks: the chunks belonging to the cited documents. The judge
            reads their TEXT — it must see evidence, not just doc ids.
        discovery: doc_id → how it was found, used only to label the evidence.
        model_id: override the judge model (defaults to ``JUDGE_MODEL_ID``).

    Returns:
        A ``Finding``. Never raises — any failure returns the fail-open ANSWER
        finding so Phase B streams exactly as it does without the judge.
    """
    started = time.perf_counter()
    resolved_model = model_id or JUDGE_MODEL_ID
    try:
        if cited_doc_ids is not None and cited_chunks:
            cited_chunks = sorted(
                (dict(c, _judge_cited=(c.get("doc_id") in cited_doc_ids)) for c in cited_chunks),
                key=lambda c: not c["_judge_cited"],
            )
        context = build_judge_context(
            query,
            chat_history or [],
            answer_plan,
            cited_chunks or [],
            discovery or {},
        )
        response = config.bedrock.converse(
            modelId=resolved_model,
            messages=[{"role": "user", "content": [{"text": context}]}],
            system=[{"text": ADEQUACY_JUDGE_PROMPT}],
            toolConfig=FINDING_TOOL_CONFIG,
            **converse_inference_kwargs(resolved_model, _JUDGE_MAX_TOKENS),
        )
        finding = parse_finding(_tool_input_from_response(response))
        finding.model_id = resolved_model
        finding.latency_ms = round((time.perf_counter() - started) * 1000)
        return finding
    except Exception as exc:  # noqa: BLE001 — the judge must never break a query
        logger.warning(
            "adequacy_judge_failed | model=%s error_type=%s error=%s",
            resolved_model,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        finding = _fail_open()
        finding.model_id = resolved_model
        finding.latency_ms = round((time.perf_counter() - started) * 1000)
        return finding


# ── Phase B injection ───────────────────────────────────────────────────────

FINDING_BLOCK_HEADER = "## RETRIEVAL FINDING"


def render_finding_block(finding: Finding) -> str:
    """Render the finding as the delimited block Phase B receives.

    Kept next to the Finding definition so the block's wording and the
    ``answerStream`` prompt rule that interprets it stay in one place.
    """
    lines = [
        FINDING_BLOCK_HEADER,
        "(An independent check of the answer plan against the material actually cited. "
        "It is internal — never mention it to the user. Where this finding and the answer "
        "plan conflict, the finding wins.)",
        f"Verdict: {finding.verdict}",
    ]
    lines.append(f"Supported by the cited material: {finding.supported or '(not stated)'}")
    lines.append(
        "Not supported by the cited material: "
        + (finding.unsupported or "(none — every claim in the plan traces to a cited chunk)")
    )
    if finding.clarification:
        axis = finding.clarification.axis or "a fact the user did not give"
        lines.append(
            f"Clarification to ask (missing fact — {axis}): {finding.clarification.question}"
        )
        lines.append(
            "Options already offered to the user as buttons: "
            + "; ".join(finding.clarification.options)
        )
    return "\n".join(lines) + "\n"


# ── Deterministic reply resolver ────────────────────────────────────────────

_FREE_TEXT_MAX_WORDS = 12


def resolve_pending_clarification(
    query: str, chat_history: list[dict] | None
) -> tuple[str, dict | None]:
    """Fold a reply to our clarification back into the question that prompted it.

    Keyed on STORED state — the ``clarification`` dict persisted on the prior
    chat-history item — not on a fixed chip list, so it works for whatever
    options the judge derived from the sources that turn.

    Three cases when the previous turn carries a pending clarification:
      a. the message matches one of its options (case-insensitively). The LAST
         option is the escape hatch ("Answer in general terms") and restores the
         original question unannotated; any other option annotates it with the
         axis and the choice.
      b. the message is short free text (≤ 12 words, no "?") — treated as the
         user typing the value instead of clicking a chip.
      c. anything else is a new question and is returned unchanged.

    Returns ``(effective_query, resolution)`` where ``resolution`` is a small
    dict for the trace, or ``None`` when nothing was resolved. The raw user
    message is still what gets persisted as the user's turn.
    """
    if not chat_history:
        return query, None
    pending = chat_history[-1].get("clarification")
    if not isinstance(pending, dict):
        return query, None

    original = str(pending.get("original_query") or chat_history[-1].get("query") or "").strip()
    text = (query or "").strip()
    if not original or not text:
        return query, None

    axis = str(pending.get("axis") or "").strip()
    options = [str(o) for o in (pending.get("options") or [])]

    def _annotate(value: str) -> str:
        return f"{original} ({axis}: {value})" if axis else f"{original} ({value})"

    folded = text.casefold()
    matched_index = next(
        (i for i, opt in enumerate(options) if opt.casefold() == folded),
        None,
    )
    if matched_index is not None:
        is_escape = matched_index == len(options) - 1
        effective = original if is_escape else _annotate(options[matched_index])
        mode = "escape" if is_escape else "option"
        choice = options[matched_index]
    elif len(text.split()) <= _FREE_TEXT_MAX_WORDS and "?" not in text:
        effective = _annotate(text)
        mode = "free_text"
        choice = text
    else:
        return query, None

    resolution = {"mode": mode, "axis": axis, "choice": choice, "original_query": original}
    logger.info(
        "Pending clarification resolved | mode=%s axis=%s choice=%r effective=%r",
        mode,
        axis,
        choice[:60],
        effective[:120],
    )
    return effective, resolution
