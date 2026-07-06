"""Generate grid-manifest.json and grid-metadata.json for the visualizer.

grid-manifest.json — tile layout data (id, docId, auth, idx) + doc info. Loaded eagerly.
grid-metadata.json — per-chunk metadata (startPage, endPage, heading, subheading). Loaded lazily.

Usage:
    AWS_PROFILE=widor AWS_REGION=us-east-1 uv run python tools/visualizer/generate_grid_manifest.py \
        --graph-id g-ndvl4j73v4 \
        --output-dir frontend/public/data
"""

import argparse
import json
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")

AUTHORITY_LEVELS = {
    "constitution": 1,
    "statutes": 2,
    "case_law": 3,
    "admin_rules": 4,
    "wpam": 5,
    "faqs": 6,
    "gov_pubs": 7,
    "iaao": 8,
    "uspap": 9,
}


def get_client():
    return boto3.client("neptune-graph", region_name=REGION)


def query_neptune(client, graph_id: str, query: str, params: dict | None = None):
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if params:
        kwargs["parameters"] = params
    resp = client.execute_query(**kwargs)
    return resp["payload"].read()


def fetch_all_chunks(client, graph_id: str) -> list[dict]:
    """Fetch all chunk nodes with their metadata."""
    # Query chunks directly (no joins — doc_id is a property on the chunk)
    query = """
    MATCH (c:Chunk)
    RETURN c.id AS chunk_id,
           c.doc_id AS doc_id,
           c.chunk_index AS chunk_index,
           c.start_page AS start_page,
           c.end_page AS end_page,
           c.heading AS heading,
           c.subheading AS subheading,
           c.edition_year AS edition_year
    ORDER BY c.doc_id, c.chunk_index
    """
    raw = query_neptune(client, graph_id, query)
    data = json.loads(raw)
    chunks = data.get("results", [])

    # Fetch doc titles from the EXTRACTED_FROM targets
    doc_query = """
    MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d)
    RETURN DISTINCT c.doc_id AS doc_id, d.title AS doc_title, d.authority_level AS authority_level
    """
    raw2 = query_neptune(client, graph_id, doc_query)
    doc_data = json.loads(raw2).get("results", [])
    doc_info = {r["doc_id"]: r for r in doc_data if r.get("doc_id")}

    # Merge doc info onto chunks
    for chunk in chunks:
        info = doc_info.get(chunk["doc_id"], {})
        chunk["doc_title"] = info.get("doc_title")
        chunk["authority_level"] = info.get("authority_level")
        chunk["framework_id"] = None

    return chunks


def infer_authority(doc_id: str, framework_id: str | None, authority_level: int | None) -> int:
    """Infer authority level from available data."""
    if authority_level:
        return int(authority_level)
    if framework_id:
        for prefix, level in AUTHORITY_LEVELS.items():
            if framework_id.lower().startswith(prefix):
                return level
    for prefix, level in AUTHORITY_LEVELS.items():
        if doc_id.lower().startswith(prefix):
            return level
    return 7  # default to gov_pubs


def is_old_wpam(doc_id: str, edition_year: int | None) -> bool:
    """Check if this is a non-current WPAM edition."""
    if "wpam" not in doc_id.lower():
        return False
    if edition_year and edition_year < 2026:
        return True
    if "2026" not in doc_id:
        return any(str(y) in doc_id for y in range(2015, 2026))
    return False


def build_manifests(chunks: list[dict]) -> tuple[dict, dict]:
    """Build the manifest (layout) and metadata (per-chunk detail) from raw chunk data."""
    tiles = []
    metadata = {}
    docs_map: dict[str, dict] = {}

    for row in chunks:
        chunk_id = row["chunk_id"]
        doc_id = row["doc_id"]
        chunk_index = row.get("chunk_index", 0) or 0
        auth = infer_authority(doc_id, row.get("framework_id"), row.get("authority_level"))
        edition_year = row.get("edition_year")

        # Tile for manifest
        tiles.append({
            "id": chunk_id,
            "docId": doc_id,
            "auth": auth,
            "idx": int(chunk_index),
        })

        # Metadata for lazy loading
        meta_entry: dict = {}
        if row.get("start_page") is not None:
            meta_entry["sp"] = int(row["start_page"])
        if row.get("end_page") is not None:
            meta_entry["ep"] = int(row["end_page"])
        if row.get("heading"):
            meta_entry["h"] = row["heading"]
        if row.get("subheading"):
            meta_entry["sh"] = row["subheading"]
        if meta_entry:
            metadata[chunk_id] = meta_entry

        # Doc info
        if doc_id not in docs_map:
            docs_map[doc_id] = {
                "docId": doc_id,
                "title": row.get("doc_title") or doc_id,
                "auth": auth,
                "chunkCount": 0,
                "isOldWpam": is_old_wpam(doc_id, edition_year),
            }
        docs_map[doc_id]["chunkCount"] += 1

    manifest = {
        "totalChunks": len(tiles),
        "totalDocs": len(docs_map),
        "tileSize": 5,
        "tileGap": 1,
        "tiles": tiles,
        "docs": list(docs_map.values()),
    }

    return manifest, metadata


def upload_to_s3(bucket: str, key: str, local_path: str):
    """Upload a local file to S3."""
    s3 = boto3.client("s3", region_name=REGION)
    s3.upload_file(
        local_path,
        bucket,
        key,
        ExtraArgs={"ContentType": "application/json"},
    )


def main():
    parser = argparse.ArgumentParser(description="Generate visualizer grid manifests from Neptune")
    parser.add_argument("--graph-id", default="g-ndvl4j73v4", help="Neptune graph identifier")
    parser.add_argument("--output-dir", default="frontend/public/data", help="Local output directory")
    parser.add_argument("--bucket", default="wis-work-bucket-c8e69250", help="S3 bucket for upload")
    parser.add_argument("--s3-prefix", default="visualizer/", help="S3 key prefix")
    parser.add_argument("--no-upload", action="store_true", help="Skip S3 upload (local only)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print(f"Connecting to Neptune graph {args.graph_id} in {REGION}...")
    client = get_client()

    print("Fetching all chunks...")
    chunks = fetch_all_chunks(client, args.graph_id)
    print(f"  → {len(chunks)} chunks")

    print("Building manifests...")
    manifest, metadata = build_manifests(chunks)
    print(f"  → {manifest['totalChunks']} tiles, {manifest['totalDocs']} docs")
    print(f"  → {len(metadata)} chunks with metadata")

    manifest_path = os.path.join(args.output_dir, "grid-manifest.json")
    metadata_path = os.path.join(args.output_dir, "grid-metadata.json")

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, separators=(",", ":"))
    manifest_size = os.path.getsize(manifest_path)
    print(f"  → {manifest_path} ({manifest_size / 1024:.0f} KB)")

    with open(metadata_path, "w") as f:
        json.dump(metadata, f, separators=(",", ":"))
    metadata_size = os.path.getsize(metadata_path)
    print(f"  → {metadata_path} ({metadata_size / 1024:.0f} KB)")

    if not args.no_upload:
        print(f"\nUploading to s3://{args.bucket}/{args.s3_prefix}...")
        upload_to_s3(args.bucket, f"{args.s3_prefix}grid-manifest.json", manifest_path)
        print(f"  → s3://{args.bucket}/{args.s3_prefix}grid-manifest.json")
        upload_to_s3(args.bucket, f"{args.s3_prefix}grid-metadata.json", metadata_path)
        print(f"  → s3://{args.bucket}/{args.s3_prefix}grid-metadata.json")

    print("Done!")


if __name__ == "__main__":
    main()
