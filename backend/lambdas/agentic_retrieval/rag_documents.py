"""RAG document construction from collected chunks and graph metadata."""

import hashlib
import logging

from case_law import (
    apply_case_law_links,
    build_opinion_card,
    collapse_case_law_by_title,
    is_case_law_stub,
)
from step_function_types.models import ChunkSnippet, RAGDocument

logger = logging.getLogger(__name__)

_NON_DOCUMENT_LABELS = frozenset({"Chunk", "Topic", "Framework"})
_SNIPPET_MAX_CHARS = 200


def _generate_source_label(chunk: dict, doc_info: dict | None) -> str:
    """Return the display label shown on the citation badge."""
    raw_url = chunk.get("source_url") or (doc_info or {}).get("source_url") or ""
    gov_source_url = raw_url if raw_url.startswith(("http://", "https://")) else ""
    return gov_source_url or (doc_info or {}).get("title", "")


def build_rag_documents(
    chunks: list[dict],
    doc_ids: set[str],
    discovery: dict[str, str] | None = None,
    fetched_opinions: dict[str, dict] | None = None,
    *,
    neptune_client,
) -> list[RAGDocument]:
    """Build RAGDocument list from collected chunks, tagged by how discovered.

    When the agent called fetch_case_opinion, the fetched opinion supersedes
    the one-chunk case-law stub for that citation, and other case-law stubs
    that came in as graph/framework noise are suppressed.
    """
    discovery = discovery or {}
    fetched_opinions = fetched_opinions or {}
    docs_by_id: dict[str, RAGDocument] = {}
    doc_infos: dict[str, dict] = {}
    snippets_by_doc: dict[str, list[ChunkSnippet]] = {}

    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text") or ""
        tag = discovery.get(doc_id, "unknown")

        page = chunk.get("start_page")
        if page is not None and chunk_text:
            snippets = snippets_by_doc.setdefault(doc_id, [])
            if not any(s.page == page for s in snippets):
                snippets.append(
                    ChunkSnippet(
                        page=page,
                        text=chunk_text[:_SNIPPET_MAX_CHARS],
                    )
                )

        if doc_id not in docs_by_id:
            doc_info = neptune_client.get_document(doc_id)
            doc_infos[doc_id] = doc_info or {}
            title = (doc_info.get("title") if doc_info else None) or doc_id
            content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
            label = _generate_source_label(chunk, doc_info)
            raw_url = chunk.get("source_url") or (doc_info or {}).get("source_url") or ""
            gov_url = raw_url if raw_url.startswith(("http://", "https://")) else None
            s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key")

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=label,
                source_url=gov_url,
                s3_key=s3_key,
                start_page=chunk.get("start_page"),
                end_page=chunk.get("end_page"),
                discovery_tag=tag,
                authority_level=(doc_info or {}).get("authority_level"),
                edition_year=chunk.get("edition_year"),
            )
        else:
            existing = docs_by_id[doc_id]
            if existing.s3_key:
                merged_s3_key = existing.s3_key
                # Use the earliest page across all merged chunks so the
                # source card links to the first relevant section, not an
                # arbitrary chunk.
                chunk_start = chunk.get("start_page")
                chunk_end = chunk.get("end_page")
                existing_start = existing.start_page
                existing_end = existing.end_page
                if chunk_start is not None and existing_start is not None:
                    merged_start_page = min(existing_start, chunk_start)
                else:
                    merged_start_page = (
                        existing_start if existing_start is not None else chunk_start
                    )
                if chunk_end is not None and existing_end is not None:
                    merged_end_page = max(existing_end, chunk_end)
                else:
                    merged_end_page = existing_end if existing_end is not None else chunk_end
            else:
                merged_s3_key = chunk.get("s3_key")
                merged_start_page = chunk.get("start_page")
                merged_end_page = chunk.get("end_page")
            merged_edition_year = existing.edition_year or chunk.get("edition_year")
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source,
                source_url=existing.source_url,
                s3_key=merged_s3_key,
                start_page=merged_start_page,
                end_page=merged_end_page,
                discovery_tag=existing.discovery_tag,
                authority_level=existing.authority_level,
                edition_year=merged_edition_year,
            )

    for doc_id in doc_ids - docs_by_id.keys():
        doc_info = neptune_client.get_document(doc_id)
        if not doc_info:
            continue
        labels = doc_info.get("labels") or []
        if any(label in _NON_DOCUMENT_LABELS for label in labels):
            continue

        if not doc_info.get("summary"):
            logger.warning(
                "STUB_PROMOTION_TRIGGERED: doc_id=%s reached citation card with no summary. "
                "Expected DEFINED_BY auto-resolution to prevent this — investigate.",
                doc_id,
            )

        content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
        tag = discovery.get(doc_id, "unknown")
        label = _generate_source_label({}, doc_info)
        doc_infos[doc_id] = doc_info
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title") or doc_id,
            content=doc_info.get("summary") or "",
            source=label,
            source_url=doc_info.get("source_url"),
            s3_key=doc_info.get("s3_key"),
            start_page=doc_info.get("_promoted_start_page"),
            end_page=doc_info.get("_promoted_end_page"),
            discovery_tag=tag,
            authority_level=doc_info.get("authority_level"),
            edition_year=doc_info.get("edition_year"),
        )

    docs_by_id = apply_case_law_links(docs_by_id, doc_infos)

    if fetched_opinions:
        for stub_doc_id, payload in fetched_opinions.items():
            docs_by_id[stub_doc_id] = build_opinion_card(stub_doc_id, payload, neptune_client)

        fetched_ids = set(fetched_opinions.keys())
        noise_tags = {"graph-neighbor", "framework-list"}
        docs_by_id = {
            doc_id: rag_doc
            for doc_id, rag_doc in docs_by_id.items()
            if doc_id in fetched_ids
            or not (is_case_law_stub(doc_id) and rag_doc.discovery_tag in noise_tags)
        }

    docs_by_id = collapse_case_law_by_title(docs_by_id)

    for doc_id, rag_doc in docs_by_id.items():
        if doc_id in snippets_by_doc:
            rag_doc.chunks = snippets_by_doc[doc_id]

    return list(docs_by_id.values())
