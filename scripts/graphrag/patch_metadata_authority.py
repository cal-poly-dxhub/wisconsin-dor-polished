"""Patch authority_level in raw .metadata.json files to match each doc's framework.

scrape_documents.py historically stamped authority_level off-by-one (it
skipped case law when numbering categories), so news/advisory/faq metadata
in the raw bucket carry the wrong level. Because that value is explicit, a
re-extract/re-load would re-apply it. This script rewrites the value in
place to the framework's canonical level (single source of truth from
ingest_config.yaml), so the subsequent extract -> embed -> load propagates
the correct authority.

Idempotent and dry-run by default. Only the authority_level field changes;
framework_id / doc_type / source_url are left untouched.

Usage:
    AWS_REGION=us-east-1 AWS_PROFILE=wisco \
        python scripts/graphrag/patch_metadata_authority.py \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --config scripts/graphrag/ingest_config.yaml
    # add --apply to write; otherwise prints what it would change.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
import yaml
from botocore.config import Config

logger = logging.getLogger(__name__)


def framework_levels(config_path: str) -> dict[str, int]:
    config = yaml.safe_load(open(config_path))
    return {fw["id"]: fw["authority_level"] for fw in config["frameworks"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default: dry-run)."
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    # Pool large enough for the worker fan-out so connections aren't churned.
    s3 = boto3.client(
        "s3",
        region_name=args.region,
        config=Config(max_pool_connections=args.max_workers + 4),
    )
    levels = framework_levels(args.config)

    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=args.raw_bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".metadata.json"):
                keys.append(obj["Key"])
    logger.info("Found %d metadata files", len(keys))

    def inspect(key: str) -> tuple[str, int, int] | None:
        """Return (key, have, want) when a change is needed, else None."""
        body = s3.get_object(Bucket=args.raw_bucket, Key=key)["Body"].read()
        doc = json.loads(body)
        attrs = doc.get("metadataAttributes", {})
        fw = attrs.get("framework_id")
        want = levels.get(fw)
        if want is None:
            return None
        raw_have = attrs.get("authority_level")
        try:
            have = int(raw_have) if raw_have is not None else None
        except (TypeError, ValueError):
            have = None
        if have == want:
            return None
        if args.apply:
            # Preserve original type convention (metadata stores strings).
            attrs["authority_level"] = str(want)
            doc["metadataAttributes"] = attrs
            s3.put_object(
                Bucket=args.raw_bucket,
                Key=key,
                Body=json.dumps(doc, indent=1).encode("utf-8"),
                ContentType="application/json",
            )
        return (key, have if have is not None else -1, want)

    changed: list[tuple[str, int, int]] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        for result in pool.map(inspect, keys):
            if result:
                changed.append(result)

    verb = "Patched" if args.apply else "[DRY] Would patch"
    by_transition: dict[tuple[int, int], int] = {}
    for _key, have, want in changed:
        by_transition[(have, want)] = by_transition.get((have, want), 0) + 1
    for (have, want), n in sorted(by_transition.items()):
        logger.info("%s %d files: authority_level %s -> %s", verb, n, have, want)
    logger.info("%s %d files total (of %d)", verb, len(changed), len(keys))
    if not args.apply and changed:
        logger.info("Re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
