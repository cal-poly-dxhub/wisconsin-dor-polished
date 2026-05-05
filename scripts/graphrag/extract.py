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
from scripts.graphrag.case_annotations import gather_case_annotations

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


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


# Case-law annotations live in the Wisconsin Statutes PDFs under docs/state-laws/.
# The path is relative to the repo root.
CASE_LAW_STATUTE_PDF_DIR = os.path.join(
    os.path.dirname(__file__), "../..", "docs", "state-laws"
)

# Max characters per opinion chunk when splitting a full court opinion .txt.
OPINION_CHUNK_SIZE = 2000
OPINION_CHUNK_OVERLAP = 200

# Opinion chunks beyond this count are dropped — opinions running 50k+ chars
# generate unusably many chunks. The agent can always fetch the full text via
# fetch_case_opinion for deep reads.
MAX_OPINION_CHUNKS = 20

# Cases with less than this much total annotation text get an LLM-generated
# summary fallback. Tuned from observed data: "Affirmed. 2011 WI 4," style
# stubs are ~20-40 chars; real one-sentence annotations are ~130+ chars.
MIN_ANNOTATION_TOTAL_CHARS = 100

# LLM-summary fallback context window: how much surrounding statute text to
# pass in. Broader than the annotation boundary so the LLM can infer the topic
# of the citing statute section.
LLM_FALLBACK_CONTEXT_CHARS = 3000


CASE_LAW_LLM_PROMPT = """You are summarizing a Wisconsin court case for a property-tax research assistant.

You have LIMITED information about the case:
- Citation: {citation}
- Cited in Wisconsin Statutes: {statute_list}
{opinion_section}
- Surrounding statute text where the case is cited:
{statute_context}

Write a 2-3 sentence summary describing:
1. What the case is (court level inferrable from citation format: "WI App" = Court of Appeals, "WI" = Supreme Court, federal reporters = federal courts)
2. The legal topic or statutory provision it relates to (based on the citing statute context)
3. Any holding you can confidently infer from the opinion text or surrounding context

DO NOT speculate about facts or holdings that aren't supported by the text above.
If you can only identify the topic area without a specific holding, say so honestly.

Return ONLY the summary text, no prefix, no markdown, no JSON."""


def _llm_summarize_case(
    citation: str,
    statute_list: list[str],
    opinion_text: str,
    statute_context: str,
    model_id: str,
) -> str | None:
    """Generate an LLM summary when annotation extraction yields thin content.

    Returns None on LLM failure — caller should fall back to a minimal
    descriptor rather than crashing the whole extract.
    """
    opinion_section = ""
    if opinion_text:
        # Truncate to keep tokens bounded; opinions can be 50k+ chars.
        opinion_section = f"- Opinion text excerpt (first 4000 chars):\n{opinion_text[:4000]}\n"

    prompt = CASE_LAW_LLM_PROMPT.format(
        citation=citation,
        statute_list=", ".join(statute_list) if statute_list else "(unknown)",
        opinion_section=opinion_section,
        statute_context=statute_context or "(no surrounding statute context available)",
    )

    try:
        response = bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.0},
        )
        return response["output"]["message"]["content"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"  LLM fallback summary failed for {citation}: {e}")
        return None


def _load_statute_context(citing_statutes: list[dict], max_chars: int) -> str:
    """Return broader surrounding text from citing statute PDFs.

    Unlike annotation extraction (which targets a single editorial paragraph),
    this grabs ~max_chars of raw text around each cited page — gives the LLM
    enough context to recognize the legal topic.
    """
    import fitz

    parts: list[str] = []
    per_source_budget = max(max_chars // max(len(citing_statutes), 1), 500)

    for src in citing_statutes:
        pdf_path = os.path.join(CASE_LAW_STATUTE_PDF_DIR, src["file"])
        if not os.path.exists(pdf_path):
            continue
        pages = src.get("pages", [])
        if not pages:
            continue
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            continue
        try:
            for page_1idx in pages[:1]:  # one page per source is enough
                page_idx = page_1idx - 1
                if 0 <= page_idx < len(doc):
                    text = doc[page_idx].get_text()
                    text = re.sub(r"\s+", " ", text).strip()
                    parts.append(f"[{src['file']} p{page_1idx}]: {text[:per_source_budget]}")
        finally:
            doc.close()

    combined = "\n\n".join(parts)
    return combined[:max_chars]


def _summary_from_annotations(annotations: list[dict], max_chars: int = 600) -> str:
    """Build a Document-level summary from annotation texts.

    Uses the longest annotation (most informative) as the primary summary. If
    it's shorter than max_chars, appends additional annotations (separated by
    " — ") until the cap. Each annotation is already self-contained per the
    Wisconsin Statutes format.
    """
    if not annotations:
        return ""
    sorted_anns = sorted(annotations, key=lambda a: -len(a["text"]))
    summary = sorted_anns[0]["text"]
    for ann in sorted_anns[1:]:
        if len(summary) >= max_chars:
            break
        if ann["text"] not in summary:
            summary = summary + " — " + ann["text"]
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def _chunk_opinion_text(text: str, doc_id: str, s3_key: str) -> list[dict]:
    """Split a full court-opinion .txt into overlapping windowed chunks."""
    chunks: list[dict] = []
    stride = OPINION_CHUNK_SIZE - OPINION_CHUNK_OVERLAP
    for i in range(0, len(text), stride):
        chunk_text = text[i : i + OPINION_CHUNK_SIZE]
        if len(chunk_text.strip()) < 50:
            continue
        chunks.append({
            "text": chunk_text,
            "metadata": {
                "doc_id": doc_id,
                "source": s3_key,
                "chunk_index": len(chunks) + 1000,  # offset so annotation chunks sort first
                "start_page": None,
                "end_page": None,
                "chunk_kind": "opinion",
            },
        })
        if len(chunks) >= MAX_OPINION_CHUNKS:
            break
    return chunks


def _select_title(
    annotations: list[dict], citation_metadata: dict, doc_id: str
) -> str:
    """Pick the best title for a case-law document.

    Priority: extracted case_name from any annotation > metadata case_name > citation > doc_id.
    """
    for ann in annotations:
        if ann.get("case_name"):
            return f"{ann['case_name']}, {citation_metadata.get('citation', '')}".strip(", ")
    if citation_metadata.get("case_name"):
        return f"{citation_metadata['case_name']}, {citation_metadata.get('citation', '')}".strip(", ")
    return citation_metadata.get("citation") or doc_id


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


def process_case_law_document(
    doc: dict, raw_bucket: str, metadata: dict, config: dict
) -> dict | None:
    """Build an extraction result for a case-law document from annotations + opinion.

    For case-law docs, we bypass the generic LLM classifier entirely. Wisconsin
    Statutes publishes per-statute annotations for each cited case — short
    editorial paragraphs describing the holding in the context of the citing
    statute. These are better grounding than anything Claude could synthesize
    from a metadata stub.

    When annotation extraction produces thin content (<MIN_ANNOTATION_TOTAL_CHARS),
    we fall back to an LLM-generated summary using the opinion text (if present)
    plus broader statute context as grounding. The resulting chunk is tagged
    `chunk_kind="llm_summary"` so downstream code can distinguish it from
    authoritative editorial annotations.

    Ordering of chunks: annotations → llm_summary (if any) → opinion chunks.
    """
    doc_id = doc["doc_id"]
    key = doc["key"]

    citing_statutes = _parse_citing_statutes(metadata)
    citation = metadata.get("citation", "")

    annotations = gather_case_annotations(
        citation, citing_statutes, CASE_LAW_STATUTE_PDF_DIR
    ) if citation else []

    # Read opinion text if available (case-law .txt files are court opinions).
    opinion_text = ""
    if key.endswith(".txt"):
        try:
            opinion_text = extract_text_from_s3(raw_bucket, key)
        except Exception as e:
            logger.warning(f"  {doc_id}: failed to read opinion txt: {e}")
            opinion_text = ""

    # Build annotation chunks — one per citing-statute annotation.
    chunks: list[dict] = []
    for i, ann in enumerate(annotations):
        chunks.append({
            "text": ann["text"],
            "metadata": {
                "doc_id": doc_id,
                "source": f"raw/{doc_id}/{doc_id}",  # logical source, not on disk
                "source_url": metadata.get("source_url", ""),
                "chunk_index": i,
                "start_page": ann["pages"][0] if ann["pages"] else None,
                "end_page": ann["pages"][-1] if ann["pages"] else None,
                "annotated_in": ann["source_file"],
                "chunk_kind": "annotation",
            },
        })

    # Fallback: when annotations are too thin to be useful, generate an LLM
    # summary from whatever grounded content we have.
    total_annotation_chars = sum(len(a["text"]) for a in annotations)
    llm_summary_text: str | None = None
    used_llm_fallback = False

    if total_annotation_chars < MIN_ANNOTATION_TOTAL_CHARS:
        used_llm_fallback = True
        llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-6")
        statute_list = sorted({src["file"] for src in citing_statutes})
        statute_context = _load_statute_context(citing_statutes, LLM_FALLBACK_CONTEXT_CHARS)

        llm_summary_text = _llm_summarize_case(
            citation=citation,
            statute_list=statute_list,
            opinion_text=opinion_text,
            statute_context=statute_context,
            model_id=llm_model,
        )

        if llm_summary_text:
            chunks.append({
                "text": llm_summary_text,
                "metadata": {
                    "doc_id": doc_id,
                    "source": f"raw/{doc_id}/{doc_id}",
                    "source_url": metadata.get("source_url", ""),
                    "chunk_index": 500,  # between annotations (0..N) and opinion (1000..N)
                    "start_page": None,
                    "end_page": None,
                    "chunk_kind": "llm_summary",
                    "grounded_on": "opinion+statutes" if opinion_text else "statutes",
                },
            })

    # Append opinion chunks AFTER annotation + llm_summary chunks.
    if opinion_text:
        chunks.extend(_chunk_opinion_text(opinion_text, doc_id, key))

    if not chunks:
        # Truly nothing: no annotations, LLM failed, no opinion.
        stub_text = f"Wisconsin case law citation: {citation}. See source link for filings."
        chunks.append({
            "text": stub_text,
            "metadata": {
                "doc_id": doc_id,
                "source": key,
                "source_url": metadata.get("source_url", ""),
                "chunk_index": 0,
                "start_page": None,
                "end_page": None,
                "chunk_kind": "placeholder",
            },
        })

    # Derive statute_refs from citing_statutes metadata (files are named by chapter number).
    statute_refs = sorted({
        src["file"].replace(" Document", "").replace(".pdf", "").strip()
        for src in citing_statutes
        if src.get("file", "").replace(" Document.pdf", "").replace(".pdf", "").strip().isdigit()
    })

    title = _select_title(annotations, metadata, doc_id)

    # Summary priority: substantive annotations > LLM fallback > thin annotations > minimal descriptor.
    # When annotations exist but total content is below the fallback threshold, the LLM summary
    # is the better document-level signal — the stub annotation becomes supporting context.
    annotation_summary = _summary_from_annotations(annotations) if annotations else ""
    if annotation_summary and total_annotation_chars >= MIN_ANNOTATION_TOTAL_CHARS:
        summary = annotation_summary
    elif llm_summary_text:
        summary = llm_summary_text
    elif annotation_summary:
        summary = annotation_summary
    else:
        summary = f"Wisconsin case law citation {citation}."

    # Attach chunk-level citation refs (uses existing extract_chunk_citations helper).
    for chunk in chunks:
        refs = extract_chunk_citations(chunk["text"])
        chunk["metadata"]["statute_refs"] = refs["statute_refs"]
        chunk["metadata"]["admin_rule_refs"] = refs["admin_rule_refs"]

    result = {
        "doc_id": doc_id,
        "s3_key": key,
        "doc_type": "case_law",
        "framework_id": metadata.get("framework_id", "FW-CASE-LAW"),
        "authority_level": int(metadata.get("authority_level", 3)),
        "title": title,
        "summary": summary,
        "statute_refs": statute_refs,
        "admin_rule_refs": [],
        "implements_refs": [],
        "topics": ["case law"],
        "source_url": metadata.get("source_url", ""),
        "full_text": summary + "\n\n" + "\n\n".join(c["text"] for c in chunks),
        "chunks": chunks,
    }

    opinion_chunk_count = sum(1 for c in chunks if c["metadata"].get("chunk_kind") == "opinion")
    fallback_marker = " [llm-fallback]" if used_llm_fallback else ""
    logger.info(
        f"  Extracted {doc_id}: {len(annotations)} annotations, "
        f"{opinion_chunk_count} opinion chunks{fallback_marker}, "
        f"title={title[:60]}"
    )
    return result


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
    args = parser.parse_args()

    config = load_config(args.config)

    docs = list_documents(args.raw_bucket, "raw/")
    logger.info(f"Found {len(docs)} documents in raw bucket")

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
