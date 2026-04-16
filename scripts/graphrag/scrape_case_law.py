"""
Background case law downloader.

Reads docs/case-law-citations.json (produced by PDF link extraction)
and attempts to download full opinion text from Google Scholar.

Google Scholar will likely CAPTCHA after ~100-200 requests even with
delays. This script saves progress and can resume where it left off.

Usage:
    # Metadata-only (always works, no external requests):
    python scripts/graphrag/scrape_case_law.py --bucket <raw-bucket> --metadata-only

    # Full download attempt (will likely get rate-limited):
    python scripts/graphrag/scrape_case_law.py --bucket <raw-bucket> --delay 30

    # Resume after being blocked:
    python scripts/graphrag/scrape_case_law.py --bucket <raw-bucket> --delay 45
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote

import boto3
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

s3 = boto3.client("s3")

PROGRESS_FILE = "docs/case-law-progress.json"
CITATIONS_FILE = "docs/case-law-citations.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_citations() -> list[dict]:
    with open(CITATIONS_FILE) as f:
        return json.load(f)


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": {}, "failed": {}, "blocked_at": None}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def make_case_doc_id(citation: str) -> str:
    """Generate a stable document ID from a case citation."""
    clean = re.sub(r"[%\s.]+", "-", citation).strip("-").lower()
    return f"case-law-{clean}"


def fetch_scholar_opinion(scholar_url: str, delay: float) -> tuple[str, str] | None:
    """
    Search Google Scholar and fetch the first matching opinion.

    Returns (case_name, opinion_text) or None if blocked/not found.
    """
    time.sleep(delay + random.uniform(0, delay * 0.5))

    try:
        resp = requests.get(scholar_url, headers=HEADERS, timeout=30)
        if resp.status_code == 429 or "captcha" in resp.text.lower():
            logger.warning("Google Scholar rate limit / CAPTCHA detected")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find first scholar_case link (skip "How cited" / "about=" links)
        case_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/scholar_case?" in href and "about=" not in href:
                case_link = href
                break

        if not case_link:
            logger.warning(f"No case link found in search results")
            return None

        # Fetch the case page
        time.sleep(delay * 0.5 + random.uniform(0, 3))
        case_url = f"https://scholar.google.com{case_link}" if case_link.startswith("/") else case_link
        resp2 = requests.get(case_url, headers=HEADERS, timeout=30)

        if resp2.status_code == 429 or "captcha" in resp2.text.lower():
            logger.warning("Google Scholar rate limit / CAPTCHA on case page")
            return None

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        opinion = soup2.find("div", id="gs_opinion")
        if not opinion:
            logger.warning("No opinion div found on case page")
            return None

        case_name = ""
        h1 = soup2.find("h1")
        if h1:
            case_name = h1.get_text(strip=True)

        text = opinion.get_text("\n", strip=True)
        return case_name, text

    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def upload_case_to_s3(
    bucket: str, prefix: str, doc_id: str,
    citation: str, case_name: str, text: str | None,
    sources: list[dict], scholar_url: str, legis_url: str,
):
    """Upload case law document + metadata to S3."""
    if text:
        content = text.encode("utf-8")
        content_type = "text/plain"
        ext = ".txt"
    else:
        # Metadata-only stub
        content = json.dumps({
            "citation": citation,
            "case_name": case_name or citation,
            "note": "Full opinion text not yet downloaded. See scholar_url.",
            "scholar_url": scholar_url,
        }, indent=2).encode("utf-8")
        content_type = "application/json"
        ext = ".json"

    doc_key = f"{prefix}{doc_id}/{doc_id}{ext}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{ext}.metadata.json"

    s3.put_object(Bucket=bucket, Key=doc_key, Body=content, ContentType=content_type)

    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id,
            "doc_type": "case_law",
            "framework_id": "FW-CASE-LAW",
            "authority_level": "3",
            "category": "case_law",
            "citation": citation,
            "case_name": case_name or citation,
            "source_url": legis_url,
            "scholar_url": scholar_url,
            "citing_statutes": json.dumps(
                [{"file": s["file"], "pages": s["pages"]} for s in sources]
            ),
        }
    }

    s3.put_object(
        Bucket=bucket, Key=meta_key,
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return doc_key


def main():
    parser = argparse.ArgumentParser(description="Scrape case law from statute PDF links")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Upload metadata stubs only, no Google Scholar fetch")
    parser.add_argument("--delay", type=float, default=30,
                        help="Base delay between Google Scholar requests in seconds (default: 30)")
    parser.add_argument("--max-failures", type=int, default=3,
                        help="Stop after N consecutive failures (likely blocked)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    citations = load_citations()
    progress = load_progress()
    logger.info(f"Loaded {len(citations)} case citations, {len(progress['completed'])} already done")

    consecutive_failures = 0
    processed = 0
    downloaded = 0
    skipped = 0

    for entry in citations:
        citation = entry["citation"]
        doc_id = make_case_doc_id(citation)

        if doc_id in progress["completed"]:
            skipped += 1
            continue

        processed += 1

        if args.dry_run:
            logger.info(f"[{processed}/{len(citations)}] Would process: {citation}")
            continue

        case_name = ""
        text = None

        if not args.metadata_only:
            result = fetch_scholar_opinion(entry["scholar_url"], args.delay)
            if result:
                case_name, text = result
                downloaded += 1
                consecutive_failures = 0
                logger.info(f"[{processed}] Downloaded: {case_name or citation} ({len(text)} chars)")
            else:
                consecutive_failures += 1
                logger.warning(
                    f"[{processed}] Failed to download: {citation} "
                    f"(consecutive failures: {consecutive_failures})"
                )
                if consecutive_failures >= args.max_failures:
                    logger.error(
                        f"Stopped after {args.max_failures} consecutive failures. "
                        f"Likely rate-limited. Run again later to resume."
                    )
                    progress["blocked_at"] = citation
                    save_progress(progress)
                    break

        doc_key = upload_case_to_s3(
            args.bucket, args.prefix, doc_id,
            citation, case_name, text,
            entry["sources"], entry["scholar_url"], entry["legis_url"],
        )

        progress["completed"][doc_id] = {
            "citation": citation,
            "has_text": text is not None,
            "s3_key": doc_key,
        }

        if processed % 50 == 0:
            save_progress(progress)
            logger.info(f"Progress saved: {processed} processed, {downloaded} downloaded")

    save_progress(progress)

    total = len(citations)
    logger.info(
        f"\nComplete: {processed} processed, {downloaded} downloaded, "
        f"{skipped} previously done, {total - processed - skipped} remaining"
    )
    if not args.metadata_only and downloaded < processed:
        logger.info(
            "Some cases were uploaded as metadata stubs. "
            "Run again later (without --metadata-only) to retry downloads."
        )


if __name__ == "__main__":
    main()
