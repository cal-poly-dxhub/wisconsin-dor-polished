"""
Scrape all documents from wisco-doc-links.docx URLs and upload to S3.

Usage:
    python scripts/graphrag/scrape_documents.py \
        --bucket <raw-bucket-name> \
        --prefix raw/
"""

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests

s3 = boto3.client("s3")

SITEMAP_URL = "https://www.revenue.wi.gov/sitemap.xml"

DOCUMENT_SOURCES = {
    "constitution": {
        "framework_id": "FW-CONSTITUTION",
        "authority_level": 1,
        "doc_type": "constitution",
        "urls": [
            "https://docs.legis.wisconsin.gov/constitution/wi_unannotated",
        ],
    },
    "statutes": {
        "framework_id": "FW-STATUTES",
        "authority_level": 2,
        "doc_type": "statute",
        "urls": [
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2017.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2019.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2033.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2038.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2059.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2060.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2061.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2062.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2066.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2069.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2073.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2074.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2075.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2076.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2077.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2079.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20120.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20121.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20165.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20200.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20706.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20757.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20943.pdf",
        ],
    },
    "admin_rules": {
        "framework_id": "FW-ADMIN-RULES",
        "authority_level": 4,
        "doc_type": "admin_rule",
        "urls": [
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%206.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2012.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2015.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2016.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2018.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2019.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2020.pdf",
        ],
    },
    "wpam": {
        "framework_id": "FW-WPAM",
        "authority_level": 5,
        "doc_type": "assessment_manual",
        "urls": [
            "https://www.revenue.wi.gov/documents/wpam25.pdf",
        ],
    },
    "faq_pages": {
        "framework_id": "FW-FAQ",
        "authority_level": 6,
        "doc_type": "faq_page",
        "urls": [
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores6.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores2.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agforest.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores3.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores5.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores4.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-aar.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor5.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor3.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor4.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor2.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-lottcr.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-fdolcred.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ptrecred.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-slevytcr.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-useassmt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-usevalue.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-chargebk.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-finrep.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ead.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-excmptraid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-levy.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-newconst.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/Act-12-Personal-Property-Aid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ppaid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-wirmtxrpt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-soa.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-sot.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-nmomittx.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-taxempt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tiw.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-telco.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-setsh.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-waste.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-pp-exemption.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-allocation-amendments.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-annexations.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-audreport.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-basevalue.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-creation.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-devagree.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-extensions.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-general.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-jrboard.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-money.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-muniown.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-overlaps.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-parcels.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-projexp.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-projplan.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-pubnotif.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-taxincre.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-audterm.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-amends.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-tid-sect-6023.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-vallimit.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-internal.aspx",
        ],
    },
    "gov_publications": {
        "framework_id": "FW-GOV-PUBS",
        "authority_level": 7,
        "doc_type": "guide",
        "urls": [
            "https://www.revenue.wi.gov/DOR%20Publications/prop066.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb065.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pr115.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb061.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/tax18.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pa502.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb056.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/mobhme.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb062.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/chargeback-steps.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/omitted-taxes-steps.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb060.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pa600.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/tif-manual.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb218.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb238.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pm-201.pdf",
        ],
    },
    "complex_inquiry_pages": {
        "framework_id": "FW-GOV-PUBS",
        "authority_level": 7,
        "doc_type": "advisory",
        "urls": [
            "https://www.revenue.wi.gov/Pages/Manufacturing/home.aspx",
            "https://www.revenue.wi.gov/Pages/RETr/Home.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assessor-certification.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assess-recert.aspx",
            "https://www.revenue.wi.gov/Pages/Apps/assessor-inquiry.aspx",
        ],
    },
    # `news_pages` is sitemap-driven: URLs are fetched at runtime, not hardcoded.
    # Lives in DOCUMENT_SOURCES so it shows up in --category and the iteration loop.
    "news_pages": {
        "framework_id": "FW-GOV-PUBS",
        "authority_level": 7,
        "doc_type": "advisory",
        "sitemap_filter": re.compile(r"/Pages/SLF/(?:COTVC-News|Assessor-News)/", re.I),
        "urls": [],  # populated lazily by load_news_urls_from_sitemap()
    },
}


# Date format variants observed in news URLs (sample from sitemap, 2026-05):
#   YYYY-MM-DD                  e.g. 2025-06-02
#   YYYY-M-D / YYYY-MM-D        e.g. 2025-2-3, 2023-08-4
#   YYYYMMDD                    e.g. 20240117
#   YYYY-M-D{a,b,c}             same-day disambiguator suffix
#   YYYY-MM-DD-Slug-Words       title appended after date (rare, ~2 cases)
#
# We accept all of these and normalize to a real ISO date for the
# effective_date metadata attribute. Letter suffixes are intentionally
# discarded — they're sub-day ordering, not a calendar distinction.
_NEWS_DATE_RE = re.compile(
    r"/(?:COTVC-News|Assessor-News)/"
    r"(?:"
    r"(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})[a-z]?"  # YYYY-M-D[suffix]
    r"|(?P<y2>\d{4})(?P<m2>\d{2})(?P<d2>\d{2})"               # YYYYMMDD
    r")",
    re.I,
)


def extract_news_date(url: str) -> str | None:
    """Return ISO date string (YYYY-MM-DD) from a news URL, or None if not parseable."""
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
        return None  # garbage like 20203-05-15


def load_news_urls_from_sitemap(filter_re: re.Pattern) -> list[str]:
    """Pull news URLs from the WI DOR sitemap, filtered to news sections."""
    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    resp = requests.get(SITEMAP_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
    return sorted({url for url in locs if filter_re.search(url)})


_GENERIC_STEMS = {"home", "index", "default", "main", "page"}
_NEWS_PARENT_SEGMENTS = {"cotvc-news", "assessor-news"}


def make_doc_id(category: str, url: str) -> str:
    """Generate a stable document ID from category and URL.

    Default: `{category}-{filename-stem}`. Two disambiguation rules:

    1. Generic stems (home.aspx, index.aspx) get the parent path prepended
       so /Pages/Manufacturing/home.aspx ≠ /Pages/RETr/Home.aspx.
    2. News URLs get the section (cotvc-news/assessor-news) prepended,
       since both sections post on the same dates (~62 collisions in 2026-05
       sitemap). Filename-only naming would silently overwrite half the corpus.
    """
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    filename = Path(path).stem
    clean_stem = re.sub(r"[%\s.]+", "-", filename).strip("-").lower()

    if clean_stem in _GENERIC_STEMS and len(parts) >= 2:
        parent = re.sub(r"[%\s.]+", "-", parts[-2]).strip("-").lower()
        if parent:
            return f"{category}-{parent}-{clean_stem}"

    # News-section discriminator (rule 2 above).
    if len(parts) >= 2:
        parent = re.sub(r"[%\s.]+", "-", parts[-2]).strip("-").lower()
        if parent in _NEWS_PARENT_SEGMENTS:
            return f"{category}-{parent}-{clean_stem}"

    return f"{category}-{clean_stem}"


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
            time.sleep(2 ** attempt)
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


def upload_to_s3(bucket: str, prefix: str, doc_id: str, data: bytes, content_type: str, metadata: dict):
    """Upload document + metadata JSON to S3."""
    ext = ".pdf" if "pdf" in content_type else ".txt"
    doc_key = f"{prefix}{doc_id}/{doc_id}{ext}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{ext}.metadata.json"

    s3.put_object(Bucket=bucket, Key=doc_key, Body=data, ContentType=content_type)
    meta_json = json.dumps({"metadataAttributes": metadata}, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=meta_key, Body=meta_json, ContentType="application/json")

    return doc_key


def main():
    parser = argparse.ArgumentParser(description="Scrape all WI DOR documents to S3")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be scraped without downloading")
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="Only scrape this category (repeatable). Choices: "
        + ", ".join(sorted(DOCUMENT_SOURCES.keys())),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between requests (default: 0.5, be kind to revenue.wi.gov)",
    )
    args = parser.parse_args()

    if args.category:
        unknown = set(args.category) - set(DOCUMENT_SOURCES.keys())
        if unknown:
            parser.error(f"Unknown --category values: {sorted(unknown)}")
        sources = {k: v for k, v in DOCUMENT_SOURCES.items() if k in args.category}
    else:
        sources = DOCUMENT_SOURCES

    # Hydrate sitemap-driven categories now that we know which we'll scrape.
    for category, config in sources.items():
        if "sitemap_filter" in config and not config["urls"]:
            print(f"Loading {category} URLs from {SITEMAP_URL}...")
            config["urls"] = load_news_urls_from_sitemap(config["sitemap_filter"])
            print(f"  {len(config['urls'])} URLs from sitemap for {category}")

    total = sum(len(cat["urls"]) for cat in sources.values())
    print(f"Scraping {total} documents across {len(sources)} categories\n")

    processed = 0
    failed = []

    for category, config in sources.items():
        print(f"\n=== {category} (authority level {config['authority_level']}) ===")

        for url in config["urls"]:
            doc_id = make_doc_id(category, url)
            processed += 1

            if args.dry_run:
                eff_date = extract_news_date(url)
                date_part = f"  date={eff_date}" if eff_date else ""
                print(f"  [{processed}/{total}] Would scrape: {doc_id} <- {url}{date_part}")
                continue

            print(f"  [{processed}/{total}] {doc_id}")

            try:
                is_html = not url.lower().endswith(".pdf")
                if is_html:
                    data, ct = scrape_html_page(url)
                else:
                    data, ct = download_file(url)

                metadata = {
                    "doc_id": doc_id,
                    "source_url": url,
                    "doc_type": config["doc_type"],
                    "framework_id": config["framework_id"],
                    "authority_level": str(config["authority_level"]),
                    "category": category,
                }

                # News pages get their publication date stamped so the loader
                # can set Advisory.effective_date for date-based supersession.
                eff_date = extract_news_date(url)
                if eff_date:
                    metadata["effective_date"] = eff_date

                doc_key = upload_to_s3(args.bucket, args.prefix, doc_id, data, ct, metadata)
                print(f"    -> s3://{args.bucket}/{doc_key}")

            except Exception as e:
                print(f"    FAILED: {e}")
                failed.append({"doc_id": doc_id, "url": url, "error": str(e)})

            if args.sleep > 0 and processed < total:
                time.sleep(args.sleep)

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Complete: {processed - len(failed)}/{processed} succeeded")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for f in failed:
            print(f"  {f['doc_id']}: {f['error']}")


if __name__ == "__main__":
    main()
