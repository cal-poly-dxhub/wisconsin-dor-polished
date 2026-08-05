"""
Neptune Analytics client for OpenCypher queries and vector search.
"""

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)
LOG_NEPTUNE_TRACE = os.environ.get("LOG_NEPTUNE_TRACE", "true").lower() == "true"
LOG_NEPTUNE_QUERY_TEXT = os.environ.get("LOG_NEPTUNE_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_QUERY_CHARS = int(os.environ.get("LOG_MAX_QUERY_CHARS", "1000"))


# Curly quotes and dashes the source PDFs carry into stored headings but that
# an LLM normalizes to ASCII when reproducing a heading from memory. Mapped 1:1.
_HEADING_TYPOGRAPHY = {
    "‘": "'",  # left single quote
    "’": "'",  # right single quote (the § 70.27 apostrophe bug)
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "–": "-",  # en dash
    "—": "-",  # em dash
}
_HEADING_TRANS = str.maketrans(_HEADING_TYPOGRAPHY)


def _normalize_heading(value: str) -> str:
    """Fold heading typography to a canonical ASCII form for fuzzy matching.

    Stored headings preserve the source PDF's curly quotes, en/em dashes, tabs
    (U+0009), and thin spaces (U+2009). A model targeting `get_section` types
    ASCII quotes/hyphens and single spaces, so exact equality misses. Used only
    on the fallback path — exact match is always tried first.
    """
    if not value:
        return ""
    # Collapse every whitespace run (tab, thin space, repeats) to one space.
    return re.sub(r"\s+", " ", value.translate(_HEADING_TRANS)).strip()


def _compact_cypher(cypher: str) -> str:
    """Make OpenCypher safe and small enough for logs.

    Vector search inlines a 1024-dim embedding into the query because Neptune
    Analytics does not parameterize CALL procedures. Logging that vector would
    be noisy and expensive, so replace it with a marker before truncating.
    """
    compact = " ".join(cypher.split())
    compact = re.sub(
        r"topKByEmbedding\(\[[^\]]+\]",
        "topKByEmbedding([<embedding>]",
        compact,
    )
    if len(compact) <= LOG_MAX_QUERY_CHARS:
        return compact
    return (
        compact[:LOG_MAX_QUERY_CHARS] + f"...[truncated {len(compact) - LOG_MAX_QUERY_CHARS} chars]"
    )


def _compact_parameters(parameters: dict | None) -> dict[str, Any]:
    if not parameters:
        return {}
    compact: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, str) and len(value) > 250:
            compact[key] = value[:250] + f"...[truncated {len(value) - 250} chars]"
        elif isinstance(value, list):
            compact[key] = value[:10] + (
                [f"...[{len(value) - 10} more]"] if len(value) > 10 else []
            )
        else:
            compact[key] = value
    return compact


def _log_neptune_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    if not LOG_NEPTUNE_TRACE and level < logging.WARNING:
        return
    payload = {
        "component": "graphrag.neptune_client",
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(payload, default=str, separators=(",", ":")))


class NeptuneClient:
    """Client for Neptune Analytics OpenCypher + vector search."""

    def __init__(self, graph_id: str | None = None, region: str | None = None):
        self.graph_id = graph_id or os.environ.get("NEPTUNE_GRAPH_ID", "")
        self.client = boto3.client(
            "neptune-graph",
            region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
        )
        self._current_wpam_year: int | None = None

    @property
    def current_wpam_year(self) -> int | None:
        """The most recent WPAM edition year in the graph. Queried once on
        first access and cached for the Lambda invocation lifetime."""
        if self._current_wpam_year is None:
            try:
                results = self.query(
                    "MATCH (d)-[:BELONGS_TO]->(fw:Framework {id: 'FW-WPAM'}) "
                    "RETURN max(d.edition_year) AS max_year",
                    query_name="current_wpam_year",
                )
                if results and results[0].get("max_year") is not None:
                    self._current_wpam_year = int(results[0]["max_year"])
                    logger.info(f"current_wpam_year resolved to {self._current_wpam_year}")
            except Exception:
                logger.warning("Failed to resolve current_wpam_year", exc_info=True)
        return self._current_wpam_year

    def query(
        self,
        cypher: str,
        parameters: dict | None = None,
        max_retries: int = 3,
        query_name: str = "open_cypher",
    ) -> list[dict]:
        """Execute an OpenCypher query with retries."""
        kwargs = {
            "graphIdentifier": self.graph_id,
            "language": "OPEN_CYPHER",
            "queryString": cypher,
        }
        if parameters:
            kwargs["parameters"] = parameters

        query_hash = hashlib.sha256(cypher.encode("utf-8")).hexdigest()[:16]
        base_log_fields: dict[str, Any] = {
            "query_name": query_name,
            "graph_id": self.graph_id,
            "query_hash": query_hash,
            "parameter_keys": sorted((parameters or {}).keys()),
        }
        if LOG_NEPTUNE_QUERY_TEXT:
            base_log_fields["query_preview"] = _compact_cypher(cypher)
            base_log_fields["parameters"] = _compact_parameters(parameters)

        for attempt in range(max_retries):
            started = time.perf_counter()
            _log_neptune_event(
                "neptune_query_start",
                **base_log_fields,
                attempt=attempt + 1,
                max_retries=max_retries,
            )
            try:
                response = self.client.execute_query(**kwargs)
                # Neptune-graph returns results as a streaming body, not a pre-parsed dict.
                payload = response.get("payload")
                if payload is None:
                    results = response.get("results", [])
                    _log_neptune_event(
                        "neptune_query_complete",
                        **base_log_fields,
                        attempt=attempt + 1,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        result_count=len(results),
                    )
                    return results
                raw = payload.read()
                if not raw:
                    _log_neptune_event(
                        "neptune_query_complete",
                        **base_log_fields,
                        attempt=attempt + 1,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        result_count=0,
                    )
                    return []
                parsed = json.loads(raw)
                results = parsed.get("results", [])
                _log_neptune_event(
                    "neptune_query_complete",
                    **base_log_fields,
                    attempt=attempt + 1,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    result_count=len(results),
                )
                return results
            except Exception as e:
                if attempt < max_retries - 1 and "Throttling" in str(e):
                    wait = min(30, 2**attempt)
                    _log_neptune_event(
                        "neptune_query_throttled",
                        logging.WARNING,
                        **base_log_fields,
                        attempt=attempt + 1,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        retry_after_s=wait,
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                    time.sleep(wait)
                else:
                    _log_neptune_event(
                        "neptune_query_error",
                        logging.ERROR,
                        **base_log_fields,
                        attempt=attempt + 1,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                    raise

        return []

    def vector_search(self, embedding: list[float], top_k: int = 10) -> list[dict]:
        """Search for similar chunks using Neptune's vector index.

        Neptune Analytics does not support parameterization inside CALL
        procedures, so the embedding and topK are inlined into the query.
        """
        embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        # OPTIONAL MATCH on the parent doc surfaces effective_date alongside
        # each chunk so the agent can prefer newer Advisory results when two
        # chunks address the same topic. Cheap join — chunk→doc is 1:1.
        # framework_id and edition_year flow through for WPAM dedup.
        results = self.query(
            f"CALL neptune.algo.vectors.topKByEmbedding({embedding_literal}, {{topK: {top_k}}}) "
            "YIELD node, score "
            "OPTIONAL MATCH (node)-[:EXTRACTED_FROM]->(parent) "
            "OPTIONAL MATCH (parent)-[:BELONGS_TO]->(fw:Framework) "
            "RETURN node.id AS chunk_id, node.text AS text, node.doc_id AS doc_id, "
            "node.source_url AS source_url, node.s3_key AS s3_key, "
            "node.start_page AS start_page, node.end_page AS end_page, "
            "node.heading AS heading, node.subheading AS subheading, "
            "node.edition_year AS edition_year, "
            "fw.id AS framework_id, "
            "parent.authority_level AS authority_level, "
            "parent.effective_date AS effective_date, score",
            query_name="vector_search",
        )
        return results

    def get_document(self, doc_id: str) -> dict | None:
        """Fetch a document node by ID."""
        results = self.query(
            "MATCH (d {id: $id}) "
            "RETURN d.id AS id, d.title AS title, d.summary AS summary, "
            "d.source_url AS source_url, d.source_key AS s3_key, "
            "d.doc_type AS doc_type, d.citation AS citation, "
            "d.authority_level AS authority_level, "
            "d.effective_date AS effective_date, "
            "d.edition_year AS edition_year, "
            "labels(d) AS labels",
            {"id": doc_id},
            query_name="get_document",
        )
        return results[0] if results else None

    def get_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 50,
        title_filter: str | None = None,
    ) -> list[dict]:
        """Get neighboring nodes via specified edge types."""
        if edge_types:
            type_filter = "|".join(edge_types)
            if direction == "outgoing":
                pattern = f"MATCH (d {{id: $id}})-[r:{type_filter}]->(n)"
            elif direction == "incoming":
                pattern = f"MATCH (d {{id: $id}})<-[r:{type_filter}]-(n)"
            else:
                pattern = f"MATCH (d {{id: $id}})-[r:{type_filter}]-(n)"
            where_clause = ""
        else:
            if direction == "outgoing":
                pattern = "MATCH (d {id: $id})-[r]->(n)"
            elif direction == "incoming":
                pattern = "MATCH (d {id: $id})<-[r]-(n)"
            else:
                pattern = "MATCH (d {id: $id})-[r]-(n)"
            where_clause = " WHERE type(r) <> 'EXTRACTED_FROM'"

        if title_filter:
            conjunction = " AND " if where_clause else " WHERE "
            where_clause += f"{conjunction}toLower(n.title) CONTAINS toLower($title_filter)"

        params: dict[str, Any] = {"id": node_id}
        if title_filter:
            params["title_filter"] = title_filter

        results = self.query(
            f"{pattern}{where_clause} "
            "OPTIONAL MATCH (n)-[:DEFINED_BY]->(resolved) WHERE n.stub = true "
            "WITH r, CASE WHEN resolved IS NOT NULL THEN resolved ELSE n END AS node "
            "OPTIONAL MATCH (node)-[:BELONGS_TO]->(fw:Framework) "
            "RETURN DISTINCT type(r) AS relationship, node.id AS id, node.title AS title, "
            "node.summary AS summary, node.source_url AS source_url, "
            "node.doc_type AS doc_type, node.citation AS citation, "
            "node.effective_date AS effective_date, "
            "node.edition_year AS edition_year, "
            "node.heading AS heading, "
            "node.authority_level AS authority_level, "
            "fw.id AS framework_id, "
            "labels(node) AS labels "
            f"LIMIT {int(limit)}",
            params,
            query_name="get_neighbors",
        )
        return results

    def get_authority_chain(self, node_id: str, max_depth: int = 5) -> list[dict]:
        """Trace the governance hierarchy from a node up to the root framework."""
        results = self.query(
            f"MATCH p=(d {{id: $id}})-[:PART_OF|BELONGS_TO|DERIVED_FROM*1..{max_depth}]->(root) "
            "WHERE NOT (root)-[:PART_OF|BELONGS_TO|DERIVED_FROM]->() "
            "UNWIND nodes(p) AS node "
            "RETURN DISTINCT node.id AS id, node.title AS title, "
            "node.authority_level AS authority_level, labels(node) AS labels",
            {"id": node_id},
            query_name="get_authority_chain",
        )
        return results

    def list_framework_docs(self, framework_id: str) -> list[dict]:
        """List all documents belonging to a framework."""
        results = self.query(
            "MATCH (d)-[:BELONGS_TO]->(f:Framework {id: $fw_id}) "
            "RETURN d.id AS id, d.title AS title, d.doc_type AS doc_type, "
            "d.source_url AS source_url, labels(d) AS labels",
            {"fw_id": framework_id},
            query_name="list_framework_docs",
        )
        return results

    def get_chunks_for_doc(self, doc_id: str) -> list[dict]:
        """Get all chunks for a document with full metadata."""
        results = self.query(
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
            "RETURN c.id AS chunk_id, c.text AS text, c.doc_id AS doc_id, "
            "c.source_url AS source_url, c.s3_key AS s3_key, "
            "c.start_page AS start_page, c.end_page AS end_page, "
            "c.heading AS heading, c.subheading AS subheading, "
            "c.chunk_index AS chunk_index "
            "ORDER BY c.chunk_index",
            {"doc_id": doc_id},
            query_name="get_chunks_for_doc",
        )
        return results

    def list_document_sections(self, doc_id: str) -> list[dict]:
        """List distinct section headings for a document with chunk counts and page ranges."""
        results = self.query(
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
            "WITH c.heading AS heading, count(c) AS chunk_count, "
            "min(c.start_page) AS first_page, max(c.end_page) AS last_page, "
            "min(c.chunk_index) AS first_index "
            "WHERE heading IS NOT NULL AND heading <> '' "
            "RETURN heading, chunk_count, first_page, last_page "
            "ORDER BY first_index",
            {"doc_id": doc_id},
            query_name="list_document_sections",
        )
        return results

    def _resolve_heading(self, doc_id: str, heading: str) -> str | None:
        """Map a model-supplied heading to the exact stored heading.

        The model reproduces headings from memory and folds curly quotes/dashes
        to ASCII and whitespace runs to single spaces, so an exact `c.heading =`
        match misses (see § 70.27's curly apostrophe). Compare on the normalized
        form and, on a tie-free miss, accept a stored heading whose normalized
        form starts with the normalized input (the model typed a shorter title).
        Returns the stored heading verbatim, or None if nothing matches.
        """
        target = _normalize_heading(heading)
        if not target:
            return None
        sections = self.list_document_sections(doc_id)
        prefix_match: str | None = None
        resolved: str | None = None
        match_kind = "none"
        for section in sections:
            stored = section.get("heading") or ""
            normalized = _normalize_heading(stored)
            if normalized == target:
                resolved, match_kind = stored, "normalized"  # always preferred
                break
            if prefix_match is None and normalized.startswith(target):
                prefix_match = stored  # first-in-document prefix hit; keep looking for exact
        if resolved is None and prefix_match is not None:
            resolved, match_kind = prefix_match, "prefix"
        _log_neptune_event(
            "get_section_heading_resolved",
            query_name="resolve_heading",
            doc_id=doc_id,
            requested_heading=heading,
            resolved_heading=resolved,
            match_kind=match_kind,
            section_count=len(sections),
        )
        return resolved

    def get_section_chunks(self, doc_id: str, heading: str) -> list[dict]:
        """Get all chunks for a specific section (heading) within a document."""
        results = self.query(
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
            "WHERE c.heading = $heading "
            "RETURN c.id AS chunk_id, c.text AS text, c.doc_id AS doc_id, "
            "c.source_url AS source_url, c.s3_key AS s3_key, "
            "c.start_page AS start_page, c.end_page AS end_page, "
            "c.heading AS heading, c.subheading AS subheading, "
            "c.chunk_index AS chunk_index "
            "ORDER BY c.chunk_index",
            {"doc_id": doc_id, "heading": heading},
            query_name="get_section_chunks",
        )
        if not results:
            resolved = self._resolve_heading(doc_id, heading)
            if resolved and resolved != heading:
                return self.get_section_chunks(doc_id, resolved)
        return results

    def get_section_chunks_with_embeddings(self, doc_id: str, heading: str) -> list[dict]:
        """Get section chunks with their stored vector embeddings for local ranking."""
        results = self.query(
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
            "WHERE c.heading = $heading "
            "WITH c ORDER BY c.chunk_index "
            "CALL neptune.algo.vectors.get(c) YIELD embedding "
            "RETURN c.id AS chunk_id, c.text AS text, c.doc_id AS doc_id, "
            "c.source_url AS source_url, c.s3_key AS s3_key, "
            "c.start_page AS start_page, c.end_page AS end_page, "
            "c.heading AS heading, c.subheading AS subheading, "
            "c.chunk_index AS chunk_index, embedding",
            {"doc_id": doc_id, "heading": heading},
            query_name="get_section_chunks_with_embeddings",
        )
        if not results:
            resolved = self._resolve_heading(doc_id, heading)
            if resolved and resolved != heading:
                return self.get_section_chunks_with_embeddings(doc_id, resolved)
        return results

    def get_neighbor_case_summaries_with_embeddings(
        self, node_id: str, direction: str = "outgoing"
    ) -> list[dict]:
        """Get CaseLaw neighbor summary chunks with embeddings for local ranking.

        Traverses CITES edges from a statute stub to CaseLaw nodes, then fetches
        their summary chunks (heading='Holding') with stored vectors.
        """
        if direction == "outgoing":
            pattern = "MATCH (s {id: $id})-[:CITES]->(cl:CaseLaw)"
        elif direction == "incoming":
            pattern = "MATCH (s {id: $id})<-[:CITES]-(cl:CaseLaw)"
        else:
            pattern = "MATCH (s {id: $id})-[:CITES]-(cl:CaseLaw)"
        results = self.query(
            f"{pattern} "
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(cl) "
            "WHERE c.content_role = 'summary_holding' "
            "OR c.heading IN ['Holding', 'Holding summary'] "
            "CALL neptune.algo.vectors.get(c) YIELD embedding "
            "RETURN cl.id AS case_id, cl.title AS title, cl.citation AS citation, "
            "cl.source_url AS source_url, c.text AS summary, embedding",
            {"id": node_id},
            query_name="get_neighbor_case_summaries",
        )
        return results

    def get_case_chunks_for_statutes_with_embeddings(
        self, statute_ids: list[str], limit: int = 200
    ) -> list[dict]:
        """Fetch directly citing case-law chunks for multiple statute stubs.

        Uses one query and one global cap. Candidate prioritization happens
        before vector materialization so a multi-stub backfill does not issue
        N calls or load every chunk from every neighboring case document.
        """
        if not statute_ids:
            return []
        fetch_limit = max(1, int(limit))
        return self.query(
            "UNWIND $statute_ids AS sid "
            "MATCH (s:Statute {id: sid})<-[:CITES]-(c:Chunk) "
            "MATCH (c)-[:EXTRACTED_FROM]->(cl:CaseLaw) "
            "WITH c, cl, collect(DISTINCT sid) AS cited_stub_ids, "
            "CASE "
            "WHEN c.content_role CONTAINS 'analysis_holding' THEN 0 "
            "WHEN c.content_role IN ['summary_holding', 'opening_holding'] THEN 1 "
            "ELSE 2 END AS role_priority "
            "ORDER BY role_priority, size(cited_stub_ids) DESC, c.chunk_index, cl.id "
            f"LIMIT {fetch_limit} "
            "CALL neptune.algo.vectors.get(c) YIELD embedding "
            "RETURN c.id AS chunk_id, c.text AS text, cl.id AS case_id, "
            "cl.id AS doc_id, cl.title AS title, cl.citation AS citation, "
            "cl.source_url AS source_url, cl.authority_level AS authority_level, "
            "c.heading AS heading, c.subheading AS subheading, "
            "c.content_role AS content_role, c.chunk_index AS chunk_index, "
            "c.start_page AS start_page, c.end_page AS end_page, "
            "cited_stub_ids, embedding",
            {"statute_ids": sorted(set(statute_ids))},
            query_name="get_case_chunks_for_statutes",
        )

    def get_chunk_statute_ids(self, chunk_ids: list[str]) -> list[str]:
        """Return statute IDs cited by the given chunks (via CITES edges)."""
        if not chunk_ids:
            return []
        results = self.query(
            "UNWIND $chunk_ids AS cid "
            "MATCH (c:Chunk {id: cid})-[:CITES]->(s:Statute) "
            "RETURN DISTINCT s.id AS statute_id",
            {"chunk_ids": chunk_ids},
            query_name="get_chunk_statute_ids",
        )
        return [r["statute_id"] for r in results if r.get("statute_id")]

    def rank_neighbors_by_shared_statutes(
        self,
        neighbor_doc_ids: list[str],
        chunk_statute_ids: list[str],
        limit: int = 3,
    ) -> list[str]:
        """Rank neighbor docs by how many statutes they share with query chunks.

        Returns doc IDs ordered by shared statute count (descending). Used to
        pick the most topically relevant neighbors for citation scanning.
        """
        if not neighbor_doc_ids or not chunk_statute_ids:
            return []
        results = self.query(
            "UNWIND $doc_ids AS did "
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: did}) "
            "MATCH (c)-[:CITES]->(s:Statute) "
            "WHERE s.id IN $statute_ids "
            "RETURN d.id AS doc_id, count(DISTINCT s) AS shared_statutes "
            "ORDER BY shared_statutes DESC "
            f"LIMIT {int(limit)}",
            {"doc_ids": neighbor_doc_ids, "statute_ids": chunk_statute_ids},
            query_name="rank_neighbors_by_shared_statutes",
        )
        return [r["doc_id"] for r in results if r.get("doc_id")]

    def get_chunks_text_for_docs(self, doc_ids: list[str]) -> list[str]:
        """Fetch chunk text for the given docs. Returns a flat list of text strings."""
        if not doc_ids:
            return []
        results = self.query(
            "UNWIND $doc_ids AS did "
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: did}) "
            "RETURN c.text AS text",
            {"doc_ids": doc_ids},
            query_name="get_chunks_text_for_docs",
        )
        return [r["text"] for r in results if r.get("text")]

    def get_statute_backfill(self, chunk_ids: list[str]) -> list[dict]:
        """Resolve the statute text that a set of source chunks CITES.

        Two-hop deterministic traversal:
            (source Chunk)-[:CITES]->(Statute stub)-[:DEFINED_BY]->(statute Chunk)

        Given the top-N most-relevant chunks from a vector search, returns the
        actual statute-text chunks they cite — the "statute backfill" that lets
        the agent ground a WPAM/guide passage in the underlying statute without
        a separate list_sections + get_section round-trip.

        Each returned row carries `source_chunk_id` (which retrieved chunk cited
        this statute) so the caller can rank/cap by source relevance. A single
        statute chunk may appear once per citing source chunk; the caller
        dedups. Returns statute chunk fields shaped like vector_search results
        so they slot into the same citation-card pipeline.
        """
        if not chunk_ids:
            return []
        results = self.query(
            "UNWIND $chunk_ids AS cid "
            "MATCH (src:Chunk {id: cid})-[:CITES]->(stub:Statute)-[:DEFINED_BY]->(sc:Chunk) "
            "OPTIONAL MATCH (sc)-[:EXTRACTED_FROM]->(parent) "
            "RETURN cid AS source_chunk_id, stub.id AS stub_id, "
            "sc.id AS chunk_id, sc.text AS text, sc.doc_id AS doc_id, "
            "sc.source_url AS source_url, sc.s3_key AS s3_key, "
            "sc.start_page AS start_page, sc.end_page AS end_page, "
            "sc.heading AS heading, sc.subheading AS subheading, "
            "parent.authority_level AS authority_level",
            {"chunk_ids": chunk_ids},
            query_name="get_statute_backfill",
        )
        return results

    def get_cases_for_subsections(self, subsection_ids: list[str], limit: int = 15) -> list[dict]:
        """Find CaseLaw nodes connected to statute subsections via CITES edges.

        Returns cases ordered so that modern-format citations (YYYY WI ...)
        appear first by year descending, followed by older-format citations.
        """
        if not subsection_ids:
            return []
        results = self.query(
            "MATCH (s)-[:CITES]-(n:CaseLaw) "
            "WHERE s.id IN $ids "
            "RETURN DISTINCT n.id AS id, n.title AS title, "
            "n.citation AS citation, n.doc_type AS doc_type, "
            "n.authority_level AS authority_level, "
            "n.source_url AS source_url, labels(n) AS labels",
            {"ids": subsection_ids},
            query_name="get_cases_for_subsections",
        )
        return results[:limit]

    def resolve_case_citations(self, citations: list[str]) -> list[dict]:
        """Look up CaseLaw nodes by their normalized citation strings."""
        if not citations:
            return []
        results = self.query(
            "MATCH (n:CaseLaw) "
            "WHERE n.citation IN $citations "
            "RETURN n.id AS id, n.title AS title, n.citation AS citation, "
            "n.doc_type AS doc_type, n.authority_level AS authority_level, "
            "n.source_url AS source_url, labels(n) AS labels",
            {"citations": citations},
            query_name="resolve_case_citations",
        )
        return results

    _CASE_SEARCH_STOP_WORDS = frozenset(
        {"v", "vs", "of", "the", "in", "re", "ex", "rel", "et", "al", "state"}
    )

    def find_case_law(
        self, search_text: str, statute_id: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Find CaseLaw nodes by title, optionally scoped to a statute.

        Splits search_text into significant terms (>2 chars, excluding common
        legal connectors) and requires ALL terms appear in the title. This
        handles variations like "Markarian v City" matching
        "State Ex Rel. Markarian v. City of Cudahy".
        """
        terms = [
            w.lower().rstrip(".,;:")
            for w in search_text.split()
            if len(w) > 2 and w.lower().rstrip(".,;:") not in self._CASE_SEARCH_STOP_WORDS
        ]
        if not terms:
            terms = [search_text.lower()]

        # Neptune Analytics doesn't support ALL() predicate, so build AND chain
        params: dict[str, Any] = {}
        where_parts: list[str] = []
        for i, term in enumerate(terms):
            key = f"term_{i}"
            params[key] = term
            where_parts.append(f"toLower(n.title) CONTAINS ${key}")
        where_clause = " AND ".join(where_parts)

        if statute_id:
            params["statute_id"] = statute_id
            results = self.query(
                f"MATCH (s {{id: $statute_id}})-[:CITES]-(n:CaseLaw) "
                f"WHERE {where_clause} "
                "RETURN n.id AS id, n.title AS title, n.citation AS citation, "
                "n.doc_type AS doc_type, n.authority_level AS authority_level, "
                "n.source_url AS source_url, labels(n) AS labels "
                f"LIMIT {int(limit)}",
                params,
                query_name="find_case_law",
            )
        else:
            results = self.query(
                "MATCH (n:CaseLaw) "
                f"WHERE {where_clause} "
                "RETURN n.id AS id, n.title AS title, n.citation AS citation, "
                "n.doc_type AS doc_type, n.authority_level AS authority_level, "
                "n.source_url AS source_url, labels(n) AS labels "
                f"LIMIT {int(limit)}",
                params,
                query_name="find_case_law",
            )
        return results
