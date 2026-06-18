"""One-shot: remove cached case-law extracts so they re-process with annotation enrichment.

Before the annotation pipeline was added, case-law extracts were either:

  - LLM-generated summaries of metadata-stub JSON blobs (placeholder "note:
    Full opinion text not yet downloaded" text fed to Claude for
    title/summary classification), producing content like
    "2009 WI App 159 - Wisconsin Court of Appeals Case (Stub)".

  - LLM-generated summaries of full .txt opinions that are missing the
    Wisconsin-Statutes-annotated editorial context entirely.

Both are replaced by the `process_case_law_document` path, which:
  - Extracts annotation paragraphs directly from the citing statute PDFs.
  - Uses the real case name (parsed from the annotation).
  - Appends opinion chunks when the .txt exists, but annotations come first.

This script deletes the stale cache entries so `extract.py` and `embed.py`
re-process them on the next run. Nothing is modified in the raw bucket; the
remediation is purely cache invalidation.

Usage:
    # Preview what would be deleted:
    python scripts/graphrag/remediate_case_law.py \\
        --work-bucket wis-work-bucket-c8e69250 --dry-run

    # Execute deletions:
    python scripts/graphrag/remediate_case_law.py \\
        --work-bucket wis-work-bucket-c8e69250

After this runs, re-run:
    python scripts/graphrag/extract.py --raw-bucket ... --work-bucket ...
    python scripts/graphrag/embed.py --work-bucket ...
    python scripts/graphrag/load.py --work-bucket ... --graph-id ...
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import boto3


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


CASE_LAW_PREFIX = "case-law-"
S3_DELETE_BATCH = 1000  # S3 delete_objects cap


def list_case_law_keys(s3, bucket: str, prefix: str) -> list[str]:
    """Return all keys under {prefix}case-law-* (ignoring manifest)."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}{CASE_LAW_PREFIX}"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/manifest.json"):
                continue
            keys.append(key)
    return keys


def delete_keys(s3, bucket: str, keys: list[str]) -> int:
    """Delete keys in batches of S3_DELETE_BATCH. Returns number actually deleted."""
    deleted = 0
    for i in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[i : i + S3_DELETE_BATCH]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        deleted += len(batch)
        logger.info(f"  Deleted batch {i // S3_DELETE_BATCH + 1} ({len(batch)} keys)")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-bucket", required=True, help="Work bucket (extract + embed caches)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    extract_keys = list_case_law_keys(s3, args.work_bucket, "extracted/")
    embed_keys = list_case_law_keys(s3, args.work_bucket, "embedded/")

    logger.info(f"Found {len(extract_keys)} case-law extract cache entries")
    logger.info(f"Found {len(embed_keys)} case-law embed cache entries")

    if extract_keys:
        logger.info(f"Sample extract keys: {extract_keys[:3]}")
    if embed_keys:
        logger.info(f"Sample embed keys: {embed_keys[:3]}")

    if args.dry_run:
        logger.info("--dry-run: no deletions performed.")
        return 0

    if not extract_keys and not embed_keys:
        logger.info("Nothing to remediate.")
        return 0

    total = 0
    if extract_keys:
        logger.info(f"Deleting {len(extract_keys)} extract cache entries...")
        total += delete_keys(s3, args.work_bucket, extract_keys)
    if embed_keys:
        logger.info(f"Deleting {len(embed_keys)} embed cache entries...")
        total += delete_keys(s3, args.work_bucket, embed_keys)

    logger.info(f"Remediation complete: {total} cache entries deleted.")
    logger.info(
        "Next steps:\n"
        "  1. Run extract.py — case-law docs will re-process with annotations\n"
        "  2. Run embed.py — regenerates embeddings from annotation text\n"
        "  3. Run load.py — upserts Document/Chunk nodes in Neptune (idempotent)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
