#!/usr/bin/env python3
"""
One-time backfill: populate the top-level `rating` scalar on ChatHistoryTable
rows so the admin activity list can show the middle ("mixed") rating.

Background: feedback submits `thumbUp` (a boolean that collapses mid→false) and,
for rich submissions, a nested `richFeedback` map that DOES carry the true
'up' | 'mid' | 'down' rating. The list GSI only projects scalars, so it can't
read the nested map. `update_query_feedback` now also writes a first-class
`rating` scalar, but pre-existing rows don't have it. This script sets it:

  - from `richFeedback.rating` when present and valid, else
  - derived from `thumbUp` ('up' if true, 'down' if false), else
  - skipped (genuinely unrated rows stay unrated).

Rows that already have a top-level `rating` are skipped (idempotent).

Usage:
  # Dry run — report what would change, write nothing:
  AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/backfill_feedback_rating.py \
    --table-name <ChatHistoryTable> --dry-run

  # Apply:
  AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/backfill_feedback_rating.py \
    --table-name <ChatHistoryTable>
"""

import argparse
import os
import sys

import boto3
from boto3.dynamodb.types import TypeDeserializer

VALID_RATINGS = ("up", "mid", "down")


def resolve_rating(item: dict) -> str | None:
    """Deserialized DynamoDB item -> new rating scalar, or None to skip."""
    rich = item.get("richFeedback")
    if isinstance(rich, dict):
        rating = rich.get("rating")
        if rating in VALID_RATINGS:
            return rating
    thumb_up = item.get("thumbUp")
    if isinstance(thumb_up, bool):
        return "up" if thumb_up else "down"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", required=True, help="ChatHistoryTable name")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without writing"
    )
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("dynamodb", region_name=region)
    deserializer = TypeDeserializer()

    scanned = 0
    already = 0
    to_update: list[tuple[str, str]] = []  # (queryId, rating)
    unrated = 0

    paginator = client.get_paginator("scan")
    for page in paginator.paginate(
        TableName=args.table_name,
        ProjectionExpression="queryId, thumbUp, richFeedback, #r",
        ExpressionAttributeNames={"#r": "rating"},
    ):
        for raw in page.get("Items", []):
            scanned += 1
            item = {k: deserializer.deserialize(v) for k, v in raw.items()}
            if item.get("rating") in VALID_RATINGS:
                already += 1
                continue
            rating = resolve_rating(item)
            if rating is None:
                unrated += 1
                continue
            query_id = item.get("queryId")
            if query_id:
                to_update.append((query_id, rating))

    print(
        f"Scanned {scanned} rows: {already} already have rating, "
        f"{unrated} unrated (skipped), {len(to_update)} to backfill."
    )

    if args.dry_run:
        for query_id, rating in to_update[:20]:
            print(f"  would set rating={rating} for {query_id}")
        if len(to_update) > 20:
            print(f"  ... and {len(to_update) - 20} more")
        print("Dry run — no writes performed.")
        return 0

    written = 0
    for query_id, rating in to_update:
        client.update_item(
            TableName=args.table_name,
            Key={"queryId": {"S": query_id}},
            UpdateExpression="SET #r = :r",
            ExpressionAttributeNames={"#r": "rating"},
            ExpressionAttributeValues={":r": {"S": rating}},
        )
        written += 1
        if written % 100 == 0:
            print(f"  ...{written}/{len(to_update)}")

    print(f"Backfill complete: {written} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
