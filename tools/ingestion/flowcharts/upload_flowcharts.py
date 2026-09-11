"""Upload WPAM flowchart sidecars to S3.

The 6 decision-flowchart sidecars are hand-authored and committed under
``tools/ingestion/flowcharts/data/*.json`` (the source of truth — they are
transcribed from raster images in the WPAM, so there is no automated extractor
like the worksheets openpyxl step). This script uploads them to
``s3://{raw-bucket}/flowcharts/{flowchart_id}.json`` where the agentic_retrieval
``get_flowchart`` tool reads them. It reuses the existing raw-bucket grant — no
IAM/CDK change.

Run at the annual WPAM refresh (or whenever a sidecar changes):

    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      uv run python -m tools.ingestion.flowcharts.upload_flowcharts \\
      --raw-bucket wis-raw-bucket-c8e69250

Add --dry-run to see what would upload without writing.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REGION = os.environ.get("AWS_REGION", "us-east-1")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
S3_PREFIX = "flowcharts/"


def _validate(path: str) -> dict:
    """Parse + minimally validate a sidecar (valid JSON, has id/nodes/edges)."""
    with open(path) as f:
        doc = json.load(f)
    fid = doc.get("flowchart_id")
    if not fid:
        raise ValueError(f"{path}: missing flowchart_id")
    if os.path.basename(path) != f"{fid}.json":
        raise ValueError(f"{path}: filename does not match flowchart_id '{fid}'")
    for required in ("nodes", "edges", "disclaimer", "start_node"):
        if required not in doc:
            raise ValueError(f"{path}: missing '{required}'")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-bucket", required=True, help="S3 raw bucket to upload sidecars to")
    ap.add_argument("--dry-run", action="store_true", help="Show what would upload; write nothing")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
    if not paths:
        logger.error("No sidecars found in %s", DATA_DIR)
        raise SystemExit(1)

    s3 = None if args.dry_run else boto3.client("s3", region_name=REGION)
    for path in paths:
        doc = _validate(path)
        key = f"{S3_PREFIX}{doc['flowchart_id']}.json"
        if args.dry_run:
            logger.info("[dry-run] would upload %s -> s3://%s/%s", path, args.raw_bucket, key)
            continue
        s3.put_object(
            Bucket=args.raw_bucket,
            Key=key,
            Body=json.dumps(doc, indent=2).encode(),
            ContentType="application/json",
        )
        logger.info("uploaded s3://%s/%s", args.raw_bucket, key)

    logger.info("%d flowchart sidecar(s) processed", len(paths))


if __name__ == "__main__":
    main()
