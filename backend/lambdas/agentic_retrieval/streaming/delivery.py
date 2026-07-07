"""WebSocket delivery of resource cards and full answers."""

import json

from step_function_types.models import FAQResource, RAGDocument
from websocket_utils.batching import batch_documents_for_ws
from websocket_utils.models import (
    FAQ,
    AnswerEventType,
    FAQContent,
    FAQMessage,
    FragmentContent,
    FragmentMessage,
    SourceDocument,
)
from websocket_utils.utils import WebSocketServer


def send_resources(
    ws_server: WebSocketServer,
    query_id: str,
    rag_documents: list[RAGDocument],
    faq_resource: FAQResource | None,
) -> None:
    """Send resource cards (documents + FAQs) over WebSocket."""
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
        data = json.dumps({"streamId": "resources", "body": msg.model_dump(by_alias=True)})
        ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

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
        data = json.dumps({"streamId": "resources", "body": faq_message.model_dump(by_alias=True)})
        ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)


def send_resources_and_finalize(
    ws_server: WebSocketServer,
    query_id: str,
    answer: str,
    rag_documents: list[RAGDocument],
    faq_resource: FAQResource | None,
) -> None:
    """Send documents, FAQs, and full answer over WebSocket (fallback path)."""
    send_resources(ws_server, query_id, rag_documents, faq_resource)

    start_msg = AnswerEventType(event="start", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": start_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)

    frag_msg = FragmentMessage(query_id=query_id, content=FragmentContent(fragment=answer))
    frag_data = json.dumps({"streamId": "answer", "body": frag_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=frag_data)

    stop_msg = AnswerEventType(event="stop", query_id=query_id)
    data = json.dumps({"streamId": "answer-event", "body": stop_msg.model_dump(by_alias=True)})
    ws_server.client.post_to_connection(ConnectionId=ws_server.connection_id, Data=data)
