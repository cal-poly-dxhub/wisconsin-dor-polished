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
        compact[:LOG_MAX_QUERY_CHARS]
        + f"...[truncated {len(compact) - LOG_MAX_QUERY_CHARS} chars]"
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
                    wait = min(30, 2 ** attempt)
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
        results = self.query(
            f"CALL neptune.algo.vectors.topKByEmbedding({embedding_literal}, {{topK: {top_k}}}) "
            "YIELD node, score "
            "RETURN node.id AS chunk_id, node.text AS text, node.doc_id AS doc_id, "
            "node.source_url AS source_url, node.s3_key AS s3_key, "
            "node.start_page AS start_page, node.end_page AS end_page, "
            "node.heading AS heading, node.subheading AS subheading, score",
            query_name="vector_search",
        )
        return results

    def get_document(self, doc_id: str) -> dict | None:
        """Fetch a document node by ID."""
        results = self.query(
            "MATCH (d {id: $id}) "
            "RETURN d.id AS id, d.title AS title, d.summary AS summary, "
            "d.source_url AS source_url, d.source_key AS s3_key, "
            "d.doc_type AS doc_type, "
            "d.authority_level AS authority_level, labels(d) AS labels",
            {"id": doc_id},
            query_name="get_document",
        )
        return results[0] if results else None

    def get_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
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
        else:
            if direction == "outgoing":
                pattern = "MATCH (d {id: $id})-[r]->(n)"
            elif direction == "incoming":
                pattern = "MATCH (d {id: $id})<-[r]-(n)"
            else:
                pattern = "MATCH (d {id: $id})-[r]-(n)"

        results = self.query(
            f"{pattern} "
            "RETURN type(r) AS relationship, n.id AS id, n.title AS title, "
            "n.summary AS summary, n.source_url AS source_url, "
            "n.doc_type AS doc_type, labels(n) AS labels",
            {"id": node_id},
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
        """Get all chunks for a document."""
        results = self.query(
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
            "RETURN c.id AS chunk_id, c.text AS text, c.source_url AS source_url, "
            "c.chunk_index AS chunk_index "
            "ORDER BY c.chunk_index",
            {"doc_id": doc_id},
            query_name="get_chunks_for_doc",
        )
        return results
