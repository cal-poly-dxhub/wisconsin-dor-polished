"""
S3 Ingest Chunks Script

Usage:
python3 ingest_chunks.py \
  --source-bucket <documents-source-bucket-name> \
  --dest-bucket <rag-bucket> \
  --prefix sources/
"""

import os
import json
import boto3
import argparse
import botocore
from datetime import datetime
from pdf_chunking.pdfChunker import process_pdf_from_s3

# === AWS CONFIG ===
s3 = boto3.client("s3")
session = boto3.session.Session()
REGION_NAME = session.region_name

LOG_FILE = "chunk_upload_summary.json"

# === HELPERS ===
def ensure_bucket_exists(s3_client, bucket_name: str):
    """Fail if bucket does not exist or is not accessible."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
    except botocore.exceptions.ClientError as e:
        raise RuntimeError(
            f"Bucket '{bucket_name}' does not exist or is not accessible.\n"
            f"Use the exact bucket name created by CDK.\n"
            f"Details: {e}"
        )
def list_all_pdfs(bucket: str, prefix: str = "sources/") -> list:
    """List all PDFs in the source bucket."""
    pdfs = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(".pdf"):
                pdfs.append(key)
    return pdfs


def get_metadata(bucket: str, key: str):
    """Fetch metadata.json and return (url, source_id)."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read())
        meta = data.get("metadataAttributes", {})
        source_url = data.get("source_url") or data.get("url") or meta.get("url") or "n/a"
        source_id = meta.get("sourceId") or "n/a"
        return source_url, source_id
    except Exception:
        print(f"⚠️ No metadata.json found for {key}, defaulting to 'n/a'")
        return "n/a", "n/a"


def log_pdf_summary(entry):
    """Append PDF-level summary to a local JSON log file."""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    else:
        logs = []

    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)


def upload_chunk(chunk: dict, index: int, pdf_key: str):
    """Upload one chunk (text + metadata) to DEST_BUCKET with readable names and no .txt extension."""
    doc_id = chunk["metadata"].get("doc_id", os.path.basename(pdf_key))
    base_name = os.path.splitext(os.path.basename(doc_id))[0].replace(" ", "_")
    chunk_name = f"{base_name}_chunk_{index:03d}"
    meta_key = f"{chunk_name}.metadata.json"

    # Upload the text chunk (no file extension)
    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=chunk_name,
        Body=chunk["text"].encode("utf-8"),
        ContentType="text/plain",
        Metadata={"doc_id": doc_id},
    )

    # Upload metadata
    metadata = {"metadataAttributes": chunk["metadata"]}
    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=meta_key,
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return chunk_name


def process_and_upload_pdf(pdf_key: str):
    """Process one PDF, chunk it, and upload all chunks."""
    print(f"\n📄 Processing {pdf_key}")
    meta_key = pdf_key + ".metadata.json"
    source_url, source_id = get_metadata(SOURCE_BUCKET, meta_key)

    start_time = datetime.utcnow().isoformat() + "Z"
    try:
        chunks = process_pdf_from_s3(
            SOURCE_BUCKET,
            pdf_key,
            document_url=source_url,
            source_id=source_id,
        )
    except Exception as e:
        print(f"❌ Failed to process {pdf_key}: {e}")
        log_pdf_summary({
            "timestamp": start_time,
            "pdf_key": pdf_key,
            "chunks_extracted": 0,
            "chunks_uploaded": 0,
            "status": "failed",
            "error": str(e)
        })
        return

    print(f"✅ Extracted {len(chunks)} chunks from {pdf_key}")

    uploaded = 0
    for i, chunk in enumerate(chunks):
        try:
            upload_chunk(chunk, i, pdf_key)
            uploaded += 1
        except Exception as e:
            print(f"⚠️ Upload failed for chunk {i}: {e}")

    status = "success" if uploaded == len(chunks) else "partial"

    log_pdf_summary({
        "timestamp": start_time,
        "pdf_key": pdf_key,
        "chunks_extracted": len(chunks),
        "chunks_uploaded": uploaded,
        "status": status
    })

    print(f"🚀 Uploaded {uploaded}/{len(chunks)} chunks for {pdf_key}")


# === MAIN ===
def main():
    parser = argparse.ArgumentParser(description="Run custom chunking on PDFs in S3")
    parser.add_argument("--source-bucket", required=True, help="S3 bucket with source PDFs")
    parser.add_argument("--dest-bucket", required=True, help="S3 bucket for chunk output")
    parser.add_argument(
        "--prefix",
        default="sources/",
        help="Prefix in source bucket where PDFs are stored (default: sources/)",
    )

    args = parser.parse_args()

    global SOURCE_BUCKET, DEST_BUCKET
    SOURCE_BUCKET = args.source_bucket
    DEST_BUCKET = args.dest_bucket

    ensure_bucket_exists(s3, DEST_BUCKET)

    print(f"🔍 Listing PDFs in {SOURCE_BUCKET}/{args.prefix}")
    pdf_keys = list_all_pdfs(SOURCE_BUCKET, args.prefix)
    print(f"Found {len(pdf_keys)} PDFs to process.\n")

    for pdf_key in pdf_keys:
        try:
            process_and_upload_pdf(pdf_key)
        except Exception as e:
            print(f"❌ Error processing {pdf_key}: {e}")
            log_pdf_summary({
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "pdf_key": pdf_key,
                "chunks_extracted": 0,
                "chunks_uploaded": 0,
                "status": "failed",
                "error": str(e)
            })


if __name__ == "__main__":
    main()