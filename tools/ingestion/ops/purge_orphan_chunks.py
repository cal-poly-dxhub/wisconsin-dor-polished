"""Purge orphan chunks left over from prior loads.

Chunk IDs are deterministic: `{doc_id}_chunk_{i:04d}` where `i` is the
0-based index in the embedded JSON's `chunks` list. After a re-ingest,
the new extract/embed may produce fewer chunks per doc than the prior
run, so any Chunk node in Neptune whose numeric suffix is >= N (current
chunk count) is an orphan from the prior load.

The script:
  1. Loads current manifest from s3://<work-bucket>/extracted/manifest.json
     (or enumerates embedded/*.json if the manifest is stale).
  2. For each doc, reads embedded JSON to count N = len(chunks).
  3. Queries Neptune for all Chunk nodes belonging to doc_id.
  4. Computes the orphan set (indexes >= N, OR chunk_ids that don't match
     the deterministic format for the current doc_id).
  5. Batch-DETACH DELETE orphans in groups of 1000 to stay under Neptune's
     query timeout / memory limits.

Run with --dry-run (default) to preview, --apply to execute.

Usage:
    python tools/ingestion/ops/purge_orphan_chunks.py \\
        --work-bucket wis-work-bucket-c8e69250 \\
        --graph-id g-ndvl4j73v4
    # add --apply to actually delete
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REGION = os.environ.get("AWS_REGION", "us-east-1")
s3 = boto3.client("s3", region_name=REGION)
neptune = boto3.client("neptune-graph", region_name=REGION)

CHUNK_ID_RE = re.compile(r"^(?P<doc_id>.+)_chunk_(?P<idx>\d{4})$")
DELETE_BATCH_SIZE = 1000


def load_embedded_chunk_counts(work_bucket: str) -> dict[str, int]:
    """Map doc_id -> count of chunks in embedded JSON."""
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=work_bucket, Prefix="embedded/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])

    logger.info(f"Found {len(keys)} embedded JSONs; fetching chunk counts...")

    def fetch(key: str) -> tuple[str, int]:
        body = s3.get_object(Bucket=work_bucket, Key=key)["Body"].read()
        doc = json.loads(body)
        return doc["doc_id"], len(doc.get("chunks", []))

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(fetch, k) for k in keys]
        for i, fut in enumerate(as_completed(futures), start=1):
            doc_id, n = fut.result()
            counts[doc_id] = n
            if i % 500 == 0 or i == len(keys):
                logger.info(f"  Loaded {i}/{len(keys)} embedded JSONs")
    return counts


def query_neptune(graph_id: str, cypher: str) -> list[dict]:
    resp = neptune.execute_query(
        graphIdentifier=graph_id, queryString=cypher, language="OPEN_CYPHER"
    )
    return json.loads(resp["payload"].read()).get("results", [])


def chunks_per_doc_in_neptune(graph_id: str) -> dict[str, list[str]]:
    """Returns doc_id -> list of chunk_ids currently in Neptune."""
    logger.info("Querying Neptune for all chunk_ids (paginated)...")
    all_rows: list[dict] = []
    # Paginate via OFFSET because Neptune has response-size limits.
    page_size = 10000
    offset = 0
    while True:
        rows = query_neptune(
            graph_id,
            f"MATCH (c:Chunk) RETURN c.id AS id, c.doc_id AS doc_id "
            f"ORDER BY c.id SKIP {offset} LIMIT {page_size}",
        )
        if not rows:
            break
        all_rows.extend(rows)
        logger.info(f"  Fetched {len(all_rows)} chunks so far (offset={offset})")
        if len(rows) < page_size:
            break
        offset += page_size

    by_doc: dict[str, list[str]] = {}
    for row in all_rows:
        did = row.get("doc_id")
        cid = row.get("id")
        if did and cid:
            by_doc.setdefault(did, []).append(cid)
    logger.info(f"Total chunks in Neptune: {len(all_rows)} across {len(by_doc)} docs")
    return by_doc


def compute_orphans(
    neptune_chunks: dict[str, list[str]], embedded_counts: dict[str, int]
) -> list[str]:
    """Return chunk_ids that are orphans (no matching embedded-JSON slot)."""
    orphans: list[str] = []
    for doc_id, chunk_ids in neptune_chunks.items():
        valid_n = embedded_counts.get(doc_id, 0)
        valid_ids = {f"{doc_id}_chunk_{i:04d}" for i in range(valid_n)}
        for cid in chunk_ids:
            # Anything not in valid_ids is orphan:
            #   - indexes >= N (doc shrunk)
            #   - non-matching format (e.g., old _final_ style from pdfChunker)
            #   - chunks for docs no longer in embedded/
            if cid not in valid_ids:
                orphans.append(cid)
    return orphans


def delete_orphans(graph_id: str, orphan_ids: list[str]) -> int:
    """Batch DETACH DELETE orphan chunks. Returns count deleted."""
    total = 0
    for i in range(0, len(orphan_ids), DELETE_BATCH_SIZE):
        batch = orphan_ids[i : i + DELETE_BATCH_SIZE]
        # UNWIND + MATCH + DETACH DELETE — idempotent, handles missing ids.
        cypher = (
            "UNWIND $ids AS cid "
            "MATCH (c:Chunk {id: cid}) "
            "DETACH DELETE c"
        )
        resp = neptune.execute_query(
            graphIdentifier=graph_id,
            queryString=cypher,
            parameters={"ids": batch},
            language="OPEN_CYPHER",
        )
        # Payload contains no useful count for DELETE; track by batch size.
        total += len(batch)
        logger.info(f"  Deleted batch: {len(batch)} chunks (cumulative {total}/{len(orphan_ids)})")
        # Light pacing to avoid throttling.
        time.sleep(0.2)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge orphan chunks left over from prior loads")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete orphans (default is dry-run: print stats only)",
    )
    args = parser.parse_args()

    logger.info(f"Mode: {'APPLY (destructive)' if args.apply else 'DRY RUN'}")
    logger.info(f"Work bucket: {args.work_bucket}")
    logger.info(f"Graph:       {args.graph_id}")

    embedded_counts = load_embedded_chunk_counts(args.work_bucket)
    neptune_chunks = chunks_per_doc_in_neptune(args.graph_id)

    orphans = compute_orphans(neptune_chunks, embedded_counts)
    logger.info("\n=== ORPHAN AUDIT ===")
    logger.info(f"Orphan chunks to delete: {len(orphans)}")

    # Per-doc breakdown for the top offenders
    from collections import Counter

    per_doc: Counter[str] = Counter()
    for oid in orphans:
        m = CHUNK_ID_RE.match(oid)
        if m:
            per_doc[m.group("doc_id")] += 1
        else:
            per_doc[f"(unparseable) {oid}"] += 1

    top = per_doc.most_common(20)
    if top:
        logger.info("Top docs with orphan chunks:")
        for doc_id, n in top:
            current = embedded_counts.get(doc_id, 0)
            total_in_neptune = len(neptune_chunks.get(doc_id, []))
            logger.info(f"  {doc_id}: {n} orphans (embedded={current}, neptune={total_in_neptune})")

    if not args.apply:
        logger.info("\nDry-run mode. Re-run with --apply to delete these chunks.")
        return

    if not orphans:
        logger.info("No orphans to delete.")
        return

    logger.info(f"\nDeleting {len(orphans)} orphan chunks in batches of {DELETE_BATCH_SIZE}...")
    delete_orphans(args.graph_id, orphans)
    logger.info("Purge complete.")


if __name__ == "__main__":
    main()
