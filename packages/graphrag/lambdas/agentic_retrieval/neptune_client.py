"""
Neptune Analytics client for OpenCypher queries and vector search.
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)


class NeptuneClient:
    """Client for Neptune Analytics OpenCypher + vector search."""

    def __init__(self, graph_id: str | None = None, region: str = "us-west-2"):
        self.graph_id = graph_id or os.environ.get("NEPTUNE_GRAPH_ID", "")
        self.client = boto3.client("neptune-graph", region_name=region)

    def query(self, cypher: str, parameters: dict | None = None, max_retries: int = 3) -> list[dict]:
        """Execute an OpenCypher query with retries."""
        kwargs = {
            "graphIdentifier": self.graph_id,
            "language": "OPEN_CYPHER",
            "queryString": cypher,
        }
        if parameters:
            kwargs["parameters"] = parameters

        for attempt in range(max_retries):
            try:
                response = self.client.execute_query(**kwargs)
                return response.get("results", [])
            except Exception as e:
                if attempt < max_retries - 1 and "Throttling" in str(e):
                    wait = min(30, 2 ** attempt)
                    logger.warning(f"Neptune throttled, retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise

        return []

    def vector_search(self, embedding: list[float], top_k: int = 10) -> list[dict]:
        """Search for similar chunks using Neptune's vector index."""
        results = self.query(
            "CALL neptune.algo.vectors.topKByEmbedding($embedding, {topK: $topK}) "
            "YIELD node, score "
            "RETURN node.id AS chunk_id, node.text AS text, node.doc_id AS doc_id, "
            "node.source_url AS source_url, node.s3_key AS s3_key, "
            "node.start_page AS start_page, node.end_page AS end_page, score",
            {"embedding": embedding, "topK": top_k},
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
        )
        return results[0] if results else None

    def get_neighbors(self, node_id: str, edge_types: list[str] | None = None, direction: str = "both") -> list[dict]:
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
        )
        return results

    def get_authority_chain(self, node_id: str, max_depth: int = 5) -> list[dict]:
        """Trace the governance hierarchy from a node up to the root framework."""
        results = self.query(
            "MATCH p=(d {id: $id})-[:PART_OF|BELONGS_TO|DERIVED_FROM*1..$depth]->(root) "
            "WHERE NOT (root)-[:PART_OF|BELONGS_TO|DERIVED_FROM]->() "
            "UNWIND nodes(p) AS node "
            "RETURN DISTINCT node.id AS id, node.title AS title, "
            "node.authority_level AS authority_level, labels(node) AS labels",
            {"id": node_id, "depth": max_depth},
        )
        return results

    def list_framework_docs(self, framework_id: str) -> list[dict]:
        """List all documents belonging to a framework."""
        results = self.query(
            "MATCH (d)-[:BELONGS_TO]->(f:Framework {id: $fw_id}) "
            "RETURN d.id AS id, d.title AS title, d.doc_type AS doc_type, "
            "d.source_url AS source_url, labels(d) AS labels",
            {"fw_id": framework_id},
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
        )
        return results
