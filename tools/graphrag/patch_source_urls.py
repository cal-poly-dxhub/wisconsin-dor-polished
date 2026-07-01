"""Patch source_url on Neptune Document/Chunk nodes and S3 metadata.

Reads doc_id -> source_url mappings from docs_missing_source_url.txt,
then:
  1. Updates Neptune Document nodes: SET d.source_url = ...
  2. Updates Neptune Chunk nodes: SET c.source_url = ... WHERE c.doc_id = ...
  3. Updates S3 .metadata.json files with the new source_url

Usage:
  AWS_PROFILE=widor AWS_REGION=us-east-1 uv run python3 tools/graphrag/patch_source_urls.py --dry-run
  AWS_PROFILE=widor AWS_REGION=us-east-1 uv run python3 tools/graphrag/patch_source_urls.py
"""

import argparse
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import boto3


BUCKET = "wis-raw-bucket-c8e69250"
GRAPH_ID = "g-ndvl4j73v4"
MAPPING_FILE = Path(__file__).parent / "docs_missing_source_url.txt"


def execute_query(client, query: str, parameters: dict | None = None) -> list:
    kwargs = {
        "graphIdentifier": GRAPH_ID,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters

    for attempt in range(5):
        try:
            resp = client.execute_query(**kwargs)
            payload = resp.get("payload")
            if payload is None:
                return []
            data = json.loads(payload.read())
            return data.get("results", [])
        except client.exceptions.ThrottlingException:
            time.sleep(2 ** attempt)
    return []


def parse_mappings() -> list[dict]:
    """Parse the text file into doc_id -> source_url mappings."""
    mappings = []
    with open(MAPPING_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("doc_id"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[2]:
                mappings.append({
                    "doc_id": parts[0],
                    "s3_key": parts[1],
                    "source_url": parts[2],
                })
    return mappings


def patch_neptune(client, mappings: list[dict], dry_run: bool):
    """Update source_url on Document and Chunk nodes in Neptune."""
    print(f"\n=== Neptune patches ({len(mappings)} docs) ===")
    for m in mappings:
        doc_id = m["doc_id"]
        source_url = m["source_url"]

        if dry_run:
            print(f"  [DRY RUN] {doc_id} -> {source_url[:60]}...")
            continue

        doc_results = execute_query(
            client,
            "MATCH (d {id: $doc_id}) SET d.source_url = $url RETURN d.id AS updated",
            {"doc_id": doc_id, "url": source_url},
        )

        chunk_results = execute_query(
            client,
            "MATCH (c:Chunk {doc_id: $doc_id}) SET c.source_url = $url RETURN count(c) AS n",
            {"doc_id": doc_id, "url": source_url},
        )
        chunk_count = chunk_results[0]["n"] if chunk_results else 0

        status = "OK" if doc_results else "NO DOC NODE"
        print(f"  {doc_id} -> {status}, {chunk_count} chunks")


def patch_s3(s3, mappings: list[dict], dry_run: bool):
    """Update .metadata.json files in S3 with the new source_url."""
    print(f"\n=== S3 metadata patches ({len(mappings)} files) ===")

    def update_one(m):
        doc_id = m["doc_id"]
        s3_key = m["s3_key"]
        source_url = m["source_url"]
        meta_key = f"{s3_key}.metadata.json"

        if dry_run:
            print(f"  [DRY RUN] {meta_key}")
            return

        try:
            resp = s3.get_object(Bucket=BUCKET, Key=meta_key)
            data = json.loads(resp["Body"].read())
            data["metadataAttributes"]["source_url"] = source_url
            s3.put_object(
                Bucket=BUCKET,
                Key=meta_key,
                Body=json.dumps(data, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            print(f"  {doc_id} OK")
        except Exception as e:
            print(f"  ERROR {doc_id}: {e}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(update_one, mappings))


def main():
    parser = argparse.ArgumentParser(description="Patch source_url on Neptune and S3")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without applying")
    parser.add_argument("--neptune-only", action="store_true", help="Only patch Neptune")
    parser.add_argument("--s3-only", action="store_true", help="Only patch S3 metadata")
    args = parser.parse_args()

    mappings = parse_mappings()
    print(f"Parsed {len(mappings)} doc_id -> source_url mappings")

    if not mappings:
        print("Nothing to do.")
        return

    region = os.environ.get("AWS_REGION", "us-east-1")

    if not args.s3_only:
        neptune = boto3.client("neptune-graph", region_name=region)
        patch_neptune(neptune, mappings, args.dry_run)

    if not args.neptune_only:
        s3 = boto3.client("s3")
        patch_s3(s3, mappings, args.dry_run)

    print("\nDone!")


if __name__ == "__main__":
    main()
