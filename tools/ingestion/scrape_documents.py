"""
Scrape documents from the manifest and upload to S3 with change detection.

Reads document_manifest.yaml (single source of truth for all corpus URLs),
downloads each document, compares content hash against S3 ETag, and only
uploads files that have changed. Produces a summary of new/changed/unchanged.

Usage:
    python tools/ingestion/scrape_documents.py \
        --bucket <raw-bucket-name> \
        --prefix raw/

    # Only scrape specific categories:
    python tools/ingestion/scrape_documents.py \
        --bucket <raw-bucket-name> --category statutes --category admin_rules

    # Force re-upload even if content matches:
    python tools/ingestion/scrape_documents.py \
        --bucket <raw-bucket-name> --force
"""

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import boto3
import requests
import yaml
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Generic filename stems that need parent-directory disambiguation
GENERIC_STEMS = {"home", "index", "default", "main", "page"}

# News category parent path segments (used for URL-based ID construction)
NEWS_PARENT_SEGMENTS = {"cotvc-news", "assessor-news", "mfg-news"}

# Regex patterns for document ID extraction by category
STATUTE_CHAPTER_RE = re.compile(r"ch\.\s*(\d+)", re.I)
ADMIN_RULE_CHAPTER_RE = re.compile(r"ch\.\s*Tax\s*(\d+)", re.I)

# Known typos in IAAO source filenames that need correction
IAAO_TYPO_CORRECTIONS = {"responibilities": "responsibilities", "comunication": "communication"}

# Date extraction from news page URLs (supports YYYY-MM-DD and YYYYMMDD formats)
NEWS_DATE_RE = re.compile(
    r"/(?:COTVC-News|Assessor-News|MFG-News)/"
    r"(?:"
    r"(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})[a-z]?"
    r"|(?P<y2>\d{4})(?P<m2>\d{2})(?P<d2>\d{2})"
    r")",
    re.I,
)

MANIFEST_PATH = Path(__file__).parent / "config" / "document_manifest.yaml"

# ---------------------------------------------------------------------------
# Module setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

s3 = boto3.client("s3")


# ---------------------------------------------------------------------------
# make_doc_id and helpers
# ---------------------------------------------------------------------------


def _split_camel_case(text: str) -> str:
    """Insert hyphens at CamelCase boundaries: 'StandardValuation' -> 'standard-valuation'."""
    text = re.sub(r"([a-z])([A-Z])", r"\1-\2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", text)
    return text.lower()


def make_doc_id(category: str, url: str, explicit_id: str | None = None) -> str:
    """Generate a stable document ID from category and URL.

    If explicit_id is provided (from the manifest), use it directly.
    Special cases for statutes/admin_rules/wpam/iaao/uspap to produce
    clean, consistent names. Otherwise: `{category}-{filename-stem}`.
    """
    if explicit_id:
        return explicit_id

    decoded_path = unquote(urlparse(url).path)

    # Statutes: extract chapter number to produce "statutes-70"
    if category == "statutes":
        match = STATUTE_CHAPTER_RE.search(decoded_path)
        if match:
            return f"statutes-{match.group(1)}"

    # Admin rules: extract Tax chapter number to produce "admin_rules-tax-16"
    if category == "admin_rules":
        match = ADMIN_RULE_CHAPTER_RE.search(decoded_path)
        if match:
            return f"admin_rules-tax-{match.group(1)}"

    # WPAM: extract year suffix to produce "wpam-wisconsin-property-assessment-manual-2026"
    if category == "wpam":
        match = re.search(r"wpam(\d{2})", decoded_path, re.I)
        if match:
            year_short = int(match.group(1))
            full_year = 2000 + year_short if year_short < 50 else 1900 + year_short
            return f"wpam-wisconsin-property-assessment-manual-{full_year}"

    # IAAO: split CamelCase filenames and fix known typos in source URLs
    if category == "iaao":
        filename = Path(decoded_path).stem
        stem = _split_camel_case(filename)
        stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
        for typo, correction in IAAO_TYPO_CORRECTIONS.items():
            stem = stem.replace(typo, correction)
        return f"iaao-{stem}"

    # USPAP: third-party URLs with useless filenames — derive from path keywords
    if category == "uspap":
        match = re.search(r"uspap", decoded_path, re.I)
        if match:
            year_match = re.search(r"20\d{2}", url)
            year_suffix = f"-{year_match.group(0)}" if year_match else ""
            return f"uspap-standards{year_suffix}"

    # Generic fallback: use filename stem, with parent disambiguation if needed
    path_parts = [part for part in decoded_path.split("/") if part]
    filename = Path(decoded_path).stem
    clean_stem = re.sub(r"[^a-z0-9]+", "-", filename.lower()).strip("-")

    if clean_stem in GENERIC_STEMS and len(path_parts) >= 2:
        parent = re.sub(r"[^a-z0-9]+", "-", path_parts[-2].lower()).strip("-")
        if parent:
            return f"{category}-{parent}-{clean_stem}"

    if len(path_parts) >= 2:
        parent = re.sub(r"[^a-z0-9]+", "-", path_parts[-2].lower()).strip("-")
        if parent in NEWS_PARENT_SEGMENTS:
            return f"{category}-{parent}-{clean_stem}"

    return f"{category}-{clean_stem}"


# ---------------------------------------------------------------------------
# Network functions
# ---------------------------------------------------------------------------


def download_file(url: str, max_retries: int = 3) -> tuple[bytes, str]:
    """Download file with retries. Returns (content_bytes, content_type)."""
    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            return response.content, content_type
        except requests.RequestException as exc:
            if attempt == max_retries - 1:
                raise
            logger.warning("  Retry %d/%d for %s: %s", attempt + 1, max_retries, url, exc)
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def scrape_html_page(url: str) -> tuple[bytes, str]:
    """Scrape an HTML page and return the main content as UTF-8 text."""
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    content = soup.find("div", id="ctl00_PlaceHolderMain_ctl01__ControlWrapper_RichHtmlField")
    if not content:
        content = soup.find("div", class_="ms-rtestate-field")
    if not content:
        content = soup.find("main") or soup.find("article") or soup.body

    text = content.get_text("\n", strip=True) if content else soup.get_text("\n", strip=True)
    return text.encode("utf-8"), "text/plain"


# ---------------------------------------------------------------------------
# S3 functions
# ---------------------------------------------------------------------------


def content_changed(bucket: str, key: str, new_data: bytes) -> tuple[bool, bool]:
    """Check if content differs from S3. Returns (changed: bool, is_new: bool)."""
    new_hash = hashlib.md5(new_data).hexdigest()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        existing_etag = head["ETag"].strip('"')
        # Multipart uploads have ETags like "abc123-2" — can't compare MD5
        if "-" in existing_etag:
            existing_body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            changed = hashlib.md5(existing_body).hexdigest() != new_hash
        else:
            changed = existing_etag != new_hash
        return changed, False
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return True, True
        raise


def upload_to_s3(
    bucket: str, prefix: str, doc_id: str, data: bytes, content_type: str, metadata: dict
) -> str:
    """Upload document + metadata JSON to S3."""
    extension = ".pdf" if "pdf" in content_type else ".txt"
    doc_key = f"{prefix}{doc_id}/{doc_id}{extension}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{extension}.metadata.json"

    s3.put_object(Bucket=bucket, Key=doc_key, Body=data, ContentType=content_type)
    meta_json = json.dumps({"metadataAttributes": metadata}, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=meta_key, Body=meta_json, ContentType="application/json")

    return doc_key


def get_s3_key(prefix: str, doc_id: str, is_pdf: bool) -> str:
    """Compute the S3 key for a document."""
    extension = ".pdf" if is_pdf else ".txt"
    return f"{prefix}{doc_id}/{doc_id}{extension}"


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------


def extract_news_date(url: str) -> str | None:
    """Return ISO date string (YYYY-MM-DD) from a news URL, or None."""
    match = NEWS_DATE_RE.search(url)
    if not match:
        return None
    if match.group("y1"):
        year, month, day = int(match.group("y1")), int(match.group("m1")), int(match.group("d1"))
    else:
        year, month, day = int(match.group("y2")), int(match.group("m2")), int(match.group("d2"))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-document processing
# ---------------------------------------------------------------------------


def process_document(
    *,
    url: str,
    doc_id: str,
    category: str,
    config: dict,
    bucket: str,
    prefix: str,
    force: bool,
    index: int,
    total: int,
    sleep_seconds: float,
    stats: dict,
    failures: list,
    effective_date_override: str | None = None,
) -> None:
    """Download, check, and upload a single document.

    Handles change detection, metadata construction, and logging.
    Updates `stats` and `failures` in place.
    """
    try:
        is_pdf = url.lower().endswith(".pdf") or "pdf" in url.lower().split("?")[0]
        if is_pdf:
            data, content_type = download_file(url)
        else:
            data, content_type = scrape_html_page(url)

        # Check if content actually changed
        s3_key = get_s3_key(prefix, doc_id, "pdf" in content_type)
        if not force:
            changed, is_new = content_changed(bucket, s3_key, data)
            if not changed:
                stats["unchanged"] += 1
                logger.info("  [%d/%d] %s — unchanged", index, total, doc_id)
                return
            status = "new" if is_new else "changed"
        else:
            status = "forced"

        metadata = {
            "doc_id": doc_id,
            "source_url": url,
            "doc_type": config["doc_type"],
            "framework_id": config["framework_id"],
            "authority_level": str(config["authority_level"]),
            "category": category,
        }

        effective_date = effective_date_override or extract_news_date(url)
        if effective_date:
            metadata["effective_date"] = effective_date

        doc_key = upload_to_s3(bucket, prefix, doc_id, data, content_type, metadata)
        stats[status] += 1
        logger.info(
            "  [%d/%d] %s — %s -> s3://%s/%s",
            index,
            total,
            doc_id,
            status.upper(),
            bucket,
            doc_key,
        )

    except Exception as exc:
        stats["failed"] += 1
        logger.error("  [%d/%d] %s — FAILED: %s", index, total, doc_id, exc)
        failures.append({"doc_id": doc_id, "url": url, "error": str(exc)})

    if sleep_seconds > 0 and index < total:
        time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(format="%(message)s", level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Scrape documents from manifest to S3 with change detection",
    )
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be scraped without downloading",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upload all documents regardless of hash match",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="Only scrape this category (repeatable)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between requests (default: 0.5)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help="Path to document manifest YAML",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    sources = manifest["categories"]

    if args.category:
        unknown = set(args.category) - set(sources.keys())
        if unknown:
            parser.error(f"Unknown --category values: {sorted(unknown)}")
        sources = {k: v for k, v in sources.items() if k in args.category}

    total = sum(len(cat["urls"]) for cat in sources.values())
    logger.info("Scraping %d documents across %d categories\n", total, len(sources))

    processed = 0
    stats = {"new": 0, "changed": 0, "unchanged": 0, "forced": 0, "failed": 0}
    failures = []

    for category, config in sources.items():
        logger.info("\n=== %s (authority level %s) ===", category, config["authority_level"])

        for entry in config["urls"]:
            # Entries can be plain URL strings or {url, doc_id, effective_date} dicts
            if isinstance(entry, dict):
                url = entry["url"]
                explicit_id = entry.get("doc_id")
                effective_date_override = entry.get("effective_date")
            else:
                url = entry
                explicit_id = None
                effective_date_override = None

            doc_id = make_doc_id(category, url, explicit_id)
            processed += 1

            if args.dry_run:
                effective_date = effective_date_override or extract_news_date(url)
                date_part = f"  date={effective_date}" if effective_date else ""
                logger.info(
                    "  [%d/%d] Would scrape: %s <- %s%s",
                    processed,
                    total,
                    doc_id,
                    url,
                    date_part,
                )
                continue

            process_document(
                url=url,
                doc_id=doc_id,
                category=category,
                config=config,
                bucket=args.bucket,
                prefix=args.prefix,
                force=args.force,
                index=processed,
                total=total,
                sleep_seconds=args.sleep,
                stats=stats,
                failures=failures,
                effective_date_override=effective_date_override,
            )

    logger.info("\n%sComplete: %d processed", "DRY RUN " if args.dry_run else "", processed)
    logger.info(
        "  New: %d, Changed: %d, Forced: %d, Unchanged: %d, Failed: %d",
        stats["new"],
        stats["changed"],
        stats["forced"],
        stats["unchanged"],
        stats["failed"],
    )

    if failures:
        logger.info("\nFailed (%d):", len(failures))
        for failure in failures:
            logger.info("  %s: %s", failure["doc_id"], failure["error"])

    uploaded = stats["new"] + stats["changed"] + stats["forced"]
    if uploaded > 0 and not args.dry_run:
        logger.info(
            "\n%d documents uploaded — run extract with --force to re-process them.",
            uploaded,
        )


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    """Load the document manifest YAML."""
    with open(path) as manifest_file:
        return yaml.safe_load(manifest_file)


if __name__ == "__main__":
    main()
