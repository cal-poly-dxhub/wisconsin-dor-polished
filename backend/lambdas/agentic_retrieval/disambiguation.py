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

_CLASSIFIER_PROMPT = """\
You are a query classifier for the Wisconsin Department of Revenue property tax chatbot.

Classify the user's question into exactly ONE of three categories.

OUT_OF_SCOPE — The question is NOT about Wisconsin property tax or any related Wisconsin Department of Revenue State & Local Finance topic. Examples: general knowledge, science, weather, math, coding, current events, other states' or federal income taxes, Wisconsin income/sales/excise tax matters unrelated to local government finance, personal or legal advice unrelated to property tax, or casual chit-chat.

The scope is BROAD. In addition to property assessment and taxation, the following Wisconsin DOR State & Local Finance topics are all IN SCOPE — never classify these as OUT_OF_SCOPE:
- Shared revenue and state aid to local governments: county and municipal aid (CMA), supplemental county and municipal aid (SCMA), expenditure restraint program, personal property aid, exempt computer aid (Chapter 79 programs)
- Levy limits and the levy limit worksheets
- Tax incremental financing (TIF/TID): base value, increment, net new construction
- Innovation grants and innovation planning grants (including fair market compensation for volunteer firefighters/EMS), and other grant programs DOR administers for local governments
- Equalized values, the statement of changes, and apportionment
- Local government financial reporting and forms administered by DOR's SLF division
These may not "look" like property tax (they can resemble grants, employment compensation, or income/sales tax), but they ARE core DOR State & Local Finance topics. When in doubt about a local-government-finance question, PROCEED.

DISAMBIGUATE — The question IS about Wisconsin property assessment or taxation, but does NOT specify a property type or classification, AND the answer would differ materially depending on the property classification (residential, commercial, manufacturing, agricultural, etc.) — e.g., different statutes, manuals, procedures, or exemption rules apply.

PROCEED — Any other in-scope question. Answer PROCEED when ANY of these are true:
- The question names a specific property type (residential, manufacturing, agricultural, etc.)
- The topic has the same answer regardless of property type (e.g., Board of Review procedures, assessment dates, general rights)
- The question is about an ownership category or exemption class (Native American/tribal, religious/church, government, nonprofit, veteran) — these depend on ownership or legal status, not property classification
- The question is about any DOR State & Local Finance program listed above (shared revenue, CMA/SCMA, levy limits, TIF/TID, innovation grants, equalized values, aid calculations)
- The question references a specific statute, form, or document
- The question is a follow-up to a previous conversation

Decision order:
1. If the question is not about Wisconsin property tax at all → OUT_OF_SCOPE.
2. Otherwise, if it needs a property type to answer well → DISAMBIGUATE.
3. Otherwise → PROCEED.

Respond with ONLY one word: OUT_OF_SCOPE, DISAMBIGUATE, or PROCEED — nothing else."""


def classify_query(query: str, chat_history: list[dict]) -> str:
    """Classify a query as OUT_OF_SCOPE, DISAMBIGUATE, or PROCEED.

    Fail-open: any error (or a mid-conversation follow-up) returns PROCEED so
    the agentic loop still runs. Local keyword checks short-circuit to PROCEED
    when the query already names a property type or ownership class — those are
    unambiguously in scope and specific, so no LLM call is needed.
    """
    # Never short-circuit a follow-up: chat history carries the context the
    # agent needs, and the classifier can't see whether the prior turn was
    # already in scope.
    if chat_history:
        return VERDICT_PROCEED

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

    try:
        response = _bedrock.converse(
            modelId=CLASSIFIER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": query}]}],
            system=[{"text": _CLASSIFIER_PROMPT}],
            inferenceConfig={"maxTokens": 16, "temperature": 0.0},
        )
        output = response["output"]["message"]["content"][0]["text"].strip().upper()
        # Check OUT_OF_SCOPE first — it is the most specific and must win over a
        # stray substring match. Anything unrecognized falls through to PROCEED.
        if "OUT_OF_SCOPE" in output or "OUT OF SCOPE" in output:
            verdict = VERDICT_OUT_OF_SCOPE
        elif "DISAMBIGUATE" in output:
            verdict = VERDICT_DISAMBIGUATE
        else:
            verdict = VERDICT_PROCEED
        logger.info(f"Query classifier: query='{query[:80]}' → raw='{output}' verdict={verdict}")
        return verdict
    except Exception:  # noqa: BLE001
        logger.warning(
            "Query classifier failed; proceeding with agentic loop",
            exc_info=True,
        )
        return VERDICT_PROCEED
