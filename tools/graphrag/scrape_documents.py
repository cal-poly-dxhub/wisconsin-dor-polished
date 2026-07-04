"""
Scrape documents from the manifest and upload to S3 with change detection.

Reads document_manifest.yaml (single source of truth for all corpus URLs),
downloads each document, compares content hash against S3 ETag, and only
uploads files that have changed. Produces a summary of new/changed/unchanged.

Usage:
    python tools/graphrag/scrape_documents.py \
        --bucket <raw-bucket-name> \
        --prefix raw/

    # Only scrape specific categories:
    python tools/graphrag/scrape_documents.py \
        --bucket <raw-bucket-name> --category statutes --category admin_rules

    # Force re-upload even if content matches:
    python tools/graphrag/scrape_documents.py \
        --bucket <raw-bucket-name> --force
"""

import argparse
import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import boto3
from botocore.exceptions import ClientError
import requests
import yaml

s3 = boto3.client("s3")

MANIFEST_PATH = Path(__file__).parent / "document_manifest.yaml"
SITEMAP_URL = "https://www.revenue.wi.gov/sitemap.xml"


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    """Load the document manifest YAML."""
    with open(path) as f:
        return yaml.safe_load(f)


def load_news_urls_from_sitemap(filter_re: re.Pattern) -> list[str]:
    """Pull news URLs from the WI DOR sitemap, filtered to news sections."""
    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    resp = requests.get(SITEMAP_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
    return sorted({url for url in locs if filter_re.search(url)})


_GENERIC_STEMS = {"home", "index", "default", "main", "page"}
_NEWS_PARENT_SEGMENTS = {"cotvc-news", "assessor-news"}


_STATUTE_RE = re.compile(r"ch\.\s*(\d+)", re.I)
_ADMIN_RULE_RE = re.compile(r"ch\.\s*Tax\s*(\d+)", re.I)

_IAAO_TYPOS = {"responibilities": "responsibilities", "comunication": "communication"}


def _split_camel_case(s: str) -> str:
    """Insert hyphens at CamelCase boundaries: 'StandardValuation' -> 'standard-valuation'."""
    s = re.sub(r"([a-z])([A-Z])", r"\1-\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", s)
    return s.lower()


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
        m = _STATUTE_RE.search(decoded_path)
        if m:
            return f"statutes-{m.group(1)}"

    # Admin rules: extract Tax chapter number to produce "admin_rules-tax-16"
    if category == "admin_rules":
        m = _ADMIN_RULE_RE.search(decoded_path)
        if m:
            return f"admin_rules-tax-{m.group(1)}"

    # WPAM: extract year suffix to produce "wpam-wisconsin-property-assessment-manual-2026"
    if category == "wpam":
        m = re.search(r"wpam(\d{2})", decoded_path, re.I)
        if m:
            year = int(m.group(1))
            full_year = 2000 + year if year < 50 else 1900 + year
            return f"wpam-wisconsin-property-assessment-manual-{full_year}"

    # IAAO: split CamelCase filenames and fix known typos in source URLs
    if category == "iaao":
        filename = Path(decoded_path).stem
        stem = _split_camel_case(filename)
        stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
        for typo, fix in _IAAO_TYPOS.items():
            stem = stem.replace(typo, fix)
        return f"iaao-{stem}"

    # USPAP: third-party URLs with useless filenames — derive from path keywords
    if category == "uspap":
        m = re.search(r"uspap", decoded_path, re.I)
        if m:
            year_match = re.search(r"20\d{2}", url)
            year = year_match.group(0) if year_match else ""
            suffix = f"-{year}" if year else ""
            return f"uspap-standards{suffix}"

    parts = [p for p in decoded_path.split("/") if p]
    filename = Path(decoded_path).stem
    clean_stem = re.sub(r"[^a-z0-9]+", "-", filename.lower()).strip("-")

    if clean_stem in _GENERIC_STEMS and len(parts) >= 2:
        parent = re.sub(r"[^a-z0-9]+", "-", parts[-2].lower()).strip("-")
        if parent:
            return f"{category}-{parent}-{clean_stem}"

    if len(parts) >= 2:
        parent = re.sub(r"[^a-z0-9]+", "-", parts[-2].lower()).strip("-")
        if parent in _NEWS_PARENT_SEGMENTS:
            return f"{category}-{parent}-{clean_stem}"

    return f"{category}-{clean_stem}"


# Date extraction for news pages
_NEWS_DATE_RE = re.compile(
    r"/(?:COTVC-News|Assessor-News)/"
    r"(?:"
    r"(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})[a-z]?"
    r"|(?P<y2>\d{4})(?P<m2>\d{2})(?P<d2>\d{2})"
    r")",
    re.I,
)


def extract_news_date(url: str) -> str | None:
    """Return ISO date string (YYYY-MM-DD) from a news URL, or None."""
    m = _NEWS_DATE_RE.search(url)
    if not m:
        return None
    if m.group("y1"):
        y, mo, d = int(m.group("y1")), int(m.group("m1")), int(m.group("d1"))
    else:
        y, mo, d = int(m.group("y2")), int(m.group("m2")), int(m.group("d2"))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def download_file(url: str, max_retries: int = 3) -> tuple[bytes, str]:
    """Download file with retries. Returns (content_bytes, content_type)."""
    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.content, ct
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{max_retries} for {url}: {e}")
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def scrape_html_page(url: str) -> tuple[bytes, str]:
    """Scrape an HTML page and return the main content as UTF-8 text."""
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    content = soup.find("div", id="ctl00_PlaceHolderMain_ctl01__ControlWrapper_RichHtmlField")
    if not content:
        content = soup.find("div", class_="ms-rtestate-field")
    if not content:
        content = soup.find("main") or soup.find("article") or soup.body

    text = content.get_text("\n", strip=True) if content else soup.get_text("\n", strip=True)
    return text.encode("utf-8"), "text/plain"


def content_changed(bucket: str, key: str, new_data: bytes) -> tuple[bool, bool]:
    """Check if content differs from S3. Returns (changed: bool, is_new: bool)."""
    new_hash = hashlib.md5(new_data).hexdigest()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        existing_etag = head["ETag"].strip('"')
        # Multipart uploads have ETags like "abc123-2" — can't compare MD5
        if "-" in existing_etag:
            existing = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            changed = hashlib.md5(existing).hexdigest() != new_hash
        else:
            changed = existing_etag != new_hash
        return changed, False
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return True, True
        raise


def upload_to_s3(bucket: str, prefix: str, doc_id: str, data: bytes, content_type: str, metadata: dict) -> str:
    """Upload document + metadata JSON to S3."""
    ext = ".pdf" if "pdf" in content_type else ".txt"
    doc_key = f"{prefix}{doc_id}/{doc_id}{ext}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{ext}.metadata.json"

    s3.put_object(Bucket=bucket, Key=doc_key, Body=data, ContentType=content_type)
    meta_json = json.dumps({"metadataAttributes": metadata}, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=meta_key, Body=meta_json, ContentType="application/json")

    return doc_key


def get_s3_key(prefix: str, doc_id: str, is_pdf: bool) -> str:
    """Compute the S3 key for a document."""
    ext = ".pdf" if is_pdf else ".txt"
    return f"{prefix}{doc_id}/{doc_id}{ext}"


def main():
    parser = argparse.ArgumentParser(description="Scrape documents from manifest to S3 with change detection")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be scraped without downloading")
    parser.add_argument("--force", action="store_true", help="Upload all documents regardless of hash match")
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

    # Hydrate sitemap-driven categories
    for category, config in sources.items():
        if config.get("sitemap_filter") and not config["urls"]:
            print(f"Loading {category} URLs from sitemap...")
            filter_re = re.compile(config["sitemap_filter"], re.I)
            config["urls"] = load_news_urls_from_sitemap(filter_re)
            print(f"  {len(config['urls'])} URLs from sitemap for {category}")

    total = sum(len(cat["urls"]) for cat in sources.values())
    print(f"Scraping {total} documents across {len(sources)} categories\n")

    processed = 0
    stats = {"new": 0, "changed": 0, "unchanged": 0, "forced": 0, "failed": 0}
    failed = []

    for category, config in sources.items():
        print(f"\n=== {category} (authority level {config['authority_level']}) ===")

        for entry in config["urls"]:
            # Entries can be plain URL strings or {url, doc_id} dicts
            if isinstance(entry, dict):
                url = entry["url"]
                explicit_id = entry.get("doc_id")
            else:
                url = entry
                explicit_id = None

            doc_id = make_doc_id(category, url, explicit_id)
            processed += 1

            if args.dry_run:
                eff_date = extract_news_date(url)
                date_part = f"  date={eff_date}" if eff_date else ""
                print(f"  [{processed}/{total}] Would scrape: {doc_id} <- {url}{date_part}")
                continue

            try:
                is_pdf = url.lower().endswith(".pdf") or "pdf" in url.lower().split("?")[0]
                if is_pdf:
                    data, ct = download_file(url)
                else:
                    data, ct = scrape_html_page(url)

                # Check if content actually changed
                s3_key = get_s3_key(args.prefix, doc_id, "pdf" in ct)
                if not args.force:
                    changed, is_new = content_changed(args.bucket, s3_key, data)
                    if not changed:
                        stats["unchanged"] += 1
                        print(f"  [{processed}/{total}] {doc_id} — unchanged")
                        continue
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

                eff_date = extract_news_date(url)
                if eff_date:
                    metadata["effective_date"] = eff_date

                doc_key = upload_to_s3(args.bucket, args.prefix, doc_id, data, ct, metadata)
                stats[status] += 1
                print(f"  [{processed}/{total}] {doc_id} — {status.upper()} -> s3://{args.bucket}/{doc_key}")

            except Exception as e:
                stats["failed"] += 1
                print(f"  [{processed}/{total}] {doc_id} — FAILED: {e}")
                failed.append({"doc_id": doc_id, "url": url, "error": str(e)})

            if args.sleep > 0 and processed < total:
                time.sleep(args.sleep)

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Complete: {processed} processed")
    print(f"  New: {stats['new']}, Changed: {stats['changed']}, "
          f"Forced: {stats['forced']}, Unchanged: {stats['unchanged']}, "
          f"Failed: {stats['failed']}")

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for f in failed:
            print(f"  {f['doc_id']}: {f['error']}")

    uploaded = stats["new"] + stats["changed"] + stats["forced"]
    if uploaded > 0 and not args.dry_run:
        print(f"\n{uploaded} documents uploaded — run extract with --force to re-process them.")


if __name__ == "__main__":
    main()
