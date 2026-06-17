"""One-off migration: remove N.W.2d/3d parallel reporter CaseLaw nodes.

These are duplicates of the canonical Wis. 2d nodes for the same cases.
After running, only Wis. 2d (and WI/WI App/Federal) citations remain.

Usage:
    AWS_REGION=us-east-1 AWS_PROFILE=widor python scripts/graphrag/remove_nw_reporter_nodes.py \
        --graph-id g-ndvl4j73v4

    # Dry-run (default): just counts and lists nodes to delete
    AWS_REGION=us-east-1 AWS_PROFILE=widor python scripts/graphrag/remove_nw_reporter_nodes.py \
        --graph-id g-ndvl4j73v4 --dry-run
"""

import argparse
import json
import logging
import os
import time

import boto3

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None):
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters

    for attempt in range(8):
        try:
            resp = client.execute_query(**kwargs)
            payload = resp.get("payload")
            if payload is None:
                return {}
            return json.loads(payload.read())
        except Exception as e:
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


def main():
    parser = argparse.ArgumentParser(description="Remove N.W.2d/3d parallel reporter CaseLaw nodes")
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    client = boto3.client(
        "neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )

    result = execute_query(
        client,
        args.graph_id,
        "MATCH (n:CaseLaw) WHERE n.citation CONTAINS 'N.W.2d' OR n.citation CONTAINS 'N.W.3d' "
        "RETURN n.id AS id, n.citation AS citation, n.title AS title",
    )
    nodes = result.get("results", [])
    logger.info(f"Found {len(nodes)} N.W.2d/3d CaseLaw nodes to remove")

    if not nodes:
        logger.info("Nothing to do.")
        return

    for node in nodes[:10]:
        logger.info(f"  {node['id']}: {node.get('citation', '')}")
    if len(nodes) > 10:
        logger.info(f"  ... and {len(nodes) - 10} more")

    if args.dry_run:
        logger.info("Dry-run mode — no deletions performed.")
        return

    batch_size = 50
    deleted = 0
    for i in range(0, len(nodes), batch_size):
        batch_ids = [n["id"] for n in nodes[i : i + batch_size]]
        execute_query(
            client,
            args.graph_id,
            "UNWIND $ids AS nid MATCH (n:CaseLaw {id: nid}) DETACH DELETE n",
            {"ids": batch_ids},
        )
        deleted += len(batch_ids)
        logger.info(f"  Deleted {deleted}/{len(nodes)}")

    logger.info(f"Done. Removed {deleted} N.W.2d/3d parallel reporter nodes.")


if __name__ == "__main__":
    main()
