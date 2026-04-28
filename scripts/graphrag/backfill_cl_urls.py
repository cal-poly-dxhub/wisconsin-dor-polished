"""
Backfill CourtListener URLs into case law metadata.

Updates source_url in each case-law .metadata.json file in S3
to point to the CourtListener opinion page instead of the
Wisconsin legislature redirect page.

Prerequisites:
    export COURTLISTENER_TOKEN="your_token_here"

Usage:
    python scripts/graphrag/backfill_cl_urls.py --bucket wis-raw-bucket-c8e69250
    python scripts/graphrag/backfill_cl_urls.py --bucket wis-raw-bucket-c8e69250 --dry-run
"""

import argparse
import json
import logging
import os
import re
import time

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

s3 = boto3.client("s3")
CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"


def list_case_law_metadata(bucket: str, prefix: str = "raw/") -> list[dict]:
    """List all case-law metadata files in S3."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "case-law-" in key and key.endswith(".txt.metadata.json"):
                keys.append(key)
    return keys


def search_courtlistener(citation: str, session: requests.Session) -> str | None:
    """Search CourtListener for a citation, return the opinion page URL or None."""
    params = {"q": f'"{citation}"', "type": "o", "page_size": 5}
    try:
        resp = session.get(CL_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if not data.get("results"):
        return None

    # Prefer exact citation match
    for result in data["results"]:
        if citation in result.get("citation", []):
            return f"https://www.courtlistener.com{result['absolute_url']}"

    # Fallback to top result
    return f"https://www.courtlistener.com{data['results'][0]['absolute_url']}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="raw/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("COURTLISTENER_TOKEN", "")
    session = requests.Session()
    if token:
        session.headers["Authorization"] = f"Token {token}"

    meta_keys = list_case_law_metadata(args.bucket, args.prefix)
    logger.info(f"Found {len(meta_keys)} case-law metadata files")

    updated = 0
    missed = 0

    for i, key in enumerate(meta_keys):
        obj = s3.get_object(Bucket=args.bucket, Key=key)
        meta = json.loads(obj["Body"].read())
        attrs = meta.get("metadataAttributes", {})
        citation = attrs.get("citation", "")

        if not citation:
            continue

        # Skip if already pointing to CourtListener
        if "courtlistener.com" in attrs.get("source_url", ""):
            continue

        cl_url = search_courtlistener(citation, session)

        if cl_url:
            # Keep original legis URL in a separate field
            if "legis_url" not in attrs:
                attrs["legis_url"] = attrs.get("source_url", "")
            attrs["source_url"] = cl_url

            if not args.dry_run:
                meta["metadataAttributes"] = attrs
                s3.put_object(
                    Bucket=args.bucket, Key=key,
                    Body=json.dumps(meta, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )

            updated += 1
            logger.info(f"[{i+1}/{len(meta_keys)}] {citation} -> {cl_url}")
        else:
            missed += 1
            logger.warning(f"[{i+1}/{len(meta_keys)}] No CL URL for: {citation}")

        time.sleep(0.5)

        if (i + 1) % 100 == 0:
            logger.info(f"Progress: {updated} updated, {missed} missed")

    logger.info(f"\nDone: {updated} updated, {missed} missed out of {len(meta_keys)}")


if __name__ == "__main__":
    main()
