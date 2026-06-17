"""
Upload local docs/ PDFs to the S3 raw bucket with proper metadata.

Maps each file to the correct category, framework_id, and authority_level
so the extract/embed/load pipeline processes them correctly.

Usage:
    python scripts/graphrag/upload_local_docs.py --bucket wis-raw-bucket-c8e69250 --profile wisco
    python scripts/graphrag/upload_local_docs.py --bucket wis-raw-bucket-c8e69250 --profile wisco --dry-run
"""

import argparse
import json
import os
import re

import boto3

DOCS_DIR = "docs"

# Map local directory structure to ingestion categories
LOCAL_SOURCES = [
    # (local_path, category_prefix, framework_id, authority_level, doc_type)
    (f"{DOCS_DIR}/state-constitution.pdf", "constitution", "FW-CONSTITUTION", 1, "constitution"),

    # State laws
    (f"{DOCS_DIR}/state-laws/", "statutes", "FW-STATUTES", 2, "statute"),

    # Admin rules
    (f"{DOCS_DIR}/administrative-rules/", "admin_rules", "FW-ADMIN-RULES", 4, "admin_rule"),

    # WPAM
    (f"{DOCS_DIR}/property-assessment-manual/", "wpam", "FW-WPAM", 5, "assessment_manual"),

    # Gov publications
    (f"{DOCS_DIR}/gov-publications/", "gov_publications", "FW-GOV-PUBS", 7, "guide"),

    # IAAO
    (f"{DOCS_DIR}/iaao/", "iaao", "FW-IAAO", 8, "iaao_standard"),

    # USPAP
    (f"{DOCS_DIR}/2024 USPAP Standards 1-4.pdf", "uspap", "FW-USPAP", 9, "uspap_standard"),
]


def make_doc_id(category: str, filename: str) -> str:
    """Generate a stable document ID from category and filename."""
    stem = os.path.splitext(filename)[0]
    clean = re.sub(r"[%\s.()]+", "-", stem).strip("-").lower()
    return f"{category}-{clean}"


def upload_doc(s3, bucket, prefix, filepath, category, framework_id, authority_level, doc_type, dry_run):
    """Upload a single document + metadata JSON to S3."""
    filename = os.path.basename(filepath)
    doc_id = make_doc_id(category, filename)
    ext = os.path.splitext(filename)[1].lower()

    doc_key = f"{prefix}{doc_id}/{doc_id}{ext}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{ext}.metadata.json"

    if dry_run:
        size = os.path.getsize(filepath)
        print(f"  [DRY RUN] {doc_id} ({size/1024:.0f} KB) -> s3://{bucket}/{doc_key}")
        return doc_key

    with open(filepath, "rb") as f:
        content = f.read()

    content_type = "application/pdf" if ext == ".pdf" else "application/octet-stream"
    s3.put_object(Bucket=bucket, Key=doc_key, Body=content, ContentType=content_type)

    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id,
            "doc_type": doc_type,
            "framework_id": framework_id,
            "authority_level": str(authority_level),
            "category": category,
            "source_url": "",
        }
    }
    s3.put_object(
        Bucket=bucket, Key=meta_key,
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"  {doc_id} ({len(content)/1024:.0f} KB) -> s3://{bucket}/{doc_key}")
    return doc_key


def main():
    parser = argparse.ArgumentParser(description="Upload local docs to S3 raw bucket")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 key prefix (default: raw/)")
    parser.add_argument("--profile", default=None, help="AWS profile name")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")

    total = 0
    uploaded = 0

    for source_path, category, fw_id, auth_level, doc_type in LOCAL_SOURCES:
        print(f"\n=== {category} (authority level {auth_level}) ===")

        if os.path.isfile(source_path):
            files = [source_path]
        elif os.path.isdir(source_path):
            files = sorted([
                os.path.join(source_path, f)
                for f in os.listdir(source_path)
                if f.endswith(".pdf")
            ])
        else:
            print(f"  WARNING: {source_path} not found, skipping")
            continue

        for filepath in files:
            total += 1
            upload_doc(s3, args.bucket, args.prefix, filepath,
                       category, fw_id, auth_level, doc_type, args.dry_run)
            uploaded += 1

    # Case law metadata stubs
    citations_file = f"{DOCS_DIR}/case-law-citations.json"
    if os.path.exists(citations_file):
        print(f"\n=== case_law (authority level 3) ===")
        with open(citations_file) as f:
            citations = json.load(f)

        # Skip N.W.2d/3d parallel reporter citations — they duplicate the
        # canonical Wis. 2d node for the same case.
        nw_re = re.compile(r"\d+ N\.W\.(?:2d|3d) \d+")
        skipped_nw = [e for e in citations if nw_re.search(e["citation"])]
        citations = [e for e in citations if not nw_re.search(e["citation"])]
        if skipped_nw:
            print(f"  Skipped {len(skipped_nw)} N.W.2d/3d parallel reporter citations")

        print(f"  {len(citations)} case law citations to upload as metadata stubs")

        for entry in citations:
            citation = entry["citation"]
            doc_id = make_doc_id("case-law", citation)
            total += 1

            stub = json.dumps({
                "citation": citation,
                "note": "Case law stub. Full opinion not downloaded. See scholar_url for source.",
                "scholar_url": entry["scholar_url"],
                "legis_url": entry["legis_url"],
                "citing_statutes": entry["sources"],
            }, indent=2).encode("utf-8")

            doc_key = f"{args.prefix}{doc_id}/{doc_id}.json"
            meta_key = f"{args.prefix}{doc_id}/{doc_id}.json.metadata.json"

            metadata = {
                "metadataAttributes": {
                    "doc_id": doc_id,
                    "doc_type": "case_law",
                    "framework_id": "FW-CASE-LAW",
                    "authority_level": "3",
                    "category": "case_law",
                    "citation": citation,
                    "source_url": entry["legis_url"],
                }
            }

            if args.dry_run:
                if total <= 5 or total == len(citations):
                    print(f"  [DRY RUN] {doc_id}")
                elif total == 6:
                    print(f"  ... ({len(citations) - 5} more)")
            else:
                s3.put_object(Bucket=args.bucket, Key=doc_key, Body=stub, ContentType="application/json")
                s3.put_object(Bucket=args.bucket, Key=meta_key,
                              Body=json.dumps(metadata, indent=2).encode("utf-8"),
                              ContentType="application/json")

            uploaded += 1

        if not args.dry_run:
            print(f"  Uploaded {len(citations)} case law stubs")

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Complete: {uploaded}/{total} uploaded")


if __name__ == "__main__":
    main()
