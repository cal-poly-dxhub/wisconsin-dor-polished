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
from pathlib import Path

import boto3
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_chunking.pdfChunker import process_pdf_from_s3

try:
    from tools.graphrag.case_annotations import extract_section_for_page
except ImportError:
    from case_annotations import extract_section_for_page

# Local mirror of the statute PDFs the case-law metadata references.
# We need these to read the running header that identifies which section
# owns each page (used to derive section-level statute_refs for case-law
# nodes — without this every case-law CITES edge points at the chapter,
# which makes section-anchored agent queries miss the case).
DEFAULT_STATE_LAWS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "state-laws"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def resolve_authority_level(
    metadata: dict, framework_id: str, config: dict
) -> int | None:
    """Resolve a document's authority level without defaulting to FAQ.

    Precedence:
      1. An explicit ``authority_level`` in the doc's metadata.
      2. The canonical level of the doc's framework (single source of truth
         in ingest_config.yaml).
      3. None — never a misleading concrete default. The old code defaulted
         general docs to 6 (FAQ) and case-law to 3, so a doc with missing
         metadata silently inherited the wrong authority downstream.
    """
    explicit = metadata.get("authority_level")
    if explicit is not None:
        return int(explicit)

    framework_levels = {
        fw["id"]: fw["authority_level"] for fw in config.get("frameworks", [])
    }
    return framework_levels.get(framework_id)


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


def _parse_citing_statutes(metadata: dict) -> list[dict]:
    """Decode the citing_statutes metadata attribute.

    Scrapers store this as a JSON string (S3 metadata attributes are strings
    only). Returns a list of {"file", "pages"} dicts, or [] if absent/invalid.
    """
    raw = metadata.get("citing_statutes") or ""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        {"file": item["file"], "pages": item.get("pages", [])}
        for item in parsed
        if isinstance(item, dict) and "file" in item
    ]


def _derive_case_statute_refs(
    citing_statutes: list[dict], state_laws_dir: Path
) -> list[str]:
    """Build the case-law document's statute_refs list.

    Returns the union of:
      - chapter-level refs derived from the citing-statute filename ("70")
      - section-level refs derived by reading each cited page's running
        header in the local statute PDF ("70.32")

    Section-level refs are the lever that makes case-law reachable from a
    section node like WIS-STAT-70.32 — without them, the agent must walk
    PART_OF up to WIS-STAT-70 to discover any cases at all (Markarian was
    invisible to vector_search ranking on §70.32 chunks even though its
    annotation lives there).

    When the local PDF mirror is missing or page detection fails, falls back
    silently to chapter-only refs, matching prior behavior.
    """
    refs: set[str] = set()
    for src in citing_statutes:
        filename = src.get("file", "")
        chapter = _statute_file_to_chapter(filename)
        if not chapter:
            continue
        refs.add(chapter)

        pdf_path = state_laws_dir / filename
        if not pdf_path.exists():
            continue
        for page in src.get("pages", []) or []:
            try:
                section = extract_section_for_page(
                    pdf_path, int(page), expected_chapter=chapter
                )
            except (ValueError, TypeError):
                continue
            if section:
                refs.add(section)
    return sorted(refs)


def process_case_law_document(
    doc: dict, raw_bucket: str, metadata: dict, config: dict
) -> dict | None:
    """Build a thin-stub extraction result for a case-law document.

    Case-law nodes are citation markers only — never a vector-search entry
    point. The editorial annotation text for each cited case is already
    embedded in the citing statute's own PDF text (Wisconsin Statutes
    Annotated prints annotations directly under the statute section), so
    there's no benefit to duplicating it here and every reason NOT to give
    case-law docs their own embeddings: it would let the agent "answer from
    the case" without ever consulting the contextualizing statute.

    The returned record has:
      - identity fields (id, title, citation, source_url, doc_type, authority_level)
      - empty `chunks` — no embeddings will be produced, no chunk nodes loaded
      - empty `summary` — the annotation text lives on the statute's chunks
      - `citing_statute_refs` — used by load.py to wire Statute -[:CITES]-> CaseLaw edges

    The full opinion text (in S3 `.txt` files) is still reachable by agents
    via the `fetch_case_opinion` tool; it's just not part of the graph index.
    """
    doc_id = doc["doc_id"]
    key = doc["key"]

    citing_statutes = _parse_citing_statutes(metadata)
    citation = metadata.get("citation", "")

    # Title preference: metadata case_name → citation → doc_id. We no longer
    # run annotation extraction here — that moved to the statute-side derivation
    # path, since the annotation text belongs ON the statute, not on the case.
    meta_case_name = metadata.get("case_name", "").strip()
    if meta_case_name and citation:
        title = f"{meta_case_name}, {citation}"
    elif meta_case_name:
        title = meta_case_name
    elif citation:
        title = citation
    else:
        title = doc_id

    # Statute refs drive the Document-level CITES edge targets in load.py.
    # We emit both chapter- and section-level refs (e.g., "70" AND "70.32")
    # so the agent can find this case from either granularity. Section is
    # derived from the page header in the local statute PDF; falls back to
    # chapter-only when the mirror is unavailable.
    state_laws_dir = Path(
        config.get("state_laws_dir") or DEFAULT_STATE_LAWS_DIR
    )
    statute_refs = _derive_case_statute_refs(citing_statutes, state_laws_dir)

    result = {
        "doc_id": doc_id,
        "s3_key": key,
        "doc_type": "case_law",
        "framework_id": metadata.get("framework_id", "FW-CASE-LAW"),
        "authority_level": resolve_authority_level(
            metadata, metadata.get("framework_id", "FW-CASE-LAW"), config
        ),
        "title": title,
        "summary": "",  # intentionally empty — annotation lives on the citing statute
        "citation": citation,
        "statute_refs": statute_refs,
        "admin_rule_refs": [],
        "implements_refs": [],
        "topics": [],  # no topic edges either; case-law is not a primary entry point
        "source_url": metadata.get("source_url", ""),
        "full_text": title,  # used only for doc-level embedding; stub nodes don't get one
        "chunks": [],  # the key Path A change: no chunks, no embeddings
    }

    logger.info(
        f"  Extracted {doc_id}: thin case-law stub, title={title[:60]}, "
        f"cites {len(statute_refs)} statute refs ({statute_refs[:5]})"
    )
    return result


_STATUTE_FILE_CHAPTER_RE = re.compile(r"^(\d+)(?:\s+Document)?\.pdf$", re.IGNORECASE)
_STATUTE_FILE_DOCUMENT_RE = re.compile(r"^Document\s+(\d+)\.pdf$", re.IGNORECASE)


def _statute_file_to_chapter(filename: str) -> str:
    """Extract the statute chapter number from a citing-statute PDF filename.

    Wisconsin statute PDFs are named inconsistently: "70.pdf", "706 Document.pdf",
    "Document 76.pdf". This normalizes all three to just the chapter number.
    Returns empty string when the name doesn't match — callers skip those.
    """
    m = _STATUTE_FILE_CHAPTER_RE.match(filename.strip())
    if m:
        return m.group(1)
    m = _STATUTE_FILE_DOCUMENT_RE.match(filename.strip())
    if m:
        return m.group(1)
    return ""


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
        # Case-law documents follow a specialized path: annotations from the
        # citing Wisconsin statute PDFs replace the LLM classifier entirely,
        # with LLM-summary fallback for cases where annotation extraction
        # yields thin content.
        if metadata.get("doc_type") == "case_law" or doc_id.startswith("case-law-"):
            result = process_case_law_document(doc, raw_bucket, metadata, config)
            if result is None:
                return None
            cache_key = f"extracted/{doc_id}.json"
            cache_data = {k: v for k, v in result.items() if k != "full_text"}
            s3.put_object(
                Bucket=work_bucket,
                Key=cache_key,
                Body=json.dumps(cache_data, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            return result

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
            "authority_level": resolve_authority_level(
                metadata, metadata.get("framework_id", "FW-GOV-PUBS"), config
            ),
            "title": metadata.get("title", classification.get("title", doc_id)),
            "summary": classification.get("summary", ""),
            "statute_refs": classification.get("statute_refs", []),
            "admin_rule_refs": classification.get("admin_rule_refs", []),
            "implements_refs": classification.get("implements_refs", []),
            "topics": classification.get("topics", []),
            "source_url": metadata.get("source_url", "n/a"),
            "effective_date": metadata.get("effective_date", ""),
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


def list_already_extracted(bucket: str) -> set[str]:
    """Return set of doc_ids that already have extraction output in the work bucket."""
    extracted = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="extracted/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != "extracted/manifest.json":
                doc_id = key.removeprefix("extracted/").removesuffix(".json")
                extracted.add(doc_id)
    return extracted


def main():
    parser = argparse.ArgumentParser(description="Extract and classify documents for GraphRAG")
    parser.add_argument("--raw-bucket", required=True, help="S3 bucket with raw documents")
    parser.add_argument("--work-bucket", required=True, help="S3 bucket for intermediate cache")
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="Re-extract all documents, ignoring cache")
    parser.add_argument(
        "--source-filter",
        default="",
        help="Only process doc_ids matching this prefix (e.g., 'wpam-' to re-extract WPAM only).",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    docs = list_documents(args.raw_bucket, "raw/")
    logger.info(f"Found {len(docs)} documents in raw bucket")

    if args.source_filter:
        before = len(docs)
        docs = [d for d in docs if d["doc_id"].startswith(args.source_filter)]
        logger.info(
            f"Source filter '{args.source_filter}': {before} → {len(docs)} documents"
        )

    if not args.force:
        already_done = list_already_extracted(args.work_bucket)
        before = len(docs)
        docs = [d for d in docs if d["doc_id"] not in already_done]
        logger.info(f"Skipping {before - len(docs)} already-extracted documents, {len(docs)} remaining")

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
