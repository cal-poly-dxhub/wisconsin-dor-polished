from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel_case(string: str) -> str:
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelCaseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel_case, populate_by_name=True)


Persona = Literal["citizen", "government"]


# Request body used to submit a message
class MessageRequest(CamelCaseModel):
    message: str
    persona: Persona | None = None
    # Set when re-sending the original question after the user picks "Continue
    # here" on a topic-shift suggestion. Threaded through to the agentic
    # retrieval Lambda so it skips pre-loop classification. Serialized as
    # `forceProceed` on the wire.
    force_proceed: bool = False


# Structured "rich" feedback captured by the feedback modal. Mirrors the
# frontend FeedbackDraft (minus transient fields). Stored as a nested map on the
# ChatHistoryTable row under `richFeedback`; the scalar `thumbUp` (derived from
# `rating`) remains the canonical filterable/summary field for the admin GSI.
class RichSubsection(CamelCaseModel):
    answer: str | None = None  # 'yes' | 'no'
    comment: str = ""


class RichSourceNote(CamelCaseModel):
    id: str
    source_id: str = ""
    cited_fully: str = ""  # '' | 'yes' | 'no'
    missed_detail: str = ""
    comment: str = ""


class RichAnnotation(CamelCaseModel):
    id: str
    start_offset: int
    end_offset: int
    quote: str
    comment: str = ""


class RichFeedback(CamelCaseModel):
    rating: str | None = None  # 'up' | 'mid' | 'down'
    positive_comment: str = ""
    response: dict[str, RichSubsection] = Field(default_factory=dict)
    sources_ok: str | None = None  # 'yes' | 'no'
    source_notes: list[RichSourceNote] = Field(default_factory=list)
    links_work: str | None = None  # 'yes' | 'no'
    broken_link_ids: list[str] = Field(default_factory=list)
    broken_links_reason: str = ""
    annotations: list[RichAnnotation] = Field(default_factory=list)
    speed_timely: str | None = None  # 'yes' | 'no'
    speed_comment: str = ""


class FeedbackRequest(CamelCaseModel):
    thumb_up: bool
    query_id: str
    feedback: str | None = None
    rich_feedback: RichFeedback | None = None


# Event emitted over EventBridge to trigger the agentic retrieval Lambda
class MessageEvent(BaseModel):
    query: str
    query_id: str
    session_id: str
    persona: Persona | None = None
    # Carries the frontend's "Continue here" choice through to the Lambda's
    # UserQuery so pre-loop classification is skipped for that turn.
    force_proceed: bool = False


class ErrorBody(BaseModel):
    message: str


class MessageProcessingErrorResponse(BaseModel):
    error: ErrorBody


# Input to the agentic retrieval Lambda via EventBridge
class UserQuery(BaseModel):
    query: str
    query_id: str
    session_id: str
    persona: Persona | None = None
    # Set by the frontend when the user picks "Continue here" on a topic-shift
    # suggestion: re-sends the original question but tells the Lambda to skip
    # pre-loop classification and run the agentic loop directly. Deterministic —
    # no re-classification, so the suggestion can't loop on itself.
    force_proceed: bool = False


class FAQ(BaseModel):
    faq_id: str
    question: str
    answer: str
    # Public revenue.wi.gov source page for this FAQ, resolved at query time
    # from the FAQ-URL table. None when no URL could be matched (no link shown).
    source_url: str | None = Field(default=None)


# Types of resources used in generating responses
class FAQResource(BaseModel):
    faqs: list[FAQ]


class ChunkSnippet(BaseModel):
    page: int
    text: str


class RAGDocument(BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = Field(default=None)
    source_url: str | None = Field(default=None)
    discovery_tag: str = Field(default="unknown")
    # Optional: 1=Constitution, 2=Statute, 3=CaseLaw, 4=AdminRule, 5=WPAM,
    # 6=FAQ, 7=GovPub, 8=IAAO, 9=USPAP. Drives the AuthorityBadge color in
    # the frontend. Stored on every Document node in Neptune; populated by
    # _build_rag_documents / _build_opinion_card so non-FAQ cards render
    # with their authority pill (FAQs hard-code level 6 client-side).
    authority_level: int | None = Field(default=None)
    # Stable S3 reference for the raw document. Citation links use the
    # public source_url; s3_key is kept for provenance/debugging only.
    s3_key: str | None = Field(default=None)
    start_page: int | None = Field(default=None)
    end_page: int | None = Field(default=None)
    # WPAM edition year (e.g., 2025). Set on chunks from the Wisconsin
    # Property Assessment Manual; null on all other doc types.
    edition_year: int | None = Field(default=None)
    # Per-page chunk snippets for citation link previews.
    chunks: list[ChunkSnippet] = Field(default_factory=list)
