from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel_case(string: str) -> str:
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelCaseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel_case, populate_by_name=True)


# Request body used to submit a message
class MessageRequest(CamelCaseModel):
    message: str


class FeedbackRequest(CamelCaseModel):
    thumb_up: bool
    query_id: str
    feedback: str | None = None


# Event emitted over EventBridge to trigger step function
class MessageEvent(BaseModel):
    query: str
    query_id: str
    session_id: str


class ErrorBody(BaseModel):
    message: str


class MessageProcessingErrorResponse(BaseModel):
    error: ErrorBody


# Possible inputs to the step function via EventBridge
class UserQuery(BaseModel):
    query: str
    query_id: str
    session_id: str


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
    # Stable S3 reference resolved at click time by the citation_resolver
    # Lambda. Replaces eager presigned URLs so restored sessions stay
    # clickable indefinitely while a copied URL still expires in 15 min.
    s3_key: str | None = Field(default=None)
    start_page: int | None = Field(default=None)
    end_page: int | None = Field(default=None)
    # WPAM edition year (e.g., 2025). Set on chunks from the Wisconsin
    # Property Assessment Manual; null on all other doc types.
    edition_year: int | None = Field(default=None)
    # Per-page chunk snippets for citation link previews.
    chunks: list[ChunkSnippet] = Field(default_factory=list)


class DocumentResource(BaseModel):
    documents: list[RAGDocument] = Field(default_factory=list)


# Job passed to the response generation lambda. Optionally
# include frequently asked questions and RAG documents in
# independent fields.
class GenerateResponseJob(BaseModel):
    query: str
    query_id: str
    session_id: str
    faqs: FAQResource | None = Field(default=None)
    documents: DocumentResource | None = Field(default=None)


# Job passed to the retrieval lambda. Can take FAQs to provide
# context for RAG retrieval.
class RetrieveJob(BaseModel):
    query: str
    query_id: str
    faqs: FAQResource | None = Field(default=None)
    session_id: str


# Job passed to the document streaming lambda
class StreamResourcesJob(BaseModel):
    query_id: str
    session_id: str
    faqs: FAQResource | None = Field(default=None)
    documents: DocumentResource | None = Field(default=None)


# Either a response generation job with a streaming job for FAQs or
# a plan retrieval job for queries classified as RAG. Differentiate
# based on the query_class field.
class ClassifierResult(BaseModel):
    successful: bool
    faqs: FAQResource | None = Field(default=None)
    query_class: Literal["faq", "rag"] | None = None
    stream_documents_job: StreamResourcesJob | None = None
    generate_response_job: GenerateResponseJob | None = None
    retrieve_job: RetrieveJob | None = None


# Retrieving documents causes document streaming and
# response generation jobs
class RetrieveResult(BaseModel):
    successful: bool
    stream_documents_job: StreamResourcesJob | None = None
    generate_response_job: GenerateResponseJob | None = None


# Terminal states
class StreamResourcesResult(BaseModel):
    successful: bool


class GenerateResponseResult(BaseModel):
    successful: bool
