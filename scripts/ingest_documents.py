#!/usr/bin/env python3
"""
S3 Document Upload Script

Uploads documents to S3 and creates corresponding metadata.json files
based on input JSON configuration.

Usage:
python3 ingest.py \
  --bucket <rag-bucket-name> \
  --prefix <bucket-prefix> \
  --input-file <path/to/input.json>
"""

import boto3
import json
import argparse
import requests
from urllib.parse import urlparse
from pathlib import Path

def download_file(url: str) -> bytes:
    """Download file from URL"""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def upload_to_s3(
    s3_client, bucket: str, key: str, data: bytes, content_type: str | None = None
):
    """Upload data to S3"""
    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    s3_client.put_object(Bucket=bucket, Key=key, Body=data, **extra_args)


def clear_bucket(s3_client, bucket: str, prefix: str = ""):
    """Clear all objects in bucket with given prefix"""
    print(f"️ Clearing bucket s3://{bucket}/{prefix}...")

    # List all objects with the prefix
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    objects_to_delete = []
    for page in pages:
        if "Contents" in page:
            objects_to_delete.extend([{"Key": obj["Key"]} for obj in page["Contents"]])

    if objects_to_delete:
        # Delete objects in batches of 1000 (S3 limit)
        for i in range(0, len(objects_to_delete), 1000):
            batch = objects_to_delete[i : i + 1000]
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        print(f"  ✅ Deleted {len(objects_to_delete)} objects")
    else:
        print(f"  ℹ️ No objects found to delete")


def sync_knowledge_base(s3_client, bucket: str, prefix: str, input_file: str):
    """Re-sync knowledge base with fresh data (without clearing bucket)"""
    print(" Starting knowledge base sync...")

    # Load and upload fresh documents
    print("📥 Loading document configuration...")
    with open(input_file, "r") as f:
        documents = json.load(f)

    print(f"📤 Uploading {len(documents)} documents...")
    for doc in documents:
        doc_id = doc["documentId"]
        metadata = doc["metadata"]
        url = metadata["url"]

        print(f"Processing {doc_id}...")

        try:
            # Download file
            file_data = download_file(url)

            # Determine filename from URL and use source ID with extension
            original_filename = Path(urlparse(url).path).name
            if not original_filename:
                original_filename = f"{doc_id}.pdf"

            # Get file extension from original filename
            file_extension = Path(original_filename).suffix
            if not file_extension:
                file_extension = ".pdf"  # Default to PDF if no extension found

            # Use source ID with appropriate extension
            filename = f"{doc_id}{file_extension}"

            # Upload document (will overwrite existing)
            doc_key = f"{prefix}{doc_id}/{filename}"
            content_type = get_content_type(filename)
            upload_to_s3(s3_client, bucket, doc_key, file_data, content_type)
            print(f"  ✅ Uploaded document: s3://{bucket}/{doc_key}")

            # Upload metadata (will overwrite existing)
            metadata_key = f"{prefix}{doc_id}/{filename}.metadata.json"
            wrapped_metadata = {"metadataAttributes": metadata}
            metadata_data = json.dumps(wrapped_metadata, indent=2).encode("utf-8")
            upload_to_s3(
                s3_client, bucket, metadata_key, metadata_data, "application/json"
            )
            print(f"  ✅ Uploaded metadata: s3://{bucket}/{metadata_key}")

        except Exception as e:
            print(f"  ❌ Failed to process {doc_id}: {str(e)}")

    print("✅ Knowledge base sync completed!")


def get_content_type(filename: str) -> str:
    """Get content type based on file extension"""
    ext = Path(filename).suffix.lower()
    content_types = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".html": "text/html",
        ".json": "application/json",
    }
    return content_types.get(ext, "application/octet-stream")


def main():
    parser = argparse.ArgumentParser(description="Upload documents to S3 with metadata")
    parser.add_argument(
        "--input-file", required=True, help="JSON file with document metadata"
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--prefix", required=True, help="S3 key prefix")
    parser.add_argument(
        "--clear-bucket", action="store_true", help="Clear bucket before uploading"
    )
    parser.add_argument(
        "--sync", action="store_true", help="Clear bucket and re-sync knowledge base"
    )
    args = parser.parse_args()

    # Initialize S3 client
    s3_client = boto3.client("s3")

    # Handle sync command
    if args.sync:
        sync_knowledge_base(s3_client, args.bucket, args.prefix, args.input_file)
        return

    # Handle regular upload with optional clear
    if args.clear_bucket:
        clear_bucket(s3_client, args.bucket, args.prefix)

    # Load configuration
    with open(args.input_file, "r") as f:
        documents = json.load(f)

    for doc in documents:
        doc_id = doc["documentId"]
        metadata = doc["metadata"]
        url = metadata["url"]

        print(f"Processing {doc_id}...")

        try:
            # Download file
            file_data = download_file(url)

            # Determine filename from URL and use source ID with extension
            original_filename = Path(urlparse(url).path).name
            if not original_filename:
                original_filename = f"{doc_id}.pdf"

            # Get file extension from original filename
            file_extension = Path(original_filename).suffix
            if not file_extension:
                file_extension = ".pdf"  # Default to PDF if no extension found

            # Use source ID with appropriate extension
            filename = f"{doc_id}{file_extension}"

            # Upload document
            doc_key = f"{args.prefix}{doc_id}/{filename}"
            content_type = get_content_type(filename)
            upload_to_s3(s3_client, args.bucket, doc_key, file_data, content_type)
            print(f"  ✅ Uploaded document: s3://{args.bucket}/{doc_key}")

            # Upload metadata
            metadata_key = f"{args.prefix}{doc_id}/{filename}.metadata.json"
            wrapped_metadata = {"metadataAttributes": metadata}
            metadata_data = json.dumps(wrapped_metadata, indent=2).encode("utf-8")
            upload_to_s3(
                s3_client, args.bucket, metadata_key, metadata_data, "application/json"
            )
            print(f"  ✅ Uploaded metadata: s3://{args.bucket}/{metadata_key}")

        except Exception as e:
            print(f"  ❌ Failed to process {doc_id}: {str(e)}")


if __name__ == "__main__":
    main()
