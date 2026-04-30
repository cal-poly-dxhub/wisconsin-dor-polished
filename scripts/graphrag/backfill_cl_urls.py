"""
Backfill CourtListener URLs into case law metadata.

Updates source_url in each case-law .metadata.json file in S3
to point to the CourtListener opinion page instead of the
Wisconsin legislature redirect page.

Prerequisites:
    export COURTLISTENER_TOKEN="your_token_here"

Usage:
    python scripts/graphrag/backfill_cl_urls.py \
        --bucket wis-raw-bucket-c8e69250 \
        --profile widor \
        --dry-run \
        --output /tmp/case_law_cl_url_backfill.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def s3_client(profile: str | None):
    if profile:
        return boto3.Session(profile_name=profile).client("s3")
    return boto3.client("s3")


def list_case_law_metadata(s3, bucket: str, prefix: str = "raw/") -> list[str]:
    """List all case-law metadata files in S3."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "case-law-" in key and key.endswith(".txt.metadata.json"):
                keys.append(key)
    return sorted(keys)


def search_courtlistener(
    citation: str,
    session: requests.Session,
    allow_fallback: bool = False,
) -> dict[str, Any] | None:
    """Search CourtListener for a citation and return match metadata."""
    params = {"q": f'"{citation}"', "type": "o", "page_size": 5}
    for attempt in range(4):
        try:
            resp = session.get(CL_SEARCH_URL, params=params, timeout=20)
            if resp.status_code == 429 and attempt < 3:
                retry_after = int(resp.headers.get("Retry-After", 10))
                logger.warning("CourtListener rate limited; sleeping %ss", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt == 3:
                logger.warning("CourtListener search failed for %s: %s", citation, exc)
                return None
            time.sleep(2 ** attempt)
    else:
        return None

    if not data.get("results"):
        return None

    for result in data["results"]:
        if citation in result.get("citation", []):
            return {
                "match_type": "exact",
                "source_url": f"https://www.courtlistener.com{result['absolute_url']}",
                "case_name": result.get("caseName") or result.get("caseNameFull") or "",
                "cluster_id": result.get("cluster_id") or "",
                "citations": result.get("citation") or [],
            }

    if not allow_fallback:
        return None

    result = data["results"][0]
    return {
        "match_type": "fallback",
        "source_url": f"https://www.courtlistener.com{result['absolute_url']}",
        "case_name": result.get("caseName") or result.get("caseNameFull") or "",
        "cluster_id": result.get("cluster_id") or "",
        "citations": result.get("citation") or [],
    }


def write_rows(path: str, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    fieldnames = [
        "metadata_key",
        "citation",
        "status",
        "match_type",
        "old_source_url",
        "new_source_url",
        "case_name",
        "cluster_id",
        "courtlistener_citations",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="raw/")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-fallback", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--verbose-matches", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token:
        raise RuntimeError("COURTLISTENER_TOKEN is required in the environment or .env")

    s3 = s3_client(args.profile)
    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"
    session.headers["User-Agent"] = "wisconsin-dor-cl-url-backfill/1.0"

    meta_keys = list_case_law_metadata(s3, args.bucket, args.prefix)
    logger.info(f"Found {len(meta_keys)} case-law metadata files")

    updated = 0
    missed = 0
    skipped = 0
    rows: list[dict[str, Any]] = []

    for i, key in enumerate(meta_keys):
        obj = s3.get_object(Bucket=args.bucket, Key=key)
        meta = json.loads(obj["Body"].read())
        attrs = meta.get("metadataAttributes", {})
        citation = attrs.get("citation", "")

        if not citation:
            skipped += 1
            continue

        old_source_url = attrs.get("source_url", "")
        row: dict[str, Any] = {
            "metadata_key": key,
            "citation": citation,
            "status": "",
            "match_type": "",
            "old_source_url": old_source_url,
            "new_source_url": "",
            "case_name": "",
            "cluster_id": "",
            "courtlistener_citations": "",
        }

        if "courtlistener.com" in old_source_url:
            skipped += 1
            row["status"] = "already-courtlistener"
            row["new_source_url"] = old_source_url
            rows.append(row)
            continue

        match = search_courtlistener(citation, session, allow_fallback=args.allow_fallback)

        if match:
            if "legis_url" not in attrs:
                attrs["legis_url"] = old_source_url
            attrs["source_url"] = match["source_url"]
            if match["case_name"]:
                attrs["case_name"] = match["case_name"]
            if match["cluster_id"]:
                attrs["courtlistener_cluster_id"] = str(match["cluster_id"])

            if not args.dry_run:
                meta["metadataAttributes"] = attrs
                s3.put_object(
                    Bucket=args.bucket, Key=key,
                    Body=json.dumps(meta, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )

            updated += 1
            row.update({
                "status": "would-update" if args.dry_run else "updated",
                "match_type": match["match_type"],
                "new_source_url": match["source_url"],
                "case_name": match["case_name"],
                "cluster_id": match["cluster_id"],
                "courtlistener_citations": "; ".join(match["citations"]),
            })
            rows.append(row)
            if args.verbose_matches:
                logger.info(f"[{i+1}/{len(meta_keys)}] {citation} -> {match['source_url']}")
        else:
            missed += 1
            row["status"] = "missed"
            rows.append(row)
            logger.warning(f"[{i+1}/{len(meta_keys)}] No CL URL for: {citation}")

        time.sleep(args.delay)

        if (i + 1) % 100 == 0:
            logger.info(f"Progress: {updated} updated, {missed} missed, {skipped} skipped")

    write_rows(args.output, rows)
    if args.output:
        logger.info("Wrote CSV audit to %s", args.output)
    logger.info(
        f"\nDone: {updated} updated, {missed} missed, {skipped} skipped out of {len(meta_keys)}"
    )


if __name__ == "__main__":
    main()
