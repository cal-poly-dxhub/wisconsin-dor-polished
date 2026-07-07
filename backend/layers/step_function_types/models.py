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


class FeedbackRequest(CamelCaseModel):
    thumb_up: bool
    query_id: str
    feedback: str | None = None


# Event emitted over EventBridge to trigger the agentic retrieval Lambda
class MessageEvent(BaseModel):
    query: str
    query_id: str
    session_id: str
    persona: Persona | None = None


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
