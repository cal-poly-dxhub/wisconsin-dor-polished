"""Batch documents for WebSocket delivery under API Gateway frame limits."""

from __future__ import annotations

from .models import DocumentsContent, DocumentsMessage, SourceDocument

# API Gateway WebSocket caps a single PostToConnection frame at 128 KB.
# We batch documents into multiple frames with headroom for the JSON envelope
# and for UTF-8 escape expansion.
WS_FRAME_BUDGET_BYTES = 100_000
# Cap any individual document's content so a single oversized chunk can't
# exceed the per-frame budget on its own.
MAX_DOC_CONTENT_BYTES = 60_000
_TRUNCATION_SUFFIX = "\n\n… [content truncated]"


def truncate_doc_content(doc: SourceDocument) -> SourceDocument:
    """Cap a single document's content at MAX_DOC_CONTENT_BYTES (UTF-8)."""
    content = doc.content or ""
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_DOC_CONTENT_BYTES:
        return doc
    truncated = encoded[:MAX_DOC_CONTENT_BYTES].decode("utf-8", errors="ignore")
    return doc.model_copy(update={"content": truncated + _TRUNCATION_SUFFIX})


def batch_documents_for_ws(
    docs: list[SourceDocument],
    query_id: str,
) -> list[DocumentsMessage]:
    """Pack documents into DocumentsMessage batches under the frame budget.

    The API Gateway WebSocket frame limit is 128 KB. Sending all docs at
    once for large result sets raises a 413, which drops every citation.
    The frontend appends on each 'documents' message, so multiple frames
    merge seamlessly on the client.
    """
    batches: list[DocumentsMessage] = []
    current: list[SourceDocument] = []
    current_size = 0

    for doc in docs:
        safe_doc = truncate_doc_content(doc)
        doc_bytes = len(safe_doc.model_dump_json(by_alias=True).encode("utf-8"))

        projected = current_size + doc_bytes + (1 if current else 0)
        if current and projected > WS_FRAME_BUDGET_BYTES:
            batches.append(
                DocumentsMessage(
                    query_id=query_id,
                    content=DocumentsContent(documents=current),
                )
            )
            current = [safe_doc]
            current_size = doc_bytes
        else:
            current.append(safe_doc)
            current_size = projected

    if current:
        batches.append(
            DocumentsMessage(
                query_id=query_id,
                content=DocumentsContent(documents=current),
            )
        )

    return batches
