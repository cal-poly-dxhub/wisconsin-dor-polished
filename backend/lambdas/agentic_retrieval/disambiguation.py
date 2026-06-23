"""Pre-loop disambiguation for generic property assessment queries.

When enabled (ENABLE_DISAMBIGUATION=true), this module classifies incoming
queries before the agentic loop starts. If the query is a generic property
assessment question where the answer depends on property classification,
the Lambda short-circuits and returns a canned clarification question
instead of entering the tool loop.

On the user's follow-up reply, chat history contains the clarification
exchange and the agent proceeds with targeted retrieval.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)

CLASSIFIER_MODEL_ID = os.environ.get(
    "DISAMBIGUATION_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

CLARIFICATION_QUESTION = (
    "To give you the most relevant answer, I need to know what category "
    "best describes your type of property:\n\n"
    "1. **Residential**\n"
    "2. **Commercial**\n"
    "3. **Manufacturing**\n"
    "4. **Agricultural**\n"
    "5. **Undeveloped**\n"
    "6. **Agricultural Forest**\n"
    "7. **Forest Land**\n"
    "8. **Farm Improvements (other)**\n"
    "9. **Not certain** — I am seeking general Wisconsin property "
    "assessment information\n\n"
    "Please select the option that best fits your situation, or describe "
    "your property type."
)

_CLASSIFIER_PROMPT = """\
You are a query classifier for the Wisconsin Department of Revenue property tax chatbot.

Determine whether the user's question is a GENERIC property assessment or tax question where the answer would differ materially depending on the property classification (residential, commercial, manufacturing, agricultural, etc.).

Answer "DISAMBIGUATE" ONLY when ALL of these are true:
1. The question is about property assessment, valuation, taxation, exemptions, or any other property tax topic
2. The question does NOT specify a property type or classification
3. The answer would be materially different for different property types (e.g., different statutes, different manuals, different procedures, different exemption rules apply)

Answer "PROCEED" when ANY of these are true:
- The question names a specific property type (residential, manufacturing, agricultural, etc.)
- The question is about a topic that has the same answer regardless of property type (e.g., Board of Review procedures, assessment dates, general rights)
- The question is about an ownership category or exemption class (Native American/tribal, religious/church, government, nonprofit, veteran) — these depend on ownership or legal status, not property classification
- The question references a specific statute, form, or document
- The question is not about property assessment at all (out of scope)
- The question is a follow-up to a previous conversation

Respond with ONLY "DISAMBIGUATE" or "PROCEED" — nothing else."""


def should_disambiguate(query: str, chat_history: list[dict]) -> bool:
    """Return True if the query should trigger the canned clarification.

    Skips disambiguation when:
    - Chat history exists (user already in a conversation — context available)
    - The query itself mentions a property type
    """
    if chat_history:
        return False

    q = query.lower()
    type_keywords = [
        "manufacturing", "agricultural", "agriculture", "farmland",
        "farm land", "residential", "commercial", "personal property",
        "forest land", "undeveloped",
    ]
    ownership_keywords = [
        "native american", "tribal", "indian", "reservation",
        "trust land", "church", "religious", "nonprofit", "non-profit",
        "government", "municipal", "county-owned", "state-owned",
        "federal", "exempt organization", "veteran",
    ]
    if any(kw in q for kw in type_keywords):
        return False
    if any(kw in q for kw in ownership_keywords):
        return False

    try:
        response = _bedrock.converse(
            modelId=CLASSIFIER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": query}]}],
            system=[{"text": _CLASSIFIER_PROMPT}],
            inferenceConfig={"maxTokens": 10, "temperature": 0.0},
        )
        output = response["output"]["message"]["content"][0]["text"].strip()
        result = "DISAMBIGUATE" in output.upper()
        logger.info(
            f"Disambiguation classifier: query='{query[:80]}' → {output} "
            f"(result={'disambiguate' if result else 'proceed'})"
        )
        return result
    except Exception:  # noqa: BLE001
        logger.warning(
            "Disambiguation classifier failed; proceeding with agentic loop",
            exc_info=True,
        )
        return False
