"""
Case law downloader using CourtListener as the primary source.

Searches CourtListener's free search API to find cases by citation,
then fetches full opinion text via the authenticated REST API.
Falls back to Google Scholar for misses (~10% of citations).

CourtListener covers ~90% of the 2,548 citations. A free API token
is required for the REST endpoints (register at courtlistener.com).

Usage:
    # Set your CourtListener API token:
    export COURTLISTENER_TOKEN="your_token_here"

    # Download all cases to S3:
    python scripts/graphrag/fetch_case_law.py --bucket <raw-bucket>

    # Dry run to see coverage:
    python scripts/graphrag/fetch_case_law.py --bucket <raw-bucket> --dry-run

    # Skip Google Scholar fallback:
    python scripts/graphrag/fetch_case_law.py --bucket <raw-bucket> --no-fallback
"""

import argparse
import json
import logging
import os
import random
import re
import time

import boto3
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

s3 = boto3.client("s3")

CITATIONS_FILE = "docs/case-law-citations.json"
PROGRESS_FILE = "docs/case-law-fetch-progress.json"

CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
CL_OPINION_URL = "https://www.courtlistener.com/api/rest/v4/opinions/{}/"

SCHOLAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Shared helpers (duplicated from scrape_case_law.py to stay self-contained)
# ---------------------------------------------------------------------------

def make_case_doc_id(citation: str) -> str:
    clean = re.sub(r"[%\s.]+", "-", citation).strip("-").lower()
    return f"case-law-{clean}"


def upload_case_to_s3(
    bucket: str, prefix: str, doc_id: str,
    citation: str, case_name: str, text: str | None,
    sources: list[dict], scholar_url: str, legis_url: str,
):
    if text:
        content = text.encode("utf-8")
        content_type = "text/plain"
        ext = ".txt"
    else:
        content = json.dumps({
            "citation": citation,
            "case_name": case_name or citation,
            "note": "Full opinion text not yet downloaded.",
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


# ---------------------------------------------------------------------------
# Google Scholar fallback
# ---------------------------------------------------------------------------

def fetch_scholar_opinion(scholar_url: str, delay: float) -> tuple[str, str] | None:
    time.sleep(delay + random.uniform(0, delay * 0.5))
    try:
        resp = requests.get(scholar_url, headers=SCHOLAR_HEADERS, timeout=30)
        if resp.status_code == 429 or "captcha" in resp.text.lower():
            logger.warning("Scholar rate limit / CAPTCHA")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        case_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/scholar_case?" in href and "about=" not in href:
                case_link = href
                break
        if not case_link:
            return None

        time.sleep(delay * 0.5 + random.uniform(0, 3))
        case_url = f"https://scholar.google.com{case_link}" if case_link.startswith("/") else case_link
        resp2 = requests.get(case_url, headers=SCHOLAR_HEADERS, timeout=30)
        if resp2.status_code == 429 or "captcha" in resp2.text.lower():
            return None

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        opinion = soup2.find("div", id="gs_opinion")
        if not opinion:
            return None

        case_name = ""
        h1 = soup2.find("h1")
        if h1:
            case_name = h1.get_text(strip=True)

        return case_name, opinion.get_text("\n", strip=True)
    except requests.RequestException as e:
        logger.error(f"Scholar request failed: {e}")
        return None


# ---------------------------------------------------------------------------
# CourtListener
# ---------------------------------------------------------------------------

def load_citations() -> list[dict]:
    with open(CITATIONS_FILE) as f:
        return json.load(f)


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": {}, "failed": [], "stats": {"courtlistener": 0, "scholar": 0, "missed": 0}}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def search_courtlistener(citation: str, session: requests.Session) -> dict | None:
    """Search CourtListener by citation. Returns best matching result or None."""
    params = {"q": f'"{citation}"', "type": "o", "page_size": 5}
    try:
        resp = session.get(CL_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"CL search failed for '{citation}': {e}")
        return None

    if not data.get("results"):
        return None

    # Prefer exact citation match
    for result in data["results"]:
        if citation in result.get("citation", []):
            return result

    return data["results"][0]


def fetch_opinion_text(opinion_id: int, session: requests.Session) -> str | None:
    """Fetch full opinion text from CourtListener REST API (requires auth token)."""
    url = CL_OPINION_URL.format(opinion_id)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 401:
            logger.error("Auth failed — set COURTLISTENER_TOKEN env var")
            return None
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 30))
            logger.warning(f"CL rate limited, sleeping {retry_after}s")
            time.sleep(retry_after)
            resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Failed to fetch opinion {opinion_id}: {e}")
        return None

    for field in ("plain_text", "html_with_citations", "html", "html_lawbox", "html_columbia", "xml_harvard"):
        text = data.get(field, "")
        if text and text.strip():
            if field.startswith("html") or field.startswith("xml"):
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text

    return None


def fetch_from_courtlistener(citation: str, session: requests.Session) -> tuple[str, str] | None:
    """Search + fetch full opinion. Returns (case_name, text) or None."""
    result = search_courtlistener(citation, session)
    if not result:
        return None

    case_name = result.get("caseName", "")
    opinions = result.get("opinions", [])
    if not opinions:
        return None

    # Prefer combined-opinion, then lead-opinion
    opinion_id = None
    for pref in ("combined-opinion", "lead-opinion"):
        for op in opinions:
            if op.get("type") == pref:
                opinion_id = op["id"]
                break
        if opinion_id:
            break
    if not opinion_id:
        opinion_id = opinions[0]["id"]

    text = fetch_opinion_text(opinion_id, session)
    if not text:
        return None

    return case_name, text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch case law via CourtListener + Scholar fallback")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument("--dry-run", action="store_true", help="Search only, don't upload to S3")
    parser.add_argument("--no-fallback", action="store_true", help="Skip Google Scholar fallback")
    parser.add_argument("--scholar-delay", type=float, default=30, help="Delay for Scholar requests (default: 30s)")
    parser.add_argument("--scholar-max-failures", type=int, default=3, help="Consecutive Scholar failures before disabling")
    parser.add_argument("--limit", type=int, default=0, help="Process only N citations (0 = all)")
    args = parser.parse_args()

    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token and not args.dry_run:
        logger.error(
            "Set COURTLISTENER_TOKEN env var. "
            "Register free at https://www.courtlistener.com/sign-in/register/"
        )
        return

    session = requests.Session()
    if token:
        session.headers["Authorization"] = f"Token {token}"

    citations = load_citations()
    progress = load_progress()
    logger.info(f"Loaded {len(citations)} citations, {len(progress['completed'])} already done")

    scholar_consecutive_failures = 0
    processed = 0

    for i, entry in enumerate(citations):
        citation = entry["citation"]
        doc_id = make_case_doc_id(citation)

        if doc_id in progress["completed"]:
            continue

        if args.limit and processed >= args.limit:
            break
        processed += 1

        # --- Try CourtListener first ---
        result = fetch_from_courtlistener(citation, session)
        if result:
            case_name, text = result
            logger.info(f"[{i+1}/{len(citations)}] CL: {case_name or citation} ({len(text)} chars)")
            progress["stats"]["courtlistener"] += 1

            if not args.dry_run:
                upload_case_to_s3(
                    args.bucket, args.prefix, doc_id,
                    citation, case_name, text,
                    entry["sources"], entry["scholar_url"], entry["legis_url"],
                )
                progress["completed"][doc_id] = {"citation": citation, "source": "courtlistener", "has_text": True}

            time.sleep(0.5)
            continue

        # --- Scholar fallback ---
        if not args.no_fallback and not args.dry_run:
            logger.info(f"[{i+1}/{len(citations)}] CL miss, trying Scholar: {citation}")
            scholar_result = fetch_scholar_opinion(entry["scholar_url"], args.scholar_delay)

            if scholar_result:
                case_name, text = scholar_result
                logger.info(f"  Scholar: {case_name or citation} ({len(text)} chars)")
                progress["stats"]["scholar"] += 1
                scholar_consecutive_failures = 0

                upload_case_to_s3(
                    args.bucket, args.prefix, doc_id,
                    citation, case_name, text,
                    entry["sources"], entry["scholar_url"], entry["legis_url"],
                )
                progress["completed"][doc_id] = {"citation": citation, "source": "scholar", "has_text": True}
            else:
                scholar_consecutive_failures += 1
                logger.warning(f"  Scholar miss: {citation} (consecutive: {scholar_consecutive_failures})")
                progress["stats"]["missed"] += 1
                if citation not in progress["failed"]:
                    progress["failed"].append(citation)

                if scholar_consecutive_failures >= args.scholar_max_failures:
                    logger.warning("Scholar rate-limited — disabling fallback for remaining cases")
                    args.no_fallback = True
        else:
            logger.info(f"[{i+1}/{len(citations)}] Miss: {citation}")
            progress["stats"]["missed"] += 1
            if citation not in progress["failed"]:
                progress["failed"].append(citation)
            time.sleep(0.5)

        if (i + 1) % 50 == 0:
            save_progress(progress)
            s = progress["stats"]
            logger.info(f"Progress: CL={s['courtlistener']}, Scholar={s['scholar']}, Missed={s['missed']}")

    save_progress(progress)
    s = progress["stats"]
    total = len(progress["completed"])
    logger.info(f"\nDone: {total} completed (CL={s['courtlistener']}, Scholar={s['scholar']}, Missed={s['missed']})")
    if progress["failed"]:
        logger.info(f"{len(progress['failed'])} citations not found — see {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
