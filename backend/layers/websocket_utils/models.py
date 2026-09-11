from typing import Any, Literal

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


class ErrorContent(WebSocketMessage):
    error: str = Field(description="The error message to be sent over the websocket.")


class ErrorMessage(WebSocketMessage):
    response_type: Literal["error"] = "error"
    content: ErrorContent


class ChunkSnippetWS(WebSocketMessage):
    page: int
    text: str


class SourceDocument(WebSocketMessage):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"
    authority_level: int | None = None
    # Mirror of RAGDocument: stable S3 reference + page range. Links use the
    # public source_url; s3_key is kept for provenance/debugging. Camel-case
    # aliasing makes these s3Key / startPage / endPage on the wire.
    s3_key: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    # WPAM edition year (e.g. 2025); None for all other doc types. Mirrors
    # RAGDocument.edition_year so the value survives the wire boundary instead
    # of being silently dropped here. Serialized as `editionYear`.
    edition_year: int | None = None
    # Per-page chunk text snippets for citation link previews.
    chunks: list[ChunkSnippetWS] = []


class DocumentsContent(WebSocketMessage):
    documents: list[SourceDocument]


class DocumentsMessage(WebSocketMessage):
    response_type: Literal["documents"] = "documents"
    query_id: str
    content: DocumentsContent


class FAQ(WebSocketMessage):
    faq_id: str
    question: str
    answer: str
    # Mirrors the shared FAQ model; serialized as `sourceUrl` for the frontend.
    source_url: str | None = None


class FAQContent(WebSocketMessage):
    faqs: list[FAQ]


class FAQMessage(WebSocketMessage):
    response_type: Literal["faq"] = "faq"
    query_id: str
    content: FAQContent


class AnswerEventType(WebSocketMessage):
    response_type: Literal["answer-event"] = "answer-event"
    event: Literal["start", "stop"]
    query_id: str


class FragmentContent(WebSocketMessage):
    fragment: str


class FragmentMessage(WebSocketMessage):
    response_type: Literal["fragment"] = "fragment"
    query_id: str
    content: FragmentContent


class AgentEventMessage(WebSocketMessage):
    """Trace event emitted by the GraphRAG agent loop.

    Delivered to the frontend during the tool loop so the UI can render
    the agent's chain-of-thought live. Best-effort — the loop must not
    block on emission failures.
    """

    response_type: Literal["agent-event"] = "agent-event"
    query_id: str
    kind: Literal[
        "loop_start",
        "reasoning",
        "tool_call",
        "tool_result",
        "loop_complete",
        "phase",
        "turn_usage",
    ]
    turn: int | None = None
    seq: int
    timestamp: int  # epoch ms at emission
    payload: dict[str, Any] = Field(default_factory=dict)
    dev_payload: dict[str, Any] = Field(default_factory=dict)


class ChoicesContent(WebSocketMessage):
    choices: list[str]


class ChoicesMessage(WebSocketMessage):
    response_type: Literal["choices"] = "choices"
    query_id: str
    content: ChoicesContent


class FlowchartAuthority(WebSocketMessage):
    kind: str  # statute | admin_rule | case_law | wpam | gov_pub | form | cq
    cite: str
    note: str | None = None


class FlowchartNode(WebSocketMessage):
    id: str
    type: str  # start | decision | terminal | end | note
    step: int | None = None
    label: str | None = None
    question: str | None = None
    definition: str | None = None
    guidance: str | None = None
    note: str | None = None
    context: str | None = None
    criteria: list[str] = []
    outcome: str | None = None  # terminal only: exempt | taxable | qualified | ...
    action: str | None = None  # terminal only: what the assessor does
    authorities: list[FlowchartAuthority] = []


class FlowchartEdge(WebSocketMessage):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    branch: str | None = None  # yes | no | None (pass-through)
    label: str | None = None


class FlowchartContent(WebSocketMessage):
    flowchart_id: str
    title: str
    summary: str | None = None
    statute: str | None = None
    disclaimer: str
    wpam_page: str | None = None
    source_url: str | None = None
    start_node: str
    nodes: list[FlowchartNode]
    edges: list[FlowchartEdge]
    router_score: float | None = None


class FlowchartMessage(WebSocketMessage):
    """An interactive decision flowchart seeded for this query.

    Delivered alongside the resource cards so the frontend can render the
    "Walk the flowchart" affordance. The chart is NOT a graph document — its
    structure (nodes/edges/authorities) travels here rather than in a citation
    card.
    """

    response_type: Literal["flowchart"] = "flowchart"
    query_id: str
    content: FlowchartContent


class SuggestionContent(WebSocketMessage):
    # `kind` discriminates the suggestion variant so the frontend can render
    # the right controls. Currently only topic-shift (offers new-chat /
    # continue-here actions); kept as a Literal so adding a variant forces a
    # matching frontend update.
    kind: Literal["topic-shift"]


class SuggestionMessage(WebSocketMessage):
    response_type: Literal["suggestion"] = "suggestion"
    query_id: str
    content: SuggestionContent
