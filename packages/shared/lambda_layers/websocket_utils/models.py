from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel_case(string: str) -> str:
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelCaseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel_case, populate_by_name=True)


class WebSocketMessage(CamelCaseModel):
    pass


class PlainWebSocketMessage(WebSocketMessage):
    """
    A plain text message.
    """

    message: str = Field(description="The message to be sent over the websocket.")


class ErrorMessage(WebSocketMessage):
    """
    Used to report errors that occur during step function processing.
    """

    error: str = Field(description="The error message to be sent over the websocket.")


class SourceDocument(WebSocketMessage):
    document_id: str
    title: str
    content: str
    source: str | None = None


class DocumentsContent(WebSocketMessage):
    documents: list[SourceDocument]


class DocumentsMessage(WebSocketMessage):
    response_type: Literal["documents"] = "documents"
    query_id: str
    content: DocumentsContent


class FAQ(WebSocketMessage):
    question: str
    answer: str


class FAQContent(WebSocketMessage):
    faq: FAQ


class FAQMessage(WebSocketMessage):
    response_type: Literal["faq"] = "faq"
    query_id: str
    content: FAQContent


class AnswerEventType(WebSocketMessage):
    response_type: Literal["answer-event"] = "answer-event"
    event: Literal["start", "stop"]
    query_id: str
