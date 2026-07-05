#!/usr/bin/env python3
"""Simulate the ingestion chunking pipeline locally for any document(s).

Runs the same code path as Fargate extract (process_pdf_from_s3) and writes
final chunks to pdf_chunking/chunk_logs/ for inspection in the admin chunk
visualizer.

Usage:
    # Single document (by doc_id prefix in the raw bucket):
    uv run tools/simulate_chunking.py statutes-70

    # Multiple documents:
    uv run tools/simulate_chunking.py statutes-70 statutes-74 wpam-7

    # All statutes:
    uv run tools/simulate_chunking.py --filter statutes-

    # All documents:
    uv run tools/simulate_chunking.py --all

    # Show quality report without writing files:
    uv run tools/simulate_chunking.py statutes-70 --report-only

    # Custom output directory:
    uv run tools/simulate_chunking.py statutes-70 --output-dir /tmp/chunks

Environment:
    AWS_PROFILE  (default: widor)
    AWS_REGION   (default: us-east-1)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import boto3

RAW_BUCKET = "wis-raw-bucket-c8e69250"


def list_documents(s3, bucket: str, prefix_filter: str = "") -> list[dict]:
    """List PDF documents in the raw bucket, optionally filtered by prefix."""
    paginator = s3.get_paginator("list_objects_v2")
    docs = []
    for page in paginator.paginate(Bucket=bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".pdf"):
                continue
            parts = key.split("/")
            if len(parts) < 3:
                continue
            doc_id = parts[1]
            if prefix_filter and not doc_id.startswith(prefix_filter):
                continue
            docs.append({"doc_id": doc_id, "key": key, "size": obj["Size"]})
    return docs


def resolve_doc_keys(s3, bucket: str, doc_ids: list[str]) -> list[dict]:
    """Resolve doc_id arguments to full S3 keys."""
    docs = []
    for doc_id in doc_ids:
        key = f"raw/{doc_id}/{doc_id}.pdf"
        try:
            resp = s3.head_object(Bucket=bucket, Key=key)
            docs.append({"doc_id": doc_id, "key": key, "size": resp["ContentLength"]})
        except s3.exceptions.ClientError:
            found = False
            for suffix in ["-document", ""]:
                alt_id = f"{doc_id}{suffix}" if suffix else doc_id
                alt_key = f"raw/{alt_id}/{alt_id}.pdf"
                try:
                    resp = s3.head_object(Bucket=bucket, Key=alt_key)
                    docs.append({"doc_id": alt_id, "key": alt_key, "size": resp["ContentLength"]})
                    found = True
                    break
                except s3.exceptions.ClientError:
                    continue
            if not found:
                print(f"  WARNING: could not find '{doc_id}' in s3://{bucket}/raw/")
    return docs


def print_quality_report(chunks: list[dict], doc_id: str):
    """Print a quality summary for the chunked document."""
    total = len(chunks)
    if total == 0:
        print(f"  {doc_id}: 0 chunks (empty)")
        return

    lengths = [len(c["text"]) for c in chunks]
    short = [c for c in chunks if len(c["text"]) < 100]
    oversized = [c for c in chunks if len(c["text"]) > 3000]
    avg = sum(lengths) / len(lengths)
    median = sorted(lengths)[len(lengths) // 2]

    print(f"  {doc_id}: {total} chunks | avg {avg:.0f} chars | median {median} chars")
    if short:
        print(f"    {len(short)} short (<100 chars):")
        for c in short[:5]:
            print(f'      {c["chunk_id"]}: ({len(c["text"])} chars) "{c["text"][:60]}"')
        if len(short) > 5:
            print(f"      ... and {len(short) - 5} more")
    if oversized:
        print(f"    {len(oversized)} oversized (>3000 chars):")
        for c in oversized[:3]:
            print(f"      {c['chunk_id']}: {len(c['text'])} chars")


def main():
    parser = argparse.ArgumentParser(
        description="Simulate the ingestion chunking pipeline locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "doc_ids",
        nargs="*",
        help="Document IDs to process (e.g. statutes-70, wpam-7)",
    )
    parser.add_argument(
        "--filter",
        dest="prefix_filter",
        default="",
        help="Process all docs matching this prefix (e.g. statutes-, wpam-)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all documents in the raw bucket",
    )
    parser.add_argument(
        "--output-dir",
        default="pdf_chunking/chunk_logs",
        help="Output directory for chunk logs (default: pdf_chunking/chunk_logs)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print quality report without writing chunk files",
    )
    parser.add_argument(
        "--bucket",
        default=RAW_BUCKET,
        help=f"S3 bucket (default: {RAW_BUCKET})",
    )
    args = parser.parse_args()

    if not args.doc_ids and not args.prefix_filter and not args.all:
        parser.error("Provide doc_ids, --filter, or --all")

    session = boto3.Session()
    s3 = session.client("s3")

    # Resolve documents to process
    if args.all:
        docs = list_documents(s3, args.bucket)
    elif args.prefix_filter:
        docs = list_documents(s3, args.bucket, args.prefix_filter)
    else:
        docs = resolve_doc_keys(s3, args.bucket, args.doc_ids)

    if not docs:
        print("No documents found.")
        return

    print(f"Processing {len(docs)} document(s)...\n")

    # Import after env setup so module-level AWS clients get the right region
    import tools.ingestion.chunking.pdfChunker as chunker_module
    from tools.ingestion.chunking.pdfChunker import process_pdf_from_s3

    if args.report_only:
        chunker_module.DEBUG = False
    else:
        # Clear output dirs before writing
        for subdir in ["final_chunks", "raw_chunks", "removed"]:
            d = Path(args.output_dir) / subdir
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
            d.mkdir(parents=True, exist_ok=True)

    results = []
    for doc in docs:
        t0 = time.time()
        try:
            meta_key = doc["key"] + ".metadata.json"
            try:
                meta_resp = s3.get_object(Bucket=args.bucket, Key=meta_key)
                metadata = json.loads(meta_resp["Body"].read())
                source_id = metadata.get("doc_id", doc["doc_id"])
                source_url = metadata.get("source_url", "n/a")
            except Exception:
                source_id = doc["doc_id"]
                source_url = "n/a"

            chunks = process_pdf_from_s3(
                args.bucket,
                doc["key"],
                document_url=source_url,
                source_id=source_id,
            )
            elapsed = time.time() - t0
            results.append({"doc_id": doc["doc_id"], "chunks": chunks, "elapsed": elapsed})
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  ERROR processing {doc['doc_id']}: {e} ({elapsed:.1f}s)")
            results.append(
                {"doc_id": doc["doc_id"], "chunks": [], "elapsed": elapsed, "error": str(e)}
            )

    # Quality report
    print("\n" + "=" * 60)
    print("QUALITY REPORT")
    print("=" * 60)
    total_chunks = 0
    total_short = 0
    for r in results:
        if r["chunks"]:
            print_quality_report(r["chunks"], r["doc_id"])
            total_chunks += len(r["chunks"])
            total_short += sum(1 for c in r["chunks"] if len(c["text"]) < 100)

    print(
        f"\n  TOTAL: {total_chunks} chunks across {len(results)} docs, {total_short} short (<100 chars)"
    )

    if not args.report_only:
        final_dir = Path(args.output_dir) / "final_chunks"
        print(f"\n  Output: {final_dir}/")


if __name__ == "__main__":
    main()
