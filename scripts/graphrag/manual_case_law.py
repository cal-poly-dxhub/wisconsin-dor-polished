"""
Manual case law uploader for remaining missing citations.

Prompts you with each citation, you find and paste the opinion text.

Usage:
    python scripts/graphrag/manual_case_law.py --bucket wis-raw-bucket-c8e69250
"""

import json
import os
import re
import sys

import boto3

s3 = boto3.client("s3")

CITATIONS_FILE = "docs/case-law-citations.json"
PROGRESS_FILE = "docs/case-law-fetch-progress.json"

MISSING = [
    "1 N.W.3d 761", "11 N.W.3d 160", "187 Wis. 2d 501", "19 N.W.3d 686",
    "2025 WI App 16", "2025 WI App 21", "2025 WI App 33", "21 N.W.3d 803",
    "388 Wis. 2d 395", "398 Wis. 2d 542", "399 Wis. 2d 769",
    "409 Wis. 2d 159", "411 Wis. 2d 622", "413 Wis. 2d 140",
    "415 Wis. 2d 542", "416 Wis. 2d 476", "5 N.W.3d 949", "5 N.W.3d 952",
    "654 F. Supp. 3d 807", "693 F. Supp. 3d 975", "933 N.W.2d 120",
    "967 N.W.2d 185", "996 N.W.2d 101", "998 N.W.2d 506",
]


def make_case_doc_id(citation):
    clean = re.sub(r"[%\s.]+", "-", citation).strip("-").lower()
    return f"case-law-{clean}"


def upload(bucket, prefix, doc_id, citation, case_name, text, entry):
    doc_key = f"{prefix}{doc_id}/{doc_id}.txt"
    meta_key = f"{prefix}{doc_id}/{doc_id}.txt.metadata.json"
    s3.put_object(Bucket=bucket, Key=doc_key, Body=text.encode("utf-8"), ContentType="text/plain")
    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id, "doc_type": "case_law", "framework_id": "FW-CASE-LAW",
            "authority_level": "3", "category": "case_law", "citation": citation,
            "case_name": case_name, "source_url": entry.get("legis_url", ""),
            "scholar_url": entry.get("scholar_url", ""),
            "citing_statutes": json.dumps(
                [{"file": s["file"], "pages": s["pages"]} for s in entry.get("sources", [])]
            ),
        }
    }
    s3.put_object(Bucket=bucket, Key=meta_key, Body=json.dumps(metadata, indent=2).encode("utf-8"), ContentType="application/json")
    return doc_key


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="raw/")
    args = parser.parse_args()

    with open(CITATIONS_FILE) as f:
        all_citations = {c["citation"]: c for c in json.load(f)}
    with open(PROGRESS_FILE) as f:
        progress = json.load(f)

    remaining = [c for c in MISSING if make_case_doc_id(c) not in progress["completed"]]
    print(f"\n{len(remaining)} cases to go. Type SKIP to skip, QUIT to stop.\n")

    for i, citation in enumerate(remaining):
        doc_id = make_case_doc_id(citation)
        entry = all_citations.get(citation, {})

        print(f"[{i+1}/{len(remaining)}] {citation}")
        print(f"  Scholar: {entry.get('scholar_url', 'N/A')}")

        case_name = input("  Case name (or SKIP/QUIT): ").strip()
        if case_name.upper() == "QUIT":
            break
        if case_name.upper() == "SKIP":
            continue

        print("  Paste opinion text below. When done, type END on its own line:")
        lines = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)

        text = "\n".join(lines).strip()
        if len(text) < 100:
            print(f"  Too short ({len(text)} chars), skipping\n")
            continue

        key = upload(args.bucket, args.prefix, doc_id, citation, case_name, text, entry)
        print(f"  ✓ Uploaded {len(text)} chars\n")

        progress["completed"][doc_id] = {"citation": citation, "source": "manual", "has_text": True}
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)

    done = len([c for c in MISSING if make_case_doc_id(c) in progress["completed"]])
    print(f"\nDone! {done}/{len(MISSING)} manual cases uploaded. {len(progress['completed'])} total.")


if __name__ == "__main__":
    main()
