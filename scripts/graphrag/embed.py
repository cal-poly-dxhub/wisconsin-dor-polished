"""
Phase 4: Embed chunks and documents using Amazon Titan Embed v2.

Usage:
    python scripts/graphrag/embed.py \
        --work-bucket <work-bucket> \
        --config scripts/graphrag/ingest_config.yaml
"""

import argparse
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def embed_text(text: str, model_id: str, dimension: int = 1024, max_retries: int = 6) -> list[float]:
    """Embed text using Titan Embed v2 with exponential backoff."""
    truncated = text[:8000]

    body = json.dumps({
        "inputText": truncated,
        "dimensions": dimension,
        "normalize": True,
    })

    for attempt in range(max_retries):
        try:
            response = bedrock.invoke_model(
                modelId=model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            return result["embedding"]
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = min(30, 2 ** attempt)
            logger.warning(f"Embed retry {attempt + 1}/{max_retries}: {e}, waiting {wait}s")
            time.sleep(wait)

    raise RuntimeError("unreachable")


def load_extracted_docs(work_bucket: str) -> list[dict]:
    """Load all extracted document JSONs from the work bucket in parallel."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=work_bucket, Prefix="extracted/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != "extracted/manifest.json":
                keys.append(key)
    logger.info(f"Found {len(keys)} extracted JSONs; downloading in parallel...")

    def fetch(key: str) -> dict:
        return json.loads(s3.get_object(Bucket=work_bucket, Key=key)["Body"].read())

    docs: list[dict] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for i, doc in enumerate(pool.map(fetch, keys), start=1):
            docs.append(doc)
            if i % 500 == 0 or i == len(keys):
                logger.info(f"  Loaded {i}/{len(keys)} extracted JSONs")
    return docs


def embed_chunks(doc: dict, model_id: str, dimension: int) -> dict:
    """Embed all chunks for a single document.

    Case-law documents are thin citation stubs: no chunks to embed and no
    doc-level embedding either. Skipping the doc embedding keeps them out of
    Phase 11 semantic-edge discovery, so the agent can only reach them via
    inbound CITES edges from statute chunks — never via vector similarity.
    """
    doc_id = doc["doc_id"]
    chunks = doc.get("chunks", [])

    if doc.get("doc_type") == "case_law":
        return doc

    for i, chunk in enumerate(chunks):
        embedding = embed_text(chunk["text"], model_id, dimension)
        chunk["embedding"] = embedding
        if (i + 1) % 50 == 0:
            logger.info(f"  {doc_id}: embedded {i + 1}/{len(chunks)} chunks")

    doc_text = f"{doc.get('title', '')} {doc.get('summary', '')} "
    if chunks:
        doc_text += chunks[0]["text"][:2000]
    doc["doc_embedding"] = embed_text(doc_text, model_id, dimension)

    return doc


def list_already_embedded(bucket: str) -> set[str]:
    """Return set of doc_ids that already have embedding output in the work bucket."""
    embedded = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="embedded/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                doc_id = key.removeprefix("embedded/").removesuffix(".json")
                embedded.add(doc_id)
    return embedded


def main():
    parser = argparse.ArgumentParser(description="Embed documents and chunks for GraphRAG")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--max-workers", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="Re-embed all documents, ignoring cache")
    args = parser.parse_args()

    config = load_config(args.config)
    model_id = config.get("bedrock_embed_model", "amazon.titan-embed-text-v2:0")
    dimension = config.get("embed_dimension", 1024)

    docs = load_extracted_docs(args.work_bucket)
    logger.info(f"Loaded {len(docs)} extracted documents")

    if not args.force:
        already_done = list_already_embedded(args.work_bucket)
        before = len(docs)
        docs = [d for d in docs if d["doc_id"] not in already_done]
        logger.info(f"Skipping {before - len(docs)} already-embedded documents, {len(docs)} remaining")

    total_chunks = sum(len(d.get("chunks", [])) for d in docs)
    logger.info(f"Total chunks to embed: {total_chunks}")

    embedded_count = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(embed_chunks, doc, model_id, dimension): doc["doc_id"]
            for doc in docs
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                result = future.result()
                n_chunks = len(result.get("chunks", []))
                embedded_count += n_chunks

                cache_key = f"embedded/{result['doc_id']}.json"
                s3.put_object(
                    Bucket=args.work_bucket,
                    Key=cache_key,
                    Body=json.dumps(result, default=str).encode("utf-8"),
                    ContentType="application/json",
                )
                logger.info(f"Embedded {doc_id}: {n_chunks} chunks ({embedded_count}/{total_chunks} total)")

            except Exception as e:
                logger.error(f"FAILED embedding {doc_id}: {e}", exc_info=True)

    logger.info(f"Embedding complete: {embedded_count}/{total_chunks} chunks embedded")


if __name__ == "__main__":
    main()
