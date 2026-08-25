"""Pre-loop query classification for the property assessment chatbot.

When enabled (ENABLE_DISAMBIGUATION=true), this module classifies incoming
queries before the agentic loop starts and returns one of three verdicts:

- OUT_OF_SCOPE — the question is not about Wisconsin property tax at all.
  The Lambda short-circuits with a canned refusal and NO retrieval.
- DISAMBIGUATE — a generic property assessment question whose answer depends
  on property classification. The Lambda short-circuits with a canned
  clarification question + property-type choices.
- PROCEED — an in-scope, answerable question. The agentic loop runs normally.

On the user's follow-up reply, chat history contains the clarification
exchange and the agent proceeds with targeted retrieval.
"""

import logging
import os

import boto3

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)

CLASSIFIER_MODEL_ID = os.environ.get(
    "DISAMBIGUATION_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

# Verdicts returned by classify_query().
VERDICT_OUT_OF_SCOPE = "OUT_OF_SCOPE"
VERDICT_DISAMBIGUATE = "DISAMBIGUATE"
VERDICT_TOPIC_SHIFT = "TOPIC_SHIFT"
VERDICT_PROCEED = "PROCEED"

PROPERTY_TYPE_CHOICES = [
    "Residential",
    "Commercial",
    "Manufacturing",
    "Agricultural",
    "Undeveloped",
    "Agricultural Forest",
    "Forest Land",
    "Farm Improvements (other)",
    "Not certain — general information",
]

CLARIFICATION_QUESTION = (
    "To give you the most relevant answer, I need to know what category "
    "best describes your type of property. "
    "Please select an option below, or describe your property type."
)

OUT_OF_SCOPE_MESSAGE = (
    "That question is outside the scope of what I can help with. I'm the "
    "**Wisconsin Department of Revenue property tax assistant** — my knowledge "
    "is limited to Wisconsin property assessment, taxation, statutes, "
    "administrative rules, exemptions, and related procedures.\n\n"
    "If you have a question about **Wisconsin property taxes** — how property "
    "is assessed, how to appeal an assessment, exemptions and credits, or "
    "agricultural, residential, commercial, and manufacturing valuation — "
    "I'm happy to help! 😊"
)

TOPIC_SHIFT_SUGGESTION = (
    "It looks like you're asking about a new topic. Starting a fresh chat can "
    "keep answers focused and accurate — or you can continue right here.\n\n"
    "What would you like to do?"
)

# Classifier system prompt. Externalized to DynamoDB (config id
# "disambiguationClassifier") and loaded via prompt.py, mirroring the
# agenticRetrieval / answerStream prompts; the bundled copy in _prompt_fallback
# is the offline fallback. The prompt always describes all four verdicts —
# whether TOPIC_SHIFT is acted on is gated by ENABLE_TOPIC_SHIFT in the handler.
from prompt import DISAMBIGUATION_CLASSIFIER_PROMPT as _CLASSIFIER_PROMPT  # noqa: E402

# Cap per-answer length when building the classifier's history context. The
# classifier only needs the topic/scope of prior turns, not the full sourced
# answer, so truncating keeps the (cheap) Haiku call small and predictable.
_CLASSIFIER_HISTORY_ANSWER_CHARS = 600
_CLASSIFIER_HISTORY_MAX_TURNS = 5


def _format_history_for_classifier(chat_history: list[dict]) -> str:
    """Render recent turns as compact text for the classifier's user message.

    Truncates each prior answer — the classifier needs conversational scope
    (was a property type established?), not the full answer text.
    """
    turns = chat_history[-_CLASSIFIER_HISTORY_MAX_TURNS:]
    lines = []
    for idx, turn in enumerate(turns, start=1):
        answer = (turn.get("answer") or "").strip()
        if len(answer) > _CLASSIFIER_HISTORY_ANSWER_CHARS:
            answer = answer[:_CLASSIFIER_HISTORY_ANSWER_CHARS] + "…"
        lines.append(f"Turn {idx}\nUser: {turn.get('query', '')}\nAssistant: {answer}")
    return "\n\n".join(lines)


def classify_query(
    query: str, chat_history: list[dict], allow_topic_shift: bool = False
) -> str:
    """Classify a query as OUT_OF_SCOPE, DISAMBIGUATE, TOPIC_SHIFT, or PROCEED.

    Fail-open: any error returns PROCEED so the agentic loop still runs. Local
    keyword checks short-circuit to PROCEED when the query already names a
    property type or ownership class — those are unambiguously in scope and
    specific, so no LLM call is needed.

    Follow-ups ARE classified (with prior turns passed to the model), so a
    generic new topic raised mid-conversation is still disambiguated, while a
    drill-down on an already-established property type proceeds. The model is
    told to treat an established property type as PROCEED.

    ``allow_topic_shift`` gates the TOPIC_SHIFT verdict (feature flag): when
    False, a topic-shift reply is downgraded to PROCEED so the loop still runs
    normally. TOPIC_SHIFT is only meaningful mid-conversation, so it is never
    returned when there is no chat history.
    """
    chat_history = chat_history or []
    q = query.lower()
    type_keywords = [
        "manufacturing",
        "agricultural",
        "agriculture",
        "farmland",
        "farm land",
        "residential",
        "commercial",
        "personal property",
        "forest land",
        "undeveloped",
    ]
    ownership_keywords = [
        "native american",
        "tribal",
        "indian",
        "reservation",
        "trust land",
        "church",
        "religious",
        "nonprofit",
        "non-profit",
        "government",
        "municipal",
        "county-owned",
        "state-owned",
        "federal",
        "exempt organization",
        "veteran",
    ]
    if any(kw in q for kw in type_keywords):
        return VERDICT_PROCEED
    if any(kw in q for kw in ownership_keywords):
        return VERDICT_PROCEED

    if chat_history:
        user_text = (
            f"PRIOR CONVERSATION:\n{_format_history_for_classifier(chat_history)}\n\n"
            f"CURRENT QUESTION: {query}"
        )
    else:
        user_text = query

    try:
        response = _bedrock.converse(
            modelId=CLASSIFIER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            system=[{"text": _CLASSIFIER_PROMPT}],
            inferenceConfig={"maxTokens": 16, "temperature": 0.0},
        )
        output = response["output"]["message"]["content"][0]["text"].strip().upper()
        # Check OUT_OF_SCOPE first — it is the most specific and must win over a
        # stray substring match. TOPIC_SHIFT before DISAMBIGUATE for the same
        # reason. Anything unrecognized falls through to PROCEED.
        if "OUT_OF_SCOPE" in output or "OUT OF SCOPE" in output:
            verdict = VERDICT_OUT_OF_SCOPE
        elif "TOPIC_SHIFT" in output or "TOPIC SHIFT" in output:
            verdict = VERDICT_TOPIC_SHIFT
        elif "DISAMBIGUATE" in output:
            verdict = VERDICT_DISAMBIGUATE
        else:
            verdict = VERDICT_PROCEED
        # Downgrade TOPIC_SHIFT to PROCEED unless the feature is enabled AND we
        # are mid-conversation — a topic shift is meaningless on turn one.
        if verdict == VERDICT_TOPIC_SHIFT and not (allow_topic_shift and chat_history):
            verdict = VERDICT_PROCEED
        logger.info(
            f"Query classifier: query='{query[:80]}' history_turns={len(chat_history)} "
            f"allow_topic_shift={allow_topic_shift} → raw='{output}' verdict={verdict}"
        )
        return verdict
    except Exception:  # noqa: BLE001
        logger.warning(
            "Query classifier failed; proceeding with agentic loop",
            exc_info=True,
        )
        return VERDICT_PROCEED
