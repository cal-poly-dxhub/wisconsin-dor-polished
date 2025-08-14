from typing import Literal

from pydantic import BaseModel, Field


# Request body used to submit a message
class MessageRequest(BaseModel):
    message: str


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


# Types of resources used in generating responses
class FAQResource(BaseModel):
    question: str
    answer: str


class RAGDocument(BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = Field(default=None)


class DocumentResource(BaseModel):
    documents: list[RAGDocument] = Field(default_factory=list)


# Job passed to the response generation lambda
class GenerateResponseJob(BaseModel):
    query: str
    query_id: str
    session_id: str
    resource_type: Literal["faq", "documents", None] = Field(default=None)
    resources: FAQResource | DocumentResource | None = Field(default=None)


# Job passed to the retrieval lambda
class RetrieveJob(BaseModel):
    query: str
    query_id: str
    session_id: str


# Job passed to the document streaming lambda
class StreamResourcesJob(BaseModel):
    query_id: str
    session_id: str
    resource_type: Literal["documents", "faq"]
    content: DocumentResource | FAQResource


# Either a response generation job with a streaming job for FAQs or
# a plan retrieval job for queries classified as RAG. Differentiate
# based on the query_class field.
class ClassifierResult(BaseModel):
    query_class: Literal["faq", "rag"] | None = None
    stream_documents_job: StreamResourcesJob | None = None
    generate_response_job: GenerateResponseJob | None = None
    retrieve_job: RetrieveJob | None = None
