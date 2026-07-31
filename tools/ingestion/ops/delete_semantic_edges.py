"""Delete LLM-classified semantic edges from the Neptune Analytics graph.

Removes the Phase 9 semantic edge layer — RELATED_TO, SUPPLEMENTS, SUPERSEDES,
CONFLICTS_WITH — that the load pipeline no longer produces. Deletes edges by
type in capped batches (DETACH DELETE / LIMIT) to stay within Neptune's
per-query memory budget, looping until no edges remain.

Expected removal on the production graph (per the phase-9 removal spec):
  RELATED_TO   13,745
  SUPPLEMENTS   2,272
  SUPERSEDES    1,800
  CONFLICTS_WITH   <a handful>
  -----------------------
  ~17,817 edges total

Run with --dry-run (default) to report per-type counts without deleting.
Run with --apply to actually delete.

Usage:
    python tools/ingestion/ops/delete_semantic_edges.py \\
        --graph-id g-ndvl4j73v4
    # add --apply to actually delete

    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 python \\
        tools/ingestion/ops/delete_semantic_edges.py --graph-id g-ndvl4j73v4 --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REGION = os.environ.get("AWS_REGION", "us-east-1")
neptune = boto3.client("neptune-graph", region_name=REGION)

SEMANTIC_EDGE_TYPES = ["RELATED_TO", "SUPPLEMENTS", "SUPERSEDES", "CONFLICTS_WITH"]
DELETE_LIMIT = 5000


def execute_query(graph_id: str, query: str) -> list[dict]:
    """Run an OpenCypher query and return its parsed ``results`` list.

    Mirrors the throttle-retry handling used by the load pipeline: Neptune
    Analytics signals throttling via ThrottlingException OR an
    UnprocessableException asking to resubmit the query.
    """
    for attempt in range(8):
        try:
            resp = neptune.execute_query(
                graphIdentifier=graph_id, queryString=query, language="OPEN_CYPHER"
            )
            payload = resp.get("payload")
            if payload is None:
                return []
            return json.loads(payload.read()).get("results", [])
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            msg = str(e)
            is_throttle = (
                "ThrottlingException" in name
                or "resubmit the query" in msg
                or "retry is suppressed" in msg.lower()
            )
            if is_throttle and attempt < 7:
                wait = min(60, 2**attempt)
                logger.warning(f"Neptune throttled ({name}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    return []


def count_edges(graph_id: str) -> dict[str, int]:
    """Return the current count of each semantic edge type in the graph."""
    counts: dict[str, int] = {}
    for edge_type in SEMANTIC_EDGE_TYPES:
        rows = execute_query(
            graph_id,
            f"MATCH ()-[r:{edge_type}]->() RETURN count(r) AS n",
        )
        counts[edge_type] = rows[0].get("n", 0) if rows else 0
    return counts


def delete_edges(graph_id: str) -> int:
    """DETACH DELETE all semantic edges in capped batches. Returns total deleted."""
    edge_pattern = "|".join(SEMANTIC_EDGE_TYPES)
    total = 0
    while True:
        rows = execute_query(
            graph_id,
            f"MATCH ()-[r:{edge_pattern}]->() "
            f"WITH r LIMIT {DELETE_LIMIT} "
            f"DELETE r RETURN count(r) AS deleted",
        )
        deleted = rows[0].get("deleted", 0) if rows else 0
        if deleted == 0:
            break
        total += deleted
        logger.info(f"  Deleted batch: {deleted} edges (cumulative {total})")
        # Light pacing to avoid throttling.
        time.sleep(0.2)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete LLM semantic edges from Neptune")
    parser.add_argument("--graph-id", required=True, help="Neptune Analytics graph identifier")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete edges (default is dry-run: print counts only)",
    )
    args = parser.parse_args()

    logger.info(f"Mode:  {'APPLY (destructive)' if args.apply else 'DRY RUN'}")
    logger.info(f"Graph: {args.graph_id}")

    logger.info("Counting existing semantic edges...")
    counts = count_edges(args.graph_id)
    total = sum(counts.values())
    logger.info("\n=== SEMANTIC EDGE AUDIT ===")
    for edge_type in SEMANTIC_EDGE_TYPES:
        logger.info(f"  {edge_type}: {counts[edge_type]}")
    logger.info(f"  TOTAL: {total}")

    if not args.apply:
        logger.info("\nDry-run mode. Re-run with --apply to delete these edges.")
        return

    if total == 0:
        logger.info("No semantic edges to delete.")
        return

    logger.info(f"\nDeleting semantic edges in batches of {DELETE_LIMIT}...")
    deleted = delete_edges(args.graph_id)
    logger.info(f"\nDeleted {deleted} semantic edges.")

    remaining = count_edges(args.graph_id)
    remaining_total = sum(remaining.values())
    if remaining_total:
        logger.warning(f"  {remaining_total} semantic edges still remain: {remaining}")
    else:
        logger.info("  Verified: 0 semantic edges remain.")


if __name__ == "__main__":
    main()
