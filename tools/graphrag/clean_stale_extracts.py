"""One-shot: delete stale work artifacts whose raw bucket source is outdated.

When the source data changes (e.g., a colleague replaces metadata stubs with
full-text opinions), old extracted/embedded JSONs can stick around and get
loaded into the graph, polluting retrieval.

This script:
  1. Lists all raw docs (from raw/{doc_id}/...).
  2. Lists work artifacts (from extracted/ and embedded/).
  3. Deletes work artifacts whose doc_id is missing from raw, or whose stored
     s3_key points at a different raw object than the current raw doc.

Usage:
    python scripts/graphrag/clean_stale_extracts.py \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --work-bucket wis-work-bucket-c8e69250 \
        --dry-run

Run without --dry-run to actually delete.
"""

import argparse
import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


S3_DELETE_BATCH = 1000  # S3 delete_objects limit
WORK_PREFIXES = ("extracted/", "embedded/")


def source_key_rank(key: str) -> tuple[int, str]:
    """Sort raw objects so full-text replacements win over old JSON stubs."""
    if key.endswith(".txt"):
        return (0, key)
    if key.endswith(".pdf"):
        return (1, key)
    if key.endswith(".json"):
        return (2, key)
    return (3, key)


def list_raw_source_keys(s3, bucket: str) -> dict[str, str]:
    """Return current raw source key by doc_id."""
    keys_by_doc_id: dict[str, list[str]] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json"):
                continue
            parts = key.replace("raw/", "", 1).split("/")
            if len(parts) >= 2 and parts[0]:
                keys_by_doc_id.setdefault(parts[0], []).append(key)
    return {
        doc_id: sorted(keys, key=source_key_rank)[0]
        for doc_id, keys in keys_by_doc_id.items()
    }


def list_raw_doc_ids(s3, bucket: str) -> set[str]:
    """Return the set of raw doc_ids (top-level folder names under raw/)."""
    return set(list_raw_source_keys(s3, bucket))


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


def artifact_source_key(doc: dict) -> str:
    """Return the raw S3 key recorded in an extracted/embedded artifact."""
    if doc.get("s3_key"):
        return doc["s3_key"]
    for chunk in doc.get("chunks", []):
        source = chunk.get("metadata", {}).get("source", "")
        if source:
            return source
    return ""


def list_artifact_source_keys(s3, bucket: str, prefix: str) -> dict[str, str]:
    """Return stored raw source key by doc_id for a work artifact prefix."""
    source_keys: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json") or key == f"{prefix}manifest.json":
                continue
            doc_id = key.removeprefix(prefix).removesuffix(".json")
            data = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            source_keys[doc_id] = artifact_source_key(data)
    return source_keys


def find_stale_artifacts(
    raw_source_keys: dict[str, str],
    artifact_source_keys: dict[str, str],
) -> dict[str, str]:
    """Return stale artifact doc_ids mapped to a concise reason."""
    stale: dict[str, str] = {}
    for doc_id, artifact_key in artifact_source_keys.items():
        raw_key = raw_source_keys.get(doc_id)
        if not raw_key:
            stale[doc_id] = "missing-raw-doc"
        elif artifact_key and artifact_key != raw_key:
            stale[doc_id] = "source-key-mismatch"
        elif not artifact_key:
            stale[doc_id] = "missing-artifact-source-key"
    return stale


def delete_artifacts(s3, bucket: str, prefix: str, stale_ids: set[str]) -> None:
    """Delete {prefix}{doc_id}.json for each stale doc_id in batches."""
    if not stale_ids:
        return

    keys = [f"{prefix}{doc_id}.json" for doc_id in stale_ids]
    for i in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[i : i + S3_DELETE_BATCH]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        logger.info(
            f"Deleted {len(batch)} stale {prefix} artifacts "
            f"(batch {i // S3_DELETE_BATCH + 1})"
        )


def delete_stale_extracts(s3, bucket: str, stale_ids: set[str]) -> None:
    """Delete extracted/{doc_id}.json for each stale doc_id in batches."""
    delete_artifacts(s3, bucket, "extracted/", stale_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete stale extracted JSONs")
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    raw_source_keys = list_raw_source_keys(s3, args.raw_bucket)
    stale_by_prefix: dict[str, dict[str, str]] = {}
    for prefix in WORK_PREFIXES:
        artifact_source_keys = list_artifact_source_keys(s3, args.work_bucket, prefix)
        stale_by_prefix[prefix] = find_stale_artifacts(raw_source_keys, artifact_source_keys)

    logger.info(f"Raw doc_ids: {len(raw_source_keys)}")
    for prefix, stale in stale_by_prefix.items():
        logger.info(f"Stale {prefix} artifacts (to delete): {len(stale)}")

    if not any(stale_by_prefix.values()):
        logger.info("Nothing to clean up.")
        return 0

    for prefix, stale in stale_by_prefix.items():
        if not stale:
            continue
        sample = [
            {"doc_id": doc_id, "reason": stale[doc_id]}
            for doc_id in sorted(stale)[:10]
        ]
        logger.info(f"Sample stale {prefix} artifacts: {sample}")

    if args.dry_run:
        logger.info("--dry-run set; no deletions performed.")
        return 0

    for prefix, stale in stale_by_prefix.items():
        delete_artifacts(s3, args.work_bucket, prefix, set(stale))
    logger.info("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
