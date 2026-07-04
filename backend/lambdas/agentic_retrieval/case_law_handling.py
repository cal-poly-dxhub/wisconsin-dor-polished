"""Case-law citation resolution: stub detection, dedup, opinion cards, link resolution."""

import hashlib
import logging
import re

from case_opinion import scholar_url
from step_function_types.models import RAGDocument

logger = logging.getLogger(__name__)

CASE_LAW_STUB_PREFIX = "case-law-"

DISCOVERY_TAG_PRIORITY = [
    "opinion-fetched",
    "fetched",
    "vector-search",
    "graph-neighbor",
    "framework-list",
    "unknown",
]

_CASE_NAME_SUFFIX_RE = re.compile(
    r"\s*(?:[–—:]|-\s)|,\s*(?=\d+\b)"
)

_YEAR_RE = re.compile(r"\b(\d{4})\b")


def is_case_law_stub(doc_id: str) -> bool:
    """True when a doc_id is a case-law citation stub."""
    return doc_id.startswith(CASE_LAW_STUB_PREFIX)


def extract_case_name(title: str) -> str:
    """Canonical case-name portion of a title, lowercased and whitespace-collapsed."""
    match = _CASE_NAME_SUFFIX_RE.search(title)
    core = title[: match.start()] if match else title
    return " ".join(core.lower().split())


def extract_year(title: str) -> str | None:
    """First 4-digit year in the title, or None if absent."""
    match = _YEAR_RE.search(title)
    return match.group(1) if match else None


def collapse_case_law_by_title(
    docs_by_id: dict[str, RAGDocument],
) -> dict[str, RAGDocument]:
    """Merge case-law RAGDocuments that share a normalized title.

    Parallel citations of the same decision become separate Neptune Document
    nodes during ingest; this collapses them back into one sidebar card.
    """
    by_name: dict[str, dict[str | None, list[tuple[str, RAGDocument]]]] = {}
    passthrough: dict[str, RAGDocument] = {}

    for doc_id, rag_doc in docs_by_id.items():
        if not is_case_law_stub(doc_id):
            passthrough[doc_id] = rag_doc
            continue
        name_key = extract_case_name(rag_doc.title)
        year_key = extract_year(rag_doc.title)
        by_name.setdefault(name_key, {}).setdefault(year_key, []).append(
            (doc_id, rag_doc)
        )

    groups: list[list[tuple[str, RAGDocument]]] = []
    for year_buckets in by_name.values():
        yearless = year_buckets.pop(None, [])
        if not year_buckets:
            if yearless:
                groups.append(yearless)
            continue

        dominant_year = max(
            year_buckets.keys(),
            key=lambda y: (len(year_buckets[y]), y),
        )
        year_buckets[dominant_year].extend(yearless)

        for bucket in year_buckets.values():
            groups.append(bucket)

    merged: dict[str, RAGDocument] = dict(passthrough)
    for group in groups:
        if len(group) == 1:
            doc_id, rag_doc = group[0]
            merged[doc_id] = rag_doc
            continue

        def sort_key(item: tuple[str, RAGDocument]) -> tuple[int, int]:
            _, doc = item
            tag_rank = (
                DISCOVERY_TAG_PRIORITY.index(doc.discovery_tag)
                if doc.discovery_tag in DISCOVERY_TAG_PRIORITY
                else len(DISCOVERY_TAG_PRIORITY)
            )
            return (tag_rank, -len(doc.content or ""))

        group.sort(key=sort_key)
        primary_id, primary_doc = group[0]

        seen_texts: set[str] = set()
        merged_parts: list[str] = []
        for _, doc in group:
            text = (doc.content or "").strip()
            if text and text not in seen_texts:
                seen_texts.add(text)
                merged_parts.append(text)
        merged_content = "\n\n".join(merged_parts)

        merged[primary_id] = RAGDocument(
            document_id=primary_doc.document_id,
            title=primary_doc.title,
            content=merged_content or primary_doc.content,
            source=primary_doc.source,
            source_url=primary_doc.source_url,
            s3_key=primary_doc.s3_key,
            start_page=primary_doc.start_page,
            end_page=primary_doc.end_page,
            discovery_tag=primary_doc.discovery_tag,
            authority_level=primary_doc.authority_level,
            edition_year=primary_doc.edition_year,
        )
        logger.info(
            f"Collapsed {len(group)} case-law parallel citations into "
            f"'{primary_doc.title[:60]}' (ids: {[g[0] for g in group]})"
        )

    return merged


def build_opinion_card(stub_doc_id: str, payload: dict, neptune_client) -> RAGDocument:
    """Build a RAGDocument for a fetched full court opinion."""
    citation = payload.get("citation", "")
    opinion_text = payload.get("text", "")
    payload_scholar_url = payload.get("scholar_url", "")

    doc_info = neptune_client.get_document(stub_doc_id) or {}
    title = doc_info.get("title") or citation or stub_doc_id
    content_hash = hashlib.sha256(stub_doc_id.encode()).hexdigest()[:7]

    node_url = doc_info.get("source_url")
    public_url = node_url or payload_scholar_url or (scholar_url(citation) if citation else None)

    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        source_url=public_url,
        s3_key=None,
        start_page=None,
        end_page=None,
        discovery_tag="opinion-fetched",
        authority_level=doc_info.get("authority_level") if doc_info else 3,
        edition_year=None,
    )


def apply_case_law_links(
    docs_by_id: dict[str, RAGDocument], doc_infos: dict[str, dict]
) -> dict[str, RAGDocument]:
    """Ensure case-law stub cards link to a public URL and drop S3 refs."""
    for doc_id, card in docs_by_id.items():
        if not is_case_law_stub(doc_id):
            continue
        doc_info = doc_infos.get(doc_id) or {}
        node_url = doc_info.get("source_url")
        citation = doc_info.get("citation")
        public_url = node_url or (scholar_url(citation) if citation else None)
        if not public_url:
            continue
        docs_by_id[doc_id] = card.model_copy(
            update={
                "source_url": public_url,
                "s3_key": None,
                "start_page": None,
                "end_page": None,
            }
        )
    return docs_by_id
