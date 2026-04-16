"""
Phase 2: Document Extraction + LLM Classification

Pulls files from S3 raw bucket, routes each to appropriate parser,
classifies via LLM, normalizes IDs, deduplicates.

Usage:
    python scripts/graphrag/extract.py \
        --raw-bucket <raw-bucket> \
        --work-bucket <work-bucket> \
        --config scripts/graphrag/ingest_config.yaml
"""

import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from pdf_chunking.pdfChunker import process_pdf_from_s3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


LLM_CLASSIFY_PROMPT = """You are a document classifier for the Wisconsin Department of Revenue.
Given the text below, extract:

1. **doc_type** - one of: constitution, statute, admin_rule, assessment_manual, faq_page, guide, advisory, template
2. **title** - document title
3. **statute_refs** - list of Wisconsin Statute section references (e.g., ["70.32", "70.05", "73.03"])
4. **admin_rule_refs** - list of administrative rule references (e.g., ["Tax 18.05", "Tax 12.01"])
5. **implements_refs** - list of statutes this document operationally implements (e.g., ["70.32"] if this is a DOR policy that implements that statute)
6. **topics** - 1-5 topic keywords relevant to Wisconsin property assessment/taxation
7. **summary** - 2-3 sentence summary

Return valid JSON only. No markdown fencing.

---
TEXT:
{text_preview}
"""


def classify_document(text: str, model_id: str) -> dict:
    """Classify a document using Bedrock LLM."""
    preview = text[:4000]
    prompt = LLM_CLASSIFY_PROMPT.format(text_preview=preview)

    response = bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
    )

    result_text = response["output"]["message"]["content"][0]["text"]
    result_text = re.sub(r"^```(?:json)?\n?", "", result_text.strip())
    result_text = re.sub(r"\n?```$", "", result_text.strip())

    return json.loads(result_text)


STATUTE_REF_PATTERN = re.compile(
    r"(?:s+s?\.\s*|Wis\.?\s*Stat\.?\s*(?:[Ss]ec\.?\s*)?)"
    r"(\d+\.\d+[A-Za-z\-]*(?:\s*\(\d+[a-z]?\))*)",
)
ADMIN_REF_PATTERN = re.compile(r"(Tax\s+\d+\.\d+[^ ,;\n]*)")
BARE_SECTION_PATTERN = re.compile(r"(?<!\d)(\d{2,3}\.\d{2,4}[A-Za-z\-]*)(?!\d)")


def extract_chunk_citations(text: str) -> dict:
    """Extract statute and admin-rule references from a chunk's text via regex."""
    statute_refs = set()
    for m in STATUTE_REF_PATTERN.finditer(text):
        statute_refs.add(m.group(1).strip())
    for m in BARE_SECTION_PATTERN.finditer(text):
        ref = m.group(1)
        chapter = ref.split(".")[0]
        if chapter.isdigit() and 17 <= int(chapter) <= 77:
            statute_refs.add(ref)

    admin_refs = set()
    for m in ADMIN_REF_PATTERN.finditer(text):
        admin_refs.add(m.group(1).strip())

    return {
        "statute_refs": sorted(statute_refs),
        "admin_rule_refs": sorted(admin_refs),
    }


def extract_text_from_s3(bucket: str, key: str) -> str:
    """Read text content from S3."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8", errors="replace")


def get_metadata(bucket: str, key: str) -> dict:
    """Fetch metadata.json for a document."""
    meta_key = key + ".metadata.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=meta_key)
        data = json.loads(obj["Body"].read())
        return data.get("metadataAttributes", {})
    except Exception:
        return {}


def list_documents(bucket: str, prefix: str) -> list[dict]:
    """List all documents in the raw bucket, grouped by doc_id folder."""
    docs = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json"):
                continue
            parts = key.replace(prefix, "").split("/")
            if len(parts) >= 2:
                doc_id = parts[0]
                if doc_id not in docs:
                    docs[doc_id] = {"doc_id": doc_id, "key": key, "size": obj["Size"]}
    return list(docs.values())


def process_document(doc: dict, raw_bucket: str, work_bucket: str, config: dict) -> dict | None:
    """Process a single document: extract text, classify, chunk."""
    doc_id = doc["doc_id"]
    key = doc["key"]
    metadata = get_metadata(raw_bucket, key)

    logger.info(f"Processing {doc_id} ({doc['size']} bytes)")

    try:
        if key.endswith(".pdf"):
            source_url = metadata.get("source_url", "n/a")
            source_id = metadata.get("doc_id", doc_id)
            chunks = process_pdf_from_s3(
                raw_bucket, key,
                document_url=source_url,
                source_id=source_id,
            )
            full_text = "\n\n".join(c["text"] for c in chunks)
        else:
            full_text = extract_text_from_s3(raw_bucket, key)
            chunk_size = config.get("chunk_size", 2000)
            overlap = config.get("chunk_overlap", 200)
            chunks = []
            stride = chunk_size - overlap
            for i in range(0, len(full_text), stride):
                chunk_text = full_text[i : i + chunk_size]
                if len(chunk_text.strip()) < 50:
                    continue
                chunks.append({
                    "text": chunk_text,
                    "metadata": {
                        "doc_id": doc_id,
                        "source": key,
                        "source_url": metadata.get("source_url", "n/a"),
                        "chunk_index": len(chunks),
                        "start_page": None,
                        "end_page": None,
                    },
                })

        for chunk in chunks:
            citations = extract_chunk_citations(chunk["text"])
            chunk["metadata"]["statute_refs"] = citations["statute_refs"]
            chunk["metadata"]["admin_rule_refs"] = citations["admin_rule_refs"]

        llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-20250514")
        classification = classify_document(full_text, llm_model)

        result = {
            "doc_id": doc_id,
            "s3_key": key,
            "doc_type": metadata.get("doc_type", classification.get("doc_type", "guide")),
            "framework_id": metadata.get("framework_id", "FW-GOV-PUBS"),
            "authority_level": int(metadata.get("authority_level", 6)),
            "title": classification.get("title", doc_id),
            "summary": classification.get("summary", ""),
            "statute_refs": classification.get("statute_refs", []),
            "admin_rule_refs": classification.get("admin_rule_refs", []),
            "implements_refs": classification.get("implements_refs", []),
            "topics": classification.get("topics", []),
            "source_url": metadata.get("source_url", "n/a"),
            "full_text": full_text,
            "chunks": chunks,
        }

        cache_key = f"extracted/{doc_id}.json"
        cache_data = {k: v for k, v in result.items() if k != "full_text"}
        s3.put_object(
            Bucket=work_bucket,
            Key=cache_key,
            Body=json.dumps(cache_data, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(f"  Extracted {doc_id}: {len(chunks)} chunks, type={result['doc_type']}")
        return result

    except Exception as e:
        logger.error(f"  FAILED {doc_id}: {e}", exc_info=True)
        return None


def deduplicate(documents: list[dict]) -> list[dict]:
    """Deduplicate by doc_id, keeping the version with longest text."""
    by_id = {}
    for doc in documents:
        did = doc["doc_id"]
        if did not in by_id or len(doc.get("full_text", "")) > len(by_id[did].get("full_text", "")):
            by_id[did] = doc
    return list(by_id.values())


def main():
    parser = argparse.ArgumentParser(description="Extract and classify documents for GraphRAG")
    parser.add_argument("--raw-bucket", required=True, help="S3 bucket with raw documents")
    parser.add_argument("--work-bucket", required=True, help="S3 bucket for intermediate cache")
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args()

    config = load_config(args.config)

    docs = list_documents(args.raw_bucket, "raw/")
    logger.info(f"Found {len(docs)} documents to process")

    results = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(process_document, doc, args.raw_bucket, args.work_bucket, config): doc
            for doc in docs
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    results = deduplicate(results)
    logger.info(f"Extraction complete: {len(results)} documents after dedup")

    manifest = [
        {k: v for k, v in doc.items() if k not in ("full_text", "chunks")}
        for doc in results
    ]
    manifest_key = "extracted/manifest.json"
    s3.put_object(
        Bucket=args.work_bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"Manifest saved to s3://{args.work_bucket}/{manifest_key}")


if __name__ == "__main__":
    main()
