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
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests

s3 = boto3.client("s3")

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
        "authority_level": 3,
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
        "authority_level": 4,
        "doc_type": "assessment_manual",
        "urls": [
            "https://www.revenue.wi.gov/documents/wpam25.pdf",
        ],
    },
    "faq_pages": {
        "framework_id": "FW-FAQ",
        "authority_level": 5,
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
        "authority_level": 6,
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
        "authority_level": 6,
        "doc_type": "advisory",
        "urls": [
            "https://www.revenue.wi.gov/Pages/SLF/COTVC-News/2024-03-29.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2025-04-24.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/COTVC-News/2025-03-19.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-10-27.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-03-02.aspx",
            "https://www.revenue.wi.gov/Pages/Manufacturing/home.aspx",
            "https://www.revenue.wi.gov/Pages/RETr/Home.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assessor-certification.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assess-recert.aspx",
            "https://www.revenue.wi.gov/Pages/Apps/assessor-inquiry.aspx",
        ],
    },
}


def make_doc_id(category: str, url: str) -> str:
    """Generate a stable document ID from category and URL."""
    path = urlparse(url).path
    filename = Path(path).stem
    clean = re.sub(r"[%\s.]+", "-", filename).strip("-").lower()
    return f"{category}-{clean}"


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
    args = parser.parse_args()

    total = sum(len(cat["urls"]) for cat in DOCUMENT_SOURCES.values())
    print(f"Scraping {total} documents across {len(DOCUMENT_SOURCES)} categories\n")

    processed = 0
    failed = []

    for category, config in DOCUMENT_SOURCES.items():
        print(f"\n=== {category} (authority level {config['authority_level']}) ===")

        for url in config["urls"]:
            doc_id = make_doc_id(category, url)
            processed += 1

            if args.dry_run:
                print(f"  [{processed}/{total}] Would scrape: {doc_id} <- {url}")
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

                doc_key = upload_to_s3(args.bucket, args.prefix, doc_id, data, ct, metadata)
                print(f"    -> s3://{args.bucket}/{doc_key}")

            except Exception as e:
                print(f"    FAILED: {e}")
                failed.append({"doc_id": doc_id, "url": url, "error": str(e)})

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Complete: {processed - len(failed)}/{processed} succeeded")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for f in failed:
            print(f"  {f['doc_id']}: {f['error']}")


if __name__ == "__main__":
    main()
