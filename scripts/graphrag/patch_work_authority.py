"""Patch authority_level in work-bucket extracted/ and embedded/ JSONs.

Companion to patch_metadata_authority.py (which fixes the raw bucket).
The work-bucket artifacts cache a flat `authority_level` produced by an
earlier extract run with the off-by-one value. load.py reads embedded/*.json
and MERGEs node properties, so patching these (then re-running load) corrects
the live graph's authority_level without re-classifying or re-embedding.

Rewrites authority_level to each doc's framework-canonical level from
ingest_config.yaml. Idempotent, dry-run by default.

Usage:
    AWS_REGION=us-east-1 AWS_PROFILE=wisco \
        python scripts/graphrag/patch_work_authority.py \
        --work-bucket wis-work-bucket-c8e69250 \
        --config scripts/graphrag/ingest_config.yaml [--apply]
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
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    s3 = boto3.client(
        "s3",
        region_name=args.region,
        config=Config(max_pool_connections=args.max_workers + 4),
    )
    levels = framework_levels(args.config)

    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in ("extracted/", "embedded/"):
        for page in paginator.paginate(Bucket=args.work_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".json"):
                    keys.append(obj["Key"])
    logger.info("Found %d work artifacts", len(keys))

    def inspect(key: str) -> tuple[int, int] | None:
        doc = json.loads(s3.get_object(Bucket=args.work_bucket, Key=key)["Body"].read())
        # Only per-document dict artifacts carry framework_id/authority_level.
        # Other JSONs under these prefixes (e.g. chunk-vector lists) are not
        # documents and load.py ignores them — skip rather than crash.
        if not isinstance(doc, dict):
            return None
        want = levels.get(doc.get("framework_id"))
        if want is None:
            return None
        raw_have = doc.get("authority_level")
        try:
            have = int(raw_have) if raw_have is not None else None
        except (TypeError, ValueError):
            have = None
        if have == want:
            return None
        if args.apply:
            doc["authority_level"] = want
            s3.put_object(
                Bucket=args.work_bucket,
                Key=key,
                Body=json.dumps(doc, default=str).encode("utf-8"),
                ContentType="application/json",
            )
        return (have if have is not None else -1, want)

    transitions: dict[tuple[int, int], int] = {}
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        for result in pool.map(inspect, keys):
            if result:
                transitions[result] = transitions.get(result, 0) + 1

    verb = "Patched" if args.apply else "[DRY] Would patch"
    total = 0
    for (have, want), n in sorted(transitions.items()):
        logger.info("%s %d artifacts: authority_level %s -> %s", verb, n, have, want)
        total += n
    logger.info("%s %d artifacts total (of %d)", verb, total, len(keys))
    if not args.apply and total:
        logger.info("Re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
