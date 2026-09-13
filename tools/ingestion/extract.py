"""
Phase 2: Document Extraction + LLM Classification

Pulls files from S3 raw bucket, routes each to appropriate parser,
classifies via LLM, normalizes IDs, deduplicates.

Usage:
    python -m tools.ingestion.extract \
        --raw-bucket <raw-bucket> \
        --work-bucket <work-bucket> \
        --config tools/ingestion/config/ingest_config.yaml
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import boto3
import yaml

# Ensure the repo root is importable when this module is run directly
# (python tools/ingestion/extract.py). Running via `-m` already has it on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tools.ingestion.chunking.case_law import select_and_chunk
from tools.ingestion.chunking.pdfChunker import process_pdf_from_s3
from tools.ingestion.ingest_case_law import _case_name_from_url
from tools.ingestion.lib import aliases as alias_lib
from tools.ingestion.lib.case_annotations import extract_section_for_page

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


def resolve_authority_level(metadata: dict, framework_id: str, config: dict) -> int | None:
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

    framework_levels = {fw["id"]: fw["authority_level"] for fw in config.get("frameworks", [])}
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


def _derive_case_statute_refs(citing_statutes: list[dict], state_laws_dir: Path) -> list[str]:
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
                section = extract_section_for_page(pdf_path, int(page), expected_chapter=chapter)
            except (ValueError, TypeError):
                continue
            if section:
                refs.add(section)
    return sorted(refs)


_CASE_LAW_SUMMARY_MODEL = os.environ.get(
    "CASE_LAW_SUMMARY_MODEL", "us.amazon.nova-2-lite-v1:0"
)

_CASE_LAW_SUMMARY_PROMPT = (
    "Read the following Wisconsin court opinion and write a 2-3 sentence summary "
    "of ALL major holdings. State clearly: (1) who won, (2) which Wisconsin "
    "statute sections were at issue, and (3) what the court decided about them. "
    "If the case involves multiple issues, summarize the most important ones. "
    "Do not include a heading or labels — just write the sentences directly.\n\n"
    "Opinion:\n{text}\n\n"
    "Holding summary (2-3 sentences):"
)

_CASE_STATUTE_RE = re.compile(
    r"(?:(?:Wis\.?\s*Stat\.?|ss?\.|sec\.?|§§?)\s*(\d{1,3}\.\d{2,4}[a-z]?))"
    r"|(?:(\d{2,3}\.\d{2,4}[a-z]?)\s*(?:\(\d+\))*\s*(?:,\s*(?:Wis\.?\s*Stat|Stats)))",
    re.IGNORECASE,
)


def _fetch_opinion_text(raw_bucket: str, doc_id: str, s3_key: str) -> str | None:
    """Fetch the opinion .txt from S3. Tries s3_key path, then reporter-path fallback."""
    slug = doc_id.replace("case-law-", "")
    # Try direct key (if stored on the node)
    if s3_key and ".txt" in s3_key:
        try:
            obj = s3.get_object(Bucket=raw_bucket, Key=s3_key)
            return obj["Body"].read().decode("utf-8", errors="replace")
        except Exception:
            pass
    # Reporter-path fallback
    for reporter in ["wis-2d", "wi", "wi-app", "n-w-2d", "n-w-3d", "f-2d", "f-3d",
                     "f-supp-2d", "f-supp-3d", "f-4th", "s-ct", "u-s", "l-ed-2d"]:
        try:
            key = f"raw/case-law/{reporter}/{slug}.txt"
            obj = s3.get_object(Bucket=raw_bucket, Key=key)
            return obj["Body"].read().decode("utf-8", errors="replace")
        except Exception:
            continue
    return None


def _summarize_opinion(text: str) -> str:
    """Generate a 2-3 sentence holding summary via Nova 2 Lite.

    Strips court-document header boilerplate (everything before ¶1) and sends
    the full opinion text — Nova 2 Lite supports 256K context, and even the
    longest opinions (~75K tokens) fit comfortably.
    """
    # Strip header: everything before the first paragraph marker
    m = re.search(r"¶\s*1", text)
    if m:
        text = text[m.start():]
    prompt = _CASE_LAW_SUMMARY_PROMPT.format(text=text)
    try:
        resp = bedrock.converse(
            modelId=_CASE_LAW_SUMMARY_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.0},
        )
        return " ".join(
            b.get("text", "").strip()
            for b in resp["output"]["message"]["content"]
            if b.get("text")
        ).strip()
    except Exception as exc:
        logger.warning(f"Case-law summary failed: {exc}")
        return ""


def _extract_statute_refs_from_text(text: str) -> list[str]:
    """Regex-extract section-level statute refs from opinion text."""
    matches = _CASE_STATUTE_RE.findall(text)
    return sorted({m[0] or m[1] for m in matches if m[0] or m[1]})


def process_case_law_document(
    doc: dict, raw_bucket: str, metadata: dict, config: dict
) -> dict | None:
    """Extract summary plus selective majority-analysis chunks from case law.

    Chunk 0 remains the LLM holding summary. Body chunks retain the majority's
    opening issue/holding synopsis and legal analysis through disposition while
    omitting captions, detailed factual narrative, notes, and separate opinions.
    Each chunk carries only the statute/admin-rule references found in its own
    text, enabling direct ``Chunk-[:CITES]->Statute`` retrieval.
    """
    doc_id = doc["doc_id"]
    key = doc["key"]

    citing_statutes = _parse_citing_statutes(metadata)
    citation = metadata.get("citation", "")

    meta_case_name = metadata.get("case_name", "").strip()
    # Fall back to the case name embedded in the CourtListener URL slug when
    # metadata has no resolved case_name. Without this, a title degrades to a
    # bare citation (e.g. "405 Wis. 2d 616"), which renders a nameless card.
    if not meta_case_name:
        url_name = _case_name_from_url(metadata.get("source_url", "") or "")
        if url_name and url_name != citation:
            meta_case_name = url_name
    if meta_case_name and citation and meta_case_name != citation:
        title = f"{meta_case_name}, {citation}"
    elif meta_case_name and not citation:
        title = meta_case_name
    elif citation:
        title = citation
    else:
        title = doc_id

    opinion_text = _fetch_opinion_text(raw_bucket, doc_id, key)
    summary = ""
    text_statute_refs: list[str] = []
    selection = None

    if opinion_text:
        summary = _summarize_opinion(opinion_text)
        text_statute_refs = _extract_statute_refs_from_text(opinion_text)
        selection = select_and_chunk(opinion_text)

    state_laws_dir = Path(config.get("state_laws_dir") or DEFAULT_STATE_LAWS_DIR)
    legacy_refs = _derive_case_statute_refs(citing_statutes, state_laws_dir)
    statute_refs = sorted(set(text_statute_refs) | set(legacy_refs))

    source_url = metadata.get("source_url", "")
    chunks: list[dict] = []

    def append_chunk(text: str, heading: str, content_role: str) -> None:
        extracted = extract_chunk_citations(text)
        local_statute_refs = sorted(
            set(extracted["statute_refs"]) | set(_extract_statute_refs_from_text(text))
        )
        chunks.append(
            {
                "text": text,
                "metadata": {
                    "doc_id": doc_id,
                    "source": key,
                    "source_url": source_url,
                    "heading": heading,
                    "content_role": content_role,
                    "start_page": None,
                    "end_page": None,
                    "statute_refs": local_statute_refs,
                    "admin_rule_refs": extracted["admin_rule_refs"],
                },
            }
        )

    if summary:
        append_chunk(summary, "Holding summary", "summary_holding")

    if selection:
        for selected in selection.chunks:
            append_chunk(selected.text, selected.heading, selected.role)

    selected_text = "\n\n".join(chunk["text"] for chunk in chunks)
    result = {
        "doc_id": doc_id,
        "s3_key": key,
        "doc_type": "case_law",
        "framework_id": metadata.get("framework_id", "FW-CASE-LAW"),
        "authority_level": resolve_authority_level(
            metadata, metadata.get("framework_id", "FW-CASE-LAW"), config
        ),
        "title": title,
        "summary": summary,
        "citation": citation,
        "statute_refs": statute_refs,
        "admin_rule_refs": [],
        "implements_refs": [],
        "topics": [],
        "source_url": source_url,
        "full_text": f"{title}\n{selected_text}" if selected_text else title,
        "chunks": chunks,
        "case_law_selection_fallback": bool(selection and selection.fallback_used),
        "case_law_selection_confidence": selection.confidence if selection else None,
    }

    if selection:
        selection_status = (
            f"selective={len(selection.chunks)} body chunks, "
            f"retained={selection.retained_ratio:.0%}, fallback={selection.fallback_used}"
        )
    else:
        selection_status = "stub (no opinion text)"
    logger.info(
        f"  Extracted {doc_id}: {len(chunks)} total chunks, {selection_status}, "
        f"title={title[:50]}, cites {len(statute_refs)} statute refs ({statute_refs[:5]})"
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


def _metadata_key_for(key: str) -> str:
    """Derive the metadata sidecar path for a content key.

    Convention: {stem}.metadata.json (sibling of the content file).
    e.g. raw/case-law/wis-2d/100-wis-2d-256.txt → ...100-wis-2d-256.metadata.json
         raw/wpam-ch7/wpam-ch7.pdf              → ...wpam-ch7.pdf.metadata.json
    """
    if "/case-law/" in key:
        return key.rsplit(".", 1)[0] + ".metadata.json"
    return key + ".metadata.json"


def get_metadata(bucket: str, key: str) -> dict:
    """Fetch metadata.json for a document."""
    meta_key = _metadata_key_for(key)
    try:
        obj = s3.get_object(Bucket=bucket, Key=meta_key)
        data = json.loads(obj["Body"].read())
        return data.get("metadataAttributes", {})
    except Exception:
        return {}


def list_documents(bucket: str, prefix: str) -> list[dict]:
    """List all documents in the raw bucket, grouped by doc_id.

    Layout:
      - Folder-per-doc:  raw/{doc_id}/{doc_id}.ext  (statutes, wpam, etc.)
      - Case law:        raw/case-law/{reporter}/{slug}.ext  → doc_id = "case-law-{slug}"
    """
    docs = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json"):
                continue
            parts = key.replace(prefix, "").split("/")
            if len(parts) == 3 and parts[0] == "case-law":
                slug = parts[2].rsplit(".", 1)[0]
                doc_id = f"case-law-{slug}"
            elif len(parts) >= 2:
                doc_id = parts[0]
            else:
                continue
            if doc_id not in docs:
                docs[doc_id] = {"doc_id": doc_id, "key": key, "size": obj["Size"]}
    return list(docs.values())


# ---------------------------------------------------------------------------
# Work-bucket key helpers (cache prefix support)
#
# Every key under the work bucket is built through these so a STAGING run with
# --cache-prefix staging/ reads/writes s3://work-bucket/staging/{classified,
# extracted,embedded,aliases}/... and never touches the production caches.
# ---------------------------------------------------------------------------


def classified_key(doc_id: str, cache_prefix: str = "") -> str:
    return f"{cache_prefix}classified/{doc_id}.json"


def extracted_key(doc_id: str, cache_prefix: str = "") -> str:
    return f"{cache_prefix}extracted/{doc_id}.json"


def aliases_key(doc_id: str, cache_prefix: str = "") -> str:
    return f"{cache_prefix}aliases/{doc_id}.json"


def manifest_key(cache_prefix: str = "") -> str:
    return f"{cache_prefix}extracted/manifest.json"


def _get_json(bucket: str, key: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except Exception:
        return None


def _put_json(bucket: str, key: str, data, *, indent: int | None = None) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=indent, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def _load_cached_classification(
    work_bucket: str,
    doc_id: str,
    cache_prefix: str = "",
    fallback_prefix: str = "",
) -> dict | None:
    """Load a previously-cached LLM classification from S3, or None if absent.

    When running under a cache prefix, a missing ``{prefix}classified/{id}.json``
    falls back to ``{fallback_prefix}classified/{id}.json`` (the production
    cache by default) and copies it forward so LLM classification is never
    re-paid for a staging run.
    """
    data = _get_json(work_bucket, classified_key(doc_id, cache_prefix))
    if data is not None or fallback_prefix == cache_prefix:
        return data
    data = _get_json(work_bucket, classified_key(doc_id, fallback_prefix))
    if data is not None:
        logger.info(
            f"  Classification for {doc_id} copied forward from "
            f"'{fallback_prefix}classified/' to '{cache_prefix}classified/'"
        )
        _save_classification(work_bucket, doc_id, data, cache_prefix)
    return data


def _save_classification(
    work_bucket: str, doc_id: str, classification: dict, cache_prefix: str = ""
) -> None:
    """Persist LLM classification separately so re-chunking doesn't require reclassification."""
    _put_json(work_bucket, classified_key(doc_id, cache_prefix), classification)


def _save_extracted(work_bucket: str, result: dict, cache_prefix: str = "") -> None:
    cache_data = {k: v for k, v in result.items() if k != "full_text"}
    _put_json(work_bucket, extracted_key(result["doc_id"], cache_prefix), cache_data)


# ---------------------------------------------------------------------------
# Alias enrichment (document expansion)
# ---------------------------------------------------------------------------


class AliasContext:
    """Flag-gated alias generation settings + run-wide counters."""

    def __init__(
        self,
        *,
        enabled: bool,
        model_id: str = alias_lib.DEFAULT_ALIAS_MODEL,
        min_chunk_chars: int = alias_lib.MIN_CHUNK_CHARS,
        workers: int = 3,
        cache_prefix: str = "",
    ):
        self.enabled = enabled
        self.model_id = model_id
        self.min_chunk_chars = min_chunk_chars
        self.workers = max(1, workers)
        self.cache_prefix = cache_prefix
        self._lock = threading.Lock()
        self.totals = {
            "docs": 0,
            "cached": 0,
            "generated": 0,
            "failed": 0,
            "skipped_short": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    @classmethod
    def from_config(
        cls, config: dict, *, enabled: bool | None, workers: int, cache_prefix: str
    ) -> "AliasContext":
        cfg = config.get("alias_enrichment") or {}
        return cls(
            enabled=bool(cfg.get("enabled", False)) if enabled is None else enabled,
            model_id=cfg.get("alias_model", alias_lib.DEFAULT_ALIAS_MODEL),
            min_chunk_chars=int(cfg.get("min_chunk_chars", alias_lib.MIN_CHUNK_CHARS)),
            workers=int(cfg.get("workers", workers) or workers),
            cache_prefix=cache_prefix,
        )

    def add(self, stats: dict) -> None:
        with self._lock:
            self.totals["docs"] += 1
            for k in (
                "cached",
                "generated",
                "failed",
                "skipped_short",
                "input_tokens",
                "output_tokens",
            ):
                self.totals[k] += int(stats.get(k, 0) or 0)

    def log_totals(self) -> None:
        t = self.totals
        gen = t["generated"] or 1
        logger.info(
            f"Alias enrichment totals: docs={t['docs']} cached={t['cached']} "
            f"generated={t['generated']} failed={t['failed']} skipped_short={t['skipped_short']} "
            f"tokens in/out={t['input_tokens']}/{t['output_tokens']} "
            f"(avg per generated chunk {t['input_tokens'] / gen:.0f}/{t['output_tokens'] / gen:.0f})"
        )


def enrich_chunks_with_aliases(result: dict, work_bucket: str, ctx: AliasContext) -> dict:
    """Attach ``chunk["aliases"]`` to every eligible chunk of an extracted doc.

    Cache: ``{prefix}aliases/{doc_id}.json`` is a dict keyed by
    ``alias_cache_key(chunk_text)`` → {questions, aliases, model,
    prompt_version, generated_at, usage}. Keyed by CONTENT, so re-chunking a
    doc reuses aliases for unchanged text. Missing entries are generated in a
    small thread pool; failures leave the chunk without an ``aliases`` field
    (never abort the doc).

    Returns per-doc stats {cached, generated, failed, skipped_short, tokens}.
    """
    doc_id = result["doc_id"]
    chunks = result.get("chunks", [])
    stats = {
        "cached": 0,
        "generated": 0,
        "failed": 0,
        "skipped_short": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    if not chunks:
        return stats

    cache = _get_json(work_bucket, aliases_key(doc_id, ctx.cache_prefix)) or {}
    if not isinstance(cache, dict):
        cache = {}

    todo: list[tuple[int, str]] = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text") or ""
        if len(text.strip()) < ctx.min_chunk_chars:
            stats["skipped_short"] += 1
            chunk.pop("aliases", None)
            continue
        key = alias_lib.alias_cache_key(text)
        entry = cache.get(key)
        if (
            entry
            and isinstance(entry, dict)
            and entry.get("prompt_version") == alias_lib.PROMPT_VERSION
        ):
            chunk["aliases"] = {
                "questions": list(entry.get("questions") or []),
                "aliases": list(entry.get("aliases") or []),
                "cache_key": key,
            }
            stats["cached"] += 1
        else:
            todo.append((i, key))

    title = result.get("title") or doc_id
    new_entries: dict[str, dict] = {}

    def _gen(item: tuple[int, str]) -> tuple[int, str, dict]:
        i, key = item
        chunk = chunks[i]
        heading = (chunk.get("metadata") or {}).get("heading") or ""
        raw = alias_lib.generate_aliases(chunk["text"], title, heading, ctx.model_id)
        return i, key, raw

    if todo:
        with ThreadPoolExecutor(max_workers=ctx.workers) as pool:
            for i, key, raw in pool.map(_gen, todo):
                if raw.get("error"):
                    stats["failed"] += 1
                    chunks[i].pop("aliases", None)
                    continue
                filtered = alias_lib.filter_aliases(raw, chunks[i]["text"])
                usage = raw.get("usage") or {}
                stats["input_tokens"] += int(usage.get("input_tokens") or 0)
                stats["output_tokens"] += int(usage.get("output_tokens") or 0)
                entry = {
                    "questions": filtered["questions"],
                    "aliases": filtered["aliases"],
                    "model": ctx.model_id,
                    "prompt_version": alias_lib.PROMPT_VERSION,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "usage": usage,
                    "raw_counts": {
                        "questions": len(raw.get("questions") or []),
                        "aliases": len(raw.get("aliases") or []),
                    },
                }
                new_entries[key] = entry
                chunks[i]["aliases"] = {
                    "questions": entry["questions"],
                    "aliases": entry["aliases"],
                    "cache_key": key,
                }
                stats["generated"] += 1

    if new_entries:
        cache.update(new_entries)
        _put_json(work_bucket, aliases_key(doc_id, ctx.cache_prefix), cache)

    logger.info(
        f"  Aliases {doc_id}: cached={stats['cached']} generated={stats['generated']} "
        f"failed={stats['failed']} skipped_short={stats['skipped_short']} "
        f"tokens in/out={stats['input_tokens']}/{stats['output_tokens']}"
    )
    ctx.add(stats)
    return stats


def _doc_fully_enriched(doc: dict, ctx: AliasContext) -> bool:
    for chunk in doc.get("chunks", []):
        text = chunk.get("text") or ""
        if len(text.strip()) < ctx.min_chunk_chars:
            continue
        if "aliases" not in chunk:
            return False
    return True


def backfill_aliases_for_doc(
    doc_id: str, work_bucket: str, ctx: AliasContext, *, force: bool = False
) -> dict | None:
    """``--aliases-only``: enrich an EXISTING extracted JSON without re-extracting.

    Reads ``{prefix}extracted/{doc_id}.json`` (seeding from the unprefixed
    ``extracted/`` when the prefixed copy is absent), generates missing
    aliases, and rewrites the extracted file — which bumps its LastModified so
    ``embed --smart`` picks it up. Docs already fully enriched are left
    untouched unless ``force``.
    """
    prefixed = extracted_key(doc_id, ctx.cache_prefix)
    doc = _get_json(work_bucket, prefixed)
    seeded = False
    if doc is None and ctx.cache_prefix:
        doc = _get_json(work_bucket, extracted_key(doc_id, ""))
        seeded = doc is not None
    if doc is None:
        logger.warning(f"  No extracted JSON for {doc_id}; skipping")
        return None

    if not force and not seeded and _doc_fully_enriched(doc, ctx):
        logger.info(f"  {doc_id}: already enriched, skipping")
        return doc

    if seeded:
        logger.info(f"  {doc_id}: seeded from extracted/ into {ctx.cache_prefix}extracted/")
    enrich_chunks_with_aliases(doc, work_bucket, ctx)
    _put_json(work_bucket, prefixed, doc)
    return doc


def process_document(
    doc: dict,
    raw_bucket: str,
    work_bucket: str,
    config: dict,
    *,
    reclassify: bool = False,
    cache_prefix: str = "",
    classification_fallback_prefix: str = "",
    alias_ctx: AliasContext | None = None,
) -> dict | None:
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
            if alias_ctx and alias_ctx.enabled:
                enrich_chunks_with_aliases(result, work_bucket, alias_ctx)
            _save_extracted(work_bucket, result, cache_prefix)
            return result

        if key.endswith(".pdf"):
            source_url = metadata.get("source_url", "n/a")
            source_id = metadata.get("doc_id", doc_id)
            chunks = process_pdf_from_s3(
                raw_bucket,
                key,
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
                chunks.append(
                    {
                        "text": chunk_text,
                        "metadata": {
                            "doc_id": doc_id,
                            "source": key,
                            "source_url": metadata.get("source_url", "n/a"),
                            "chunk_index": len(chunks),
                            "start_page": None,
                            "end_page": None,
                        },
                    }
                )

        for chunk in chunks:
            citations = extract_chunk_citations(chunk["text"])
            chunk["metadata"]["statute_refs"] = citations["statute_refs"]
            chunk["metadata"]["admin_rule_refs"] = citations["admin_rule_refs"]

        # Classification: reuse cached result unless --reclassify is set.
        classification = None
        if not reclassify:
            classification = _load_cached_classification(
                work_bucket, doc_id, cache_prefix, classification_fallback_prefix
            )
            if classification:
                logger.info(f"  Reusing cached classification for {doc_id}")

        if classification is None:
            llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-20250514")
            classification = classify_document(full_text, llm_model)
            _save_classification(work_bucket, doc_id, classification, cache_prefix)

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

        if alias_ctx and alias_ctx.enabled:
            enrich_chunks_with_aliases(result, work_bucket, alias_ctx)
        _save_extracted(work_bucket, result, cache_prefix)

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


def list_already_extracted(bucket: str, cache_prefix: str = "") -> set[str]:
    """Return set of doc_ids that already have extraction output in the work bucket."""
    extracted = set()
    prefix = f"{cache_prefix}extracted/"
    manifest = manifest_key(cache_prefix)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != manifest:
                doc_id = key.removeprefix(prefix).removesuffix(".json")
                extracted.add(doc_id)
    return extracted


def run_aliases_only(args, config: dict) -> None:
    """Backfill aliases onto existing extracted JSONs (no re-extraction, no raw bucket)."""
    ctx = AliasContext.from_config(
        config, enabled=True, workers=args.max_workers, cache_prefix=args.cache_prefix
    )
    doc_ids = list_already_extracted(args.work_bucket, args.cache_prefix)
    if args.cache_prefix:
        # Seed set: anything in production extracted/ that isn't under the prefix yet.
        doc_ids |= list_already_extracted(args.work_bucket, "")
    doc_ids_sorted = sorted(doc_ids)
    if args.source_filter:
        before = len(doc_ids_sorted)
        doc_ids_sorted = [d for d in doc_ids_sorted if d.startswith(args.source_filter)]
        logger.info(
            f"Source filter '{args.source_filter}': {before} → {len(doc_ids_sorted)} documents"
        )
    logger.info(
        f"Aliases-only backfill: {len(doc_ids_sorted)} documents under "
        f"s3://{args.work_bucket}/{args.cache_prefix}extracted/ (model={ctx.model_id})"
    )

    done = 0
    # Outer pool is 1 wide: the per-doc alias pool already fans out to
    # ctx.workers Bedrock calls; stacking pools would multiply throttling.
    for doc_id in doc_ids_sorted:
        try:
            backfill_aliases_for_doc(doc_id, args.work_bucket, ctx, force=args.force)
            done += 1
        except Exception as exc:  # noqa: BLE001 — keep going, one doc at a time
            logger.error(f"  FAILED alias backfill {doc_id}: {exc}", exc_info=True)
    logger.info(f"Aliases-only backfill complete: {done}/{len(doc_ids_sorted)} documents processed")
    ctx.log_totals()


def main():
    parser = argparse.ArgumentParser(description="Extract and classify documents for GraphRAG")
    parser.add_argument(
        "--raw-bucket",
        default="",
        help="S3 bucket with raw documents (not needed for --aliases-only)",
    )
    parser.add_argument("--work-bucket", required=True, help="S3 bucket for intermediate cache")
    parser.add_argument("--config", default="tools/ingestion/config/ingest_config.yaml")
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument(
        "--cache-prefix",
        default="",
        help="Prefix for every work-bucket key (classified/, extracted/, aliases/, manifest). "
        "e.g. 'staging/' writes s3://work-bucket/staging/extracted/... and leaves production untouched.",
    )
    parser.add_argument(
        "--classification-fallback-prefix",
        default="",
        help="When --cache-prefix is set and {prefix}classified/{id}.json is missing, reuse "
        "{fallback}classified/{id}.json and copy it forward (default: production, i.e. no prefix). "
        "Set equal to --cache-prefix to disable the fallback.",
    )
    parser.add_argument(
        "--aliases",
        action="store_true",
        help="Generate plain-language questions/synonyms per chunk (document expansion) and "
        "store them as chunk['aliases']. Also enabled by alias_enrichment.enabled in the config.",
    )
    parser.add_argument(
        "--aliases-only",
        action="store_true",
        help="Skip extraction; backfill aliases onto EXISTING extracted JSONs and rewrite them "
        "(bumps LastModified so `embed --smart` re-embeds). With --cache-prefix, seeds from "
        "the unprefixed extracted/ when the prefixed copy is absent.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-extract all documents, ignoring cache"
    )
    parser.add_argument(
        "--smart",
        action="store_true",
        help="Only re-extract documents whose raw file is newer than their extraction cache.",
    )
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="Force LLM reclassification even if a cached classification exists. "
        "Without this flag, only chunking + citation extraction re-runs on --force.",
    )
    parser.add_argument(
        "--source-filter",
        default="",
        help="Only process doc_ids matching this prefix (e.g., 'wpam-' to re-extract WPAM only).",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.aliases_only:
        run_aliases_only(args, config)
        return

    if not args.raw_bucket:
        parser.error("--raw-bucket is required unless --aliases-only is set")

    alias_ctx = AliasContext.from_config(
        config,
        enabled=True if args.aliases else None,
        workers=args.max_workers,
        cache_prefix=args.cache_prefix,
    )
    if alias_ctx.enabled:
        logger.info(
            f"Alias enrichment ENABLED (model={alias_ctx.model_id}, workers={alias_ctx.workers})"
        )
    if args.cache_prefix:
        logger.info(f"Cache prefix: '{args.cache_prefix}' (work-bucket keys are namespaced)")

    docs = list_documents(args.raw_bucket, "raw/")
    logger.info(f"Found {len(docs)} documents in raw bucket")

    if args.source_filter:
        before = len(docs)
        docs = [d for d in docs if d["doc_id"].startswith(args.source_filter)]
        logger.info(f"Source filter '{args.source_filter}': {before} → {len(docs)} documents")

    if args.force:
        pass  # Re-extract everything
    elif args.smart:
        # Only re-extract docs whose raw file is newer than extraction cache
        from botocore.exceptions import ClientError

        stale = []
        for doc in docs:
            doc_id = doc["doc_id"]
            ext_key = extracted_key(doc_id, args.cache_prefix)
            try:
                raw_head = s3.head_object(Bucket=args.raw_bucket, Key=doc["key"])
                ext_head = s3.head_object(Bucket=args.work_bucket, Key=ext_key)
                if raw_head["LastModified"] > ext_head["LastModified"]:
                    stale.append(doc)
            except ClientError:
                stale.append(doc)  # No extraction cache yet — needs processing
        logger.info(f"Smart mode: {len(stale)}/{len(docs)} documents have stale extractions")
        docs = stale
    else:
        already_done = list_already_extracted(args.work_bucket, args.cache_prefix)
        before = len(docs)
        docs = [d for d in docs if d["doc_id"] not in already_done]
        logger.info(
            f"Skipping {before - len(docs)} already-extracted documents, {len(docs)} remaining"
        )

    results = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                process_document,
                doc,
                args.raw_bucket,
                args.work_bucket,
                config,
                reclassify=args.reclassify,
                cache_prefix=args.cache_prefix,
                classification_fallback_prefix=args.classification_fallback_prefix,
                alias_ctx=alias_ctx,
            ): doc
            for doc in docs
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    results = deduplicate(results)
    logger.info(f"Extraction complete: {len(results)} documents after dedup")
    if alias_ctx.enabled:
        alias_ctx.log_totals()

    manifest = [
        {k: v for k, v in doc.items() if k not in ("full_text", "chunks")} for doc in results
    ]
    mkey = manifest_key(args.cache_prefix)
    _put_json(args.work_bucket, mkey, manifest, indent=2)
    logger.info(f"Manifest saved to s3://{args.work_bucket}/{mkey}")


if __name__ == "__main__":
    main()
