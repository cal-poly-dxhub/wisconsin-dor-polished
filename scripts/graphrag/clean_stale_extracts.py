"""One-shot: delete stale extracted/*.json files whose raw bucket entry no longer exists.

When the source data changes (e.g., a colleague replaces metadata stubs with
full-text opinions under different doc IDs), the old extract JSONs stick
around and would get embedded + loaded into the graph, polluting retrieval.

This script:
  1. Lists all raw doc_ids (from raw/{doc_id}/...).
  2. Lists all extracted doc_ids (from extracted/{doc_id}.json).
  3. Deletes extracted JSONs whose doc_id is not in the raw set.

Usage:
    python scripts/graphrag/clean_stale_extracts.py \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --work-bucket wis-work-bucket-c8e69250 \
        --dry-run

Run without --dry-run to actually delete.
"""

import argparse
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


S3_DELETE_BATCH = 1000  # S3 delete_objects limit


def list_raw_doc_ids(s3, bucket: str) -> set[str]:
    """Return the set of raw doc_ids (top-level folder names under raw/)."""
    ids: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json"):
                continue
            parts = key.replace("raw/", "", 1).split("/")
            if len(parts) >= 2 and parts[0]:
                ids.add(parts[0])
    return ids


def list_extracted_doc_ids(s3, bucket: str) -> set[str]:
    """Return the set of doc_ids present as extracted/{doc_id}.json."""
    ids: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="extracted/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != "extracted/manifest.json":
                doc_id = key.removeprefix("extracted/").removesuffix(".json")
                if doc_id:
                    ids.add(doc_id)
    return ids


def find_stale_extracts(raw_ids: set[str], extracted_ids: set[str]) -> set[str]:
    """Extracted doc_ids whose raw counterpart is missing."""
    return extracted_ids - raw_ids


def delete_stale_extracts(s3, bucket: str, stale_ids: set[str]) -> None:
    """Delete extracted/{doc_id}.json for each stale doc_id in batches."""
    if not stale_ids:
        return

    keys = [f"extracted/{doc_id}.json" for doc_id in stale_ids]
    for i in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[i : i + S3_DELETE_BATCH]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        logger.info(f"Deleted {len(batch)} stale extracts (batch {i // S3_DELETE_BATCH + 1})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete stale extracted JSONs")
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    raw_ids = list_raw_doc_ids(s3, args.raw_bucket)
    extracted_ids = list_extracted_doc_ids(s3, args.work_bucket)
    stale = find_stale_extracts(raw_ids, extracted_ids)

    logger.info(f"Raw doc_ids: {len(raw_ids)}")
    logger.info(f"Extracted doc_ids: {len(extracted_ids)}")
    logger.info(f"Stale (to delete): {len(stale)}")

    if not stale:
        logger.info("Nothing to clean up.")
        return 0

    sample = sorted(list(stale))[:10]
    logger.info(f"Sample stale IDs: {sample}")

    if args.dry_run:
        logger.info("--dry-run set; no deletions performed.")
        return 0

    delete_stale_extracts(s3, args.work_bucket, stale)
    logger.info("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
