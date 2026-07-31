"""Seed the FaqUrlTable from documents/faqs.json.

Builds the normalized-question -> source_url map (with fuzzy recovery) and
upserts one item per normalized question into the DynamoDB table. Idempotent:
re-running overwrites items in place.

Usage:
    AWS_REGION=us-east-1 AWS_PROFILE=<your-profile> \
        python tools/ingestion/ops/seed_faq_url_table.py \
        --table <FaqUrlTableName> --faqs documents/faqs.json
    # --dry-run prints counts without writing.

Find the table name from stack outputs:
    aws cloudformation describe-stacks --stack-name WisconsinBotGraphRAG \
        --profile <your-profile> --region us-east-1 \
        --query "Stacks[0].Outputs[?contains(OutputKey,'FaqUrlTable')].OutputValue" --output text
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import boto3

# Ensure the repo root is importable when run directly, regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.ingestion.lib.faq_url_map import build_url_map  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--table", required=True, help="FaqUrlTable name")
    parser.add_argument("--faqs", default="documents/faqs.json")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    with open(args.faqs, encoding="utf-8") as f:
        records = json.load(f)
    url_map = build_url_map(records)
    by_question = url_map["by_question"]
    logger.info(
        "Loaded %d FAQ records -> %d unique normalized questions",
        len(records),
        len(by_question),
    )

    if args.dry_run:
        logger.info("[DRY] would upsert %d items into %s", len(by_question), args.table)
        for nq, url in list(by_question.items())[:5]:
            logger.info("[DRY]   %r -> %s", nq[:60], url)
        return 0

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    table = session.resource("dynamodb", region_name=args.region).Table(args.table)
    written = 0
    with table.batch_writer() as batch:
        for nq, url in by_question.items():
            batch.put_item(Item={"normalized_question": nq, "source_url": url})
            written += 1
    logger.info("Upserted %d items into %s", written, args.table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
