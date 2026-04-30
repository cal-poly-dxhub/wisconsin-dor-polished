"""
Sync case-law CourtListener URLs from raw metadata into extracted/embedded artifacts.

This avoids re-running extraction and embedding for a URL-only change. It reads
case-law raw metadata from the raw bucket, then updates matching JSON artifacts
in the work bucket:

  - top-level document source_url
  - each chunk's metadata.source_url

Usage:
    python scripts/graphrag/sync_case_law_url_artifacts.py \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --work-bucket wis-work-bucket-c8e69250 \
        --profile widor \
        --dry-run \
        --output /tmp/case_law_artifact_url_sync.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from typing import Any

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def s3_client(profile: str | None):
    if profile:
        return boto3.Session(profile_name=profile).client("s3")
    return boto3.client("s3")


def doc_id_from_metadata_key(key: str) -> str:
    return key.split("/")[-2]


def list_case_law_metadata(s3, bucket: str, prefix: str = "raw/") -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "case-law-" in key and key.endswith(".txt.metadata.json"):
                keys.append(key)
    return sorted(keys)


def load_courtlistener_url_map(s3, bucket: str, prefix: str) -> dict[str, dict[str, str]]:
    url_map: dict[str, dict[str, str]] = {}
    for key in list_case_law_metadata(s3, bucket, prefix):
        obj = s3.get_object(Bucket=bucket, Key=key)
        meta = json.loads(obj["Body"].read())
        attrs = meta.get("metadataAttributes", {})
        source_url = attrs.get("source_url", "")
        if "courtlistener.com" not in source_url:
            continue
        doc_id = attrs.get("doc_id") or doc_id_from_metadata_key(key)
        url_map[doc_id] = {
            "source_url": source_url,
            "citation": attrs.get("citation", ""),
            "case_name": attrs.get("case_name", ""),
            "legis_url": attrs.get("legis_url", ""),
        }
    return url_map


def list_json_artifacts(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != "extracted/manifest.json":
                keys.append(key)
    return sorted(keys)


def update_artifact_doc(doc: dict[str, Any], source_url: str) -> tuple[bool, int, str]:
    old_top_level = doc.get("source_url", "")
    changed = old_top_level != source_url
    doc["source_url"] = source_url

    changed_chunks = 0
    for chunk in doc.get("chunks", []):
        meta = chunk.setdefault("metadata", {})
        if meta.get("source_url", "") != source_url:
            meta["source_url"] = source_url
            changed_chunks += 1
            changed = True
    return changed, changed_chunks, old_top_level


def sync_prefix(
    s3,
    work_bucket: str,
    prefix: str,
    url_map: dict[str, dict[str, str]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keys = list_json_artifacts(s3, work_bucket, prefix)
    logger.info("Found %s %s artifacts", len(keys), prefix)

    for i, key in enumerate(keys, start=1):
        doc_id = key.removeprefix(prefix).removesuffix(".json")
        mapping = url_map.get(doc_id)
        if not mapping:
            continue

        obj = s3.get_object(Bucket=work_bucket, Key=key)
        doc = json.loads(obj["Body"].read())
        changed, changed_chunks, old_source_url = update_artifact_doc(doc, mapping["source_url"])

        if changed and not dry_run:
            s3.put_object(
                Bucket=work_bucket,
                Key=key,
                Body=json.dumps(doc, default=str).encode("utf-8"),
                ContentType="application/json",
            )

        rows.append({
            "artifact_key": key,
            "doc_id": doc_id,
            "citation": mapping.get("citation", ""),
            "case_name": mapping.get("case_name", ""),
            "old_source_url": old_source_url,
            "new_source_url": mapping["source_url"],
            "changed": changed,
            "changed_chunks": changed_chunks,
            "status": "would-update" if dry_run and changed else ("updated" if changed else "already-current"),
        })

        if i % 500 == 0:
            logger.info("Scanned %s/%s %s artifacts", i, len(keys), prefix)

    return rows


def write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    fieldnames = [
        "artifact_key",
        "doc_id",
        "citation",
        "case_name",
        "old_source_url",
        "new_source_url",
        "changed",
        "changed_chunks",
        "status",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync case-law URLs into work artifacts")
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--raw-prefix", default="raw/")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    s3 = s3_client(args.profile)
    url_map = load_courtlistener_url_map(s3, args.raw_bucket, args.raw_prefix)
    logger.info("Loaded %s CourtListener case-law URL mappings", len(url_map))

    rows: list[dict[str, Any]] = []
    rows.extend(sync_prefix(s3, args.work_bucket, "extracted/", url_map, args.dry_run))
    rows.extend(sync_prefix(s3, args.work_bucket, "embedded/", url_map, args.dry_run))

    write_csv(args.output, rows)
    if args.output:
        logger.info("Wrote CSV audit to %s", args.output)

    changed = sum(1 for row in rows if row["changed"])
    chunks = sum(int(row["changed_chunks"]) for row in rows)
    logger.info(
        "Done: %s matching artifacts, %s %s, %s chunk metadata URLs %s",
        len(rows),
        changed,
        "would change" if args.dry_run else "changed",
        chunks,
        "would change" if args.dry_run else "changed",
    )


if __name__ == "__main__":
    main()
