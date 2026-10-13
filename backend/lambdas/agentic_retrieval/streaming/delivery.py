"""WebSocket delivery of resource cards and full answers."""


from chat_contracts.models import FAQResource, RAGDocument
from websocket_utils.batching import batch_documents_for_ws
from websocket_utils.models import (
    FAQ,
    FAQContent,
    FAQMessage,
    FlowchartContent,
    FlowchartMessage,
    SourceDocument,
)
from websocket_utils.utils import WebSocketServer, post_message


def flowchart_content_for_wire(chart: dict, score: float | None) -> dict:
    """Map a seeded flowchart sidecar dict to the camelCase wire/persist shape.

    The sidecar is snake_case with a nested `source`; the frontend (and the
    stored history record, so resume matches the live path) expects the flat
    camelCase FlowchartContent. Extra sidecar keys (e.g. per-node `figure`) are
    dropped by the model's field set; edge `from`/`to` aliases are preserved.
    """
    src = chart.get("source", {}) or {}
    content = FlowchartContent.model_validate(
        {
            "flowchartId": chart.get("flowchart_id", ""),
            "title": chart.get("title", ""),
            "summary": chart.get("summary"),
            "statute": chart.get("statute"),
            "disclaimer": chart.get("disclaimer", ""),
            "wpamPage": src.get("wpam_page"),
            "sourceUrl": src.get("source_url"),
            "startNode": chart.get("start_node", ""),
            "nodes": chart.get("nodes", []),
            "edges": chart.get("edges", []),
            "routerScore": score,
        }
    )
    return content.model_dump(by_alias=True)


def _flowchart_message(query_id: str, chart: dict, score: float | None) -> FlowchartMessage:
    """Build a FlowchartMessage from a seeded flowchart sidecar dict."""
    src = chart.get("source", {}) or {}
    return FlowchartMessage(
        query_id=query_id,
        content=FlowchartContent.model_validate(
            {
                "flowchartId": chart.get("flowchart_id", ""),
                "title": chart.get("title", ""),
                "summary": chart.get("summary"),
                "statute": chart.get("statute"),
                "disclaimer": chart.get("disclaimer", ""),
                "wpamPage": src.get("wpam_page"),
                "sourceUrl": src.get("source_url"),
                "startNode": chart.get("start_node", ""),
                "nodes": chart.get("nodes", []),
                "edges": chart.get("edges", []),
                "routerScore": score,
            }
        ),
    )


def send_resources(
    ws_server: WebSocketServer,
    query_id: str,
    rag_documents: list[RAGDocument],
    faq_resource: FAQResource | None,
    seeded_flowchart: dict | None = None,
    seeded_flowchart_score: float | None = None,
) -> None:
    """Send resource cards (documents + FAQs + a seeded flowchart) over WebSocket."""
    source_documents = [
        SourceDocument(
            document_id=doc.document_id,
            title=doc.title,
            content=doc.content,
            source=doc.source,
            source_url=doc.source_url,
            discovery_tag=doc.discovery_tag,
            authority_level=doc.authority_level,
            s3_key=doc.s3_key,
            start_page=doc.start_page,
            end_page=doc.end_page,
            edition_year=doc.edition_year,
            chunks=[{"page": c.page, "text": c.text} for c in doc.chunks],
        )
        for doc in rag_documents
    ]

    for msg in batch_documents_for_ws(source_documents, query_id):
        post_message(ws_server, msg)

    if faq_resource:
        faq_message = FAQMessage(
            query_id=query_id,
            content=FAQContent(
                faqs=[
                    FAQ(
                        faq_id=faq.faq_id,
                        question=faq.question,
                        answer=faq.answer,
                        source_url=faq.source_url,
                    )
                    for faq in faq_resource.faqs
                ]
            ),
        )
        post_message(ws_server, faq_message)

    if seeded_flowchart:
        fc_message = _flowchart_message(query_id, seeded_flowchart, seeded_flowchart_score)
        post_message(ws_server, fc_message)

