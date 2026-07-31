"""
Cleanup orphan chunks in Neptune that weren't overwritten during re-ingestion.

When extract produces fewer chunks than previously existed (e.g., WPAM 2025
went from 2226 → 483 chunks after chunker improvements), the load script's
MERGE only updates chunks that still exist by ID. Chunks with IDs beyond the
new range (e.g., _chunk_0483 through _chunk_2225) remain as orphans with stale
text, bad headings, and outdated embeddings.

This script:
1. Reads the embedded JSONs from the work bucket to find the expected chunk
   count per document.
2. Queries Neptune for the actual chunk count per document.
3. Deletes orphan chunks (those beyond the expected range).

Usage:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 python tools/ingestion/ops/cleanup_orphan_chunks.py \
        --work-bucket wis-work-bucket-c8e69250 \
        --graph-id g-ndvl4j73v4 \
        [--dry-run]
"""

import argparse
import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")


def get_neptune_client(graph_id: str):
    return boto3.client(
        "neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1")
    ), graph_id


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None) -> dict:
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
                "Throttling" in name or "Unprocessable" in name or "resubmit" in msg.lower()
            )
            if is_throttle and attempt < 7:
                wait = min(2**attempt, 30)
                logger.warning(f"  Throttled ({name}), retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise

    return {}


def get_expected_chunk_counts(work_bucket: str, source_filter: str | None = None) -> dict[str, int]:
    """Read extracted JSONs and return {doc_id: chunk_count}.

    Uses extracted/ (smaller, no embeddings) rather than embedded/ for speed.
    Only downloads files matching source_filter prefix if given.
    """
    prefix = "extracted/"
    if source_filter:
        prefix = f"extracted/{source_filter}"
    logger.info(f"Reading extracted JSONs from s3://{work_bucket}/{prefix}...")
    paginator = s3.get_paginator("list_objects_v2")
    counts = {}
    total_files = 0

    for page in paginator.paginate(Bucket=work_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            total_files += 1
            resp = s3.get_object(Bucket=work_bucket, Key=key)
            data = json.loads(resp["Body"].read())
            doc_id = data.get("doc_id", "")
            chunks = data.get("chunks", [])
            if doc_id and chunks:
                counts[doc_id] = len(chunks)
            if total_files % 200 == 0:
                logger.info(f"  Read {total_files} files...")

    logger.info(f"  Read {total_files} extracted JSONs, {len(counts)} with chunks")
    return counts


def get_actual_chunk_counts(client, graph_id: str) -> dict[str, int]:
    """Query Neptune for actual chunk count per document."""
    logger.info("Querying Neptune for actual chunk counts per document...")
    result = execute_query(
        client,
        graph_id,
        "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d) RETURN d.id AS doc_id, count(c) AS chunk_count",
    )
    counts = {}
    for row in result.get("results", []):
        counts[row["doc_id"]] = row["chunk_count"]
    logger.info(f"  Found {len(counts)} documents with chunks in Neptune")
    return counts


def delete_orphan_chunks(
    client, graph_id: str, doc_id: str, expected_count: int, actual_count: int, dry_run: bool
) -> int:
    """Delete chunks beyond the expected range for a document."""
    orphan_count = actual_count - expected_count
    if orphan_count <= 0:
        return 0

    # Build list of orphan chunk IDs
    orphan_ids = [f"{doc_id}_chunk_{i:04d}" for i in range(expected_count, actual_count)]

    if dry_run:
        logger.info(
            f"  [DRY RUN] Would delete {orphan_count} orphan chunks for {doc_id} "
            f"(IDs _chunk_{expected_count:04d} through _chunk_{actual_count - 1:04d})"
        )
        return orphan_count

    # Delete in batches of 100
    batch_size = 100
    deleted = 0
    for i in range(0, len(orphan_ids), batch_size):
        batch = orphan_ids[i : i + batch_size]
        execute_query(
            client,
            graph_id,
            "UNWIND $ids AS cid MATCH (c:Chunk {id: cid}) DETACH DELETE c",
            {"ids": batch},
        )
        deleted += len(batch)
        if deleted % 500 == 0:
            logger.info(f"    Deleted {deleted}/{orphan_count} orphans for {doc_id}...")

    return deleted


def main():
    parser = argparse.ArgumentParser(description="Delete orphan chunks from Neptune")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted without deleting"
    )
    parser.add_argument("--source-filter", help="Only process doc_ids matching this prefix")
    args = parser.parse_args()

    client, graph_id = get_neptune_client(args.graph_id)

    expected = get_expected_chunk_counts(args.work_bucket, args.source_filter)
    actual = get_actual_chunk_counts(client, graph_id)

    # Find mismatches
    orphan_docs = []
    for doc_id, actual_count in sorted(actual.items()):
        if args.source_filter and not doc_id.startswith(args.source_filter):
            continue
        expected_count = expected.get(doc_id, 0)
        if actual_count > expected_count and expected_count > 0:
            orphan_docs.append((doc_id, expected_count, actual_count))

    if not orphan_docs:
        logger.info("No orphan chunks found — Neptune matches work bucket.")
        return

    total_orphans = sum(actual - exp for _, exp, actual in orphan_docs)
    logger.info(
        f"\nFound {len(orphan_docs)} documents with orphan chunks ({total_orphans} total orphans):"
    )
    for doc_id, exp, act in orphan_docs:
        logger.info(f"  {doc_id}: expected={exp}, actual={act}, orphans={act - exp}")

    if args.dry_run:
        logger.info(
            f"\n[DRY RUN] Would delete {total_orphans} orphan chunks across {len(orphan_docs)} documents."
        )
    else:
        logger.info(f"\nDeleting {total_orphans} orphan chunks...")

    total_deleted = 0
    for doc_id, expected_count, actual_count in orphan_docs:
        deleted = delete_orphan_chunks(
            client, graph_id, doc_id, expected_count, actual_count, args.dry_run
        )
        total_deleted += deleted

    logger.info(
        f"\n{'[DRY RUN] ' if args.dry_run else ''}Done. Deleted {total_deleted} orphan chunks."
    )

    if not args.dry_run:
        logger.info("\nNext steps:")
        logger.info("  1. Re-run load from Phase 9 (vectors) to re-embed the updated chunks:")
        logger.info(
            "     ./tools/ingestion/scripts/run_fargate.sh load --start-phase 9 --stop-after-phase 9"
        )
        logger.info("  2. Optionally re-run Phase 11 (semantic edges) if desired.")


if __name__ == "__main__":
    main()
