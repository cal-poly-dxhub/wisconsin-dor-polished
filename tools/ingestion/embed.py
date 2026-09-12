"""
Phase 4: Embed chunks and documents using Amazon Titan Embed v2.

Usage:
    python -m tools.ingestion.embed \
        --work-bucket <work-bucket> \
        --config tools/ingestion/config/ingest_config.yaml
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tools.ingestion.lib.aliases import compose_embed_input

EMBED_INPUT_MODES = ("plain", "enriched")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def embed_text(
    text: str, model_id: str, dimension: int = 1024, max_retries: int = 6
) -> list[float]:
    """Embed text using Titan Embed v2 with exponential backoff."""
    truncated = text[:8000]

    body = json.dumps(
        {
            "inputText": truncated,
            "dimensions": dimension,
            "normalize": True,
        }
    )

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
            wait = min(30, 2**attempt)
            logger.warning(f"Embed retry {attempt + 1}/{max_retries}: {e}, waiting {wait}s")
            time.sleep(wait)

    raise RuntimeError("unreachable")


def extracted_key(doc_id: str, cache_prefix: str = "") -> str:
    return f"{cache_prefix}extracted/{doc_id}.json"


def embedded_key(doc_id: str, cache_prefix: str = "") -> str:
    return f"{cache_prefix}embedded/{doc_id}.json"


def load_extracted_docs(work_bucket: str, cache_prefix: str = "") -> list[dict]:
    """Load all extracted document JSONs from the work bucket in parallel."""
    keys: list[str] = []
    prefix = f"{cache_prefix}extracted/"
    manifest = f"{prefix}manifest.json"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=work_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != manifest:
                keys.append(key)
    logger.info(f"Found {len(keys)} extracted JSONs under '{prefix}'; downloading in parallel...")

    def fetch(key: str) -> dict:
        return json.loads(s3.get_object(Bucket=work_bucket, Key=key)["Body"].read())

    docs: list[dict] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for i, doc in enumerate(pool.map(fetch, keys), start=1):
            docs.append(doc)
            if i % 500 == 0 or i == len(keys):
                logger.info(f"  Loaded {i}/{len(keys)} extracted JSONs")
    return docs


# Doc types that are embedded PLAIN even in enriched mode. Case law is
# deliberately excluded: its analysis/holding chunks are already semantically
# strong (they once took 13/15 vector_search slots, which is why the authority
# quota and the CITES-chain case-law backfill exist). Case law should arrive
# through the statute it interprets, not by matching a citizen's phrasing
# directly. Aliases are still GENERATED and cached for case law so this can be
# flipped on later as its own measured step. Override via
# alias_enrichment.exclude_doc_types in ingest_config.yaml.
ENRICH_EXCLUDE_DOC_TYPES: frozenset[str] = frozenset(
    {"case_law", "iaao_standard", "uspap_standard"}
)

# Chunks whose heading matches one of these are embedded plain: index / table-
# of-contents chunks are lists of topics, so a generated question like "Are
# mobile homes exempt?" is technically grounded but semantically empty, and it
# pulled garbled old-edition WPAM index chunks into the top 10 for exemption
# queries during the 2026-09-11 staging evaluation.
ENRICH_EXCLUDE_HEADING_PATTERNS: tuple[str, ...] = (
    r"index (of|to) legal decisions",
    r"legal decisions and",
    r"table of contents",
    r"^index\b",
)


class EnrichPolicy:
    """Decides which chunks get the enriched embed input.

    Everything not excluded is enriched. Exclusions (all config-driven via
    alias_enrichment in ingest_config.yaml):
      - doc types (case law, IAAO, USPAP by default — see ENRICH_EXCLUDE_DOC_TYPES)
      - superseded WPAM editions when ``wpam_latest_only`` (older editions exist
        for historical questions only; enriching them made 2013–2023 copies of
        the same passage crowd the top 10)
      - chunks whose heading matches an index / TOC pattern
    """

    def __init__(
        self,
        exclude_doc_types=ENRICH_EXCLUDE_DOC_TYPES,
        exclude_doc_ids: frozenset[str] = frozenset(),
        exclude_heading_patterns=ENRICH_EXCLUDE_HEADING_PATTERNS,
    ):
        import re

        self.exclude_doc_types = frozenset(exclude_doc_types)
        self.exclude_doc_ids = frozenset(exclude_doc_ids)
        self._heading_re = [re.compile(pat, re.IGNORECASE) for pat in exclude_heading_patterns]

    def doc_enriched(self, doc: dict) -> bool:
        return (
            doc.get("doc_type") not in self.exclude_doc_types
            and doc.get("doc_id") not in self.exclude_doc_ids
        )

    def chunk_enriched(self, doc: dict, chunk: dict) -> bool:
        if not self.doc_enriched(doc):
            return False
        heading = " ".join(
            str(x)
            for x in (
                chunk.get("heading") or (chunk.get("metadata") or {}).get("heading") or "",
                chunk.get("subheading") or (chunk.get("metadata") or {}).get("subheading") or "",
            )
        )
        return not any(r.search(heading) for r in self._heading_re)


def superseded_wpam_doc_ids(docs: list[dict]) -> frozenset[str]:
    """WPAM doc_ids that are NOT the newest edition present in ``docs``."""
    from tools.ingestion.lib.wpam_year import extract_wpam_year_from_doc_id

    years = {}
    for d in docs:
        y = extract_wpam_year_from_doc_id(d.get("doc_id", ""))
        if y is not None:
            years[d["doc_id"]] = y
    if not years:
        return frozenset()
    latest = max(years.values())
    return frozenset(doc_id for doc_id, y in years.items() if y < latest)


def _as_policy(exclude_doc_types) -> "EnrichPolicy":
    return (
        exclude_doc_types
        if isinstance(exclude_doc_types, EnrichPolicy)
        else EnrichPolicy(exclude_doc_types=exclude_doc_types)
    )


def chunk_embed_input(
    doc: dict, chunk: dict, embed_input: str, exclude_doc_types=ENRICH_EXCLUDE_DOC_TYPES
) -> str:
    """The string sent to Titan for one chunk.

    ``plain``    → chunk["text"] exactly as before.
    ``enriched`` → title/heading header + gold queries + generated questions +
                   aliases, then the chunk text (see lib/aliases.compose_embed_input),
                   except for doc types in ``exclude_doc_types`` (plain).
    The stored chunk text is never modified.
    """
    if embed_input == "enriched" and _as_policy(exclude_doc_types).chunk_enriched(doc, chunk):
        return compose_embed_input(doc, chunk, chunk.get("aliases"), chunk.get("gold_queries", []))
    return chunk["text"]


def embed_chunks(
    doc: dict,
    model_id: str,
    dimension: int,
    embed_input: str = "plain",
    exclude_doc_types=ENRICH_EXCLUDE_DOC_TYPES,
) -> dict:
    """Embed all chunks for a single document.

    Case-law opinions with extracted text are embedded chunk-by-chunk like the
    rest of the corpus. Cases without opinion text remain thin stubs and skip
    both chunk and document embeddings.

    Records ``doc["embed_input_mode"]`` and per-chunk ``embed_input_chars`` for
    provenance. The document-level embedding is unchanged by ``embed_input``.
    """
    doc_id = doc["doc_id"]
    chunks = doc.get("chunks", [])
    if embed_input not in EMBED_INPUT_MODES:
        raise ValueError(f"embed_input must be one of {EMBED_INPUT_MODES}, got {embed_input!r}")

    if doc.get("doc_type") == "case_law" and not chunks:
        return doc

    effective = (
        "plain"
        if embed_input == "enriched" and not _as_policy(exclude_doc_types).doc_enriched(doc)
        else embed_input
    )
    doc["embed_input_mode"] = effective
    for i, chunk in enumerate(chunks):
        text = chunk_embed_input(doc, chunk, embed_input, exclude_doc_types)
        chunk["embed_input_chars"] = len(text)
        chunk["embedding"] = embed_text(text, model_id, dimension)
        if (i + 1) % 50 == 0:
            logger.info(f"  {doc_id}: embedded {i + 1}/{len(chunks)} chunks")

    doc_text = f"{doc.get('title', '')} {doc.get('summary', '')} "
    if chunks:
        doc_text += chunks[0]["text"][:2000]
    doc["doc_embedding"] = embed_text(doc_text, model_id, dimension)

    return doc


def list_already_embedded(bucket: str, cache_prefix: str = "") -> set[str]:
    """Return set of doc_ids that already have embedding output in the work bucket."""
    embedded = set()
    prefix = f"{cache_prefix}embedded/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                doc_id = key.removeprefix(prefix).removesuffix(".json")
                embedded.add(doc_id)
    return embedded


def main():
    parser = argparse.ArgumentParser(description="Embed documents and chunks for GraphRAG")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--config", default="tools/ingestion/config/ingest_config.yaml")
    parser.add_argument("--max-workers", type=int, default=5)
    parser.add_argument(
        "--cache-prefix",
        default="",
        help="Prefix for every work-bucket key (extracted/, embedded/). "
        "e.g. 'staging/' reads staging/extracted/ and writes staging/embedded/.",
    )
    parser.add_argument(
        "--embed-input",
        choices=EMBED_INPUT_MODES,
        default=None,
        help="What to send to Titan per chunk: 'plain' (chunk text, default) or 'enriched' "
        "(title/heading + gold queries + generated questions + aliases + chunk text). "
        "Falls back to config alias_enrichment.embed_input, then 'plain'.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-embed all documents, ignoring cache"
    )
    parser.add_argument(
        "--smart",
        action="store_true",
        help="Only re-embed docs whose extraction is newer than embedding cache",
    )
    parser.add_argument(
        "--source-filter",
        default="",
        help="Only embed doc_ids matching this prefix (e.g., 'wpam-' to re-embed WPAM only).",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    model_id = config.get("bedrock_embed_model", "amazon.titan-embed-text-v2:0")
    dimension = config.get("embed_dimension", 1024)
    embed_input = args.embed_input or (config.get("alias_enrichment") or {}).get(
        "embed_input", "plain"
    )
    if embed_input not in EMBED_INPUT_MODES:
        parser.error(f"alias_enrichment.embed_input must be one of {EMBED_INPUT_MODES}")
    cache_prefix = args.cache_prefix

    docs = load_extracted_docs(args.work_bucket, cache_prefix)
    # Enrichment policy is computed over the FULL corpus (before --source-filter)
    # so 'latest WPAM edition' is the real latest, not the latest in the subset.
    ae = config.get("alias_enrichment") or {}
    exclude_doc_ids = frozenset()
    if ae.get("wpam_latest_only", True):
        exclude_doc_ids = superseded_wpam_doc_ids(docs)
    exclude_doc_types = EnrichPolicy(
        exclude_doc_types=frozenset(ae.get("exclude_doc_types", sorted(ENRICH_EXCLUDE_DOC_TYPES))),
        exclude_doc_ids=exclude_doc_ids,
        exclude_heading_patterns=tuple(
            ae.get("exclude_heading_patterns", ENRICH_EXCLUDE_HEADING_PATTERNS)
        ),
    )
    logger.info(
        f"Embed input mode: {embed_input}; cache prefix: '{cache_prefix}'; "
        f"enrichment excluded doc types: {sorted(exclude_doc_types.exclude_doc_types)}; "
        f"superseded WPAM editions excluded: {len(exclude_doc_ids)}; "
        f"heading exclusions: {len(exclude_doc_types._heading_re)}"
    )
    logger.info(f"Loaded {len(docs)} extracted documents")

    if args.source_filter:
        before = len(docs)
        docs = [d for d in docs if d["doc_id"].startswith(args.source_filter)]
        logger.info(f"Source filter '{args.source_filter}': {before} → {len(docs)} documents")

    if args.force:
        pass
    elif args.smart:
        from botocore.exceptions import ClientError

        stale = []
        for doc in docs:
            doc_id = doc["doc_id"]
            ext_key = extracted_key(doc_id, cache_prefix)
            emb_key = embedded_key(doc_id, cache_prefix)
            try:
                ext_head = s3.head_object(Bucket=args.work_bucket, Key=ext_key)
                emb_head = s3.head_object(Bucket=args.work_bucket, Key=emb_key)
                if ext_head["LastModified"] > emb_head["LastModified"]:
                    stale.append(doc)
            except ClientError:
                stale.append(doc)
        logger.info(f"Smart mode: {len(stale)}/{len(docs)} documents have stale embeddings")
        docs = stale
    else:
        already_done = list_already_embedded(args.work_bucket, cache_prefix)
        before = len(docs)
        docs = [d for d in docs if d["doc_id"] not in already_done]
        logger.info(
            f"Skipping {before - len(docs)} already-embedded documents, {len(docs)} remaining"
        )

    total_chunks = sum(len(d.get("chunks", [])) for d in docs)
    logger.info(f"Total chunks to embed: {total_chunks}")

    embedded_count = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                embed_chunks, doc, model_id, dimension, embed_input, exclude_doc_types
            ): doc["doc_id"]
            for doc in docs
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                result = future.result()
                n_chunks = len(result.get("chunks", []))
                embedded_count += n_chunks

                cache_key = embedded_key(result["doc_id"], cache_prefix)
                s3.put_object(
                    Bucket=args.work_bucket,
                    Key=cache_key,
                    Body=json.dumps(result, default=str).encode("utf-8"),
                    ContentType="application/json",
                )
                logger.info(
                    f"Embedded {doc_id}: {n_chunks} chunks ({embedded_count}/{total_chunks} total)"
                )

            except Exception as e:
                logger.error(f"FAILED embedding {doc_id}: {e}", exc_info=True)

    logger.info(f"Embedding complete: {embedded_count}/{total_chunks} chunks embedded")

    # GC: delete embedded files not present in the extracted set.
    # The extracted/ prefix is the authoritative document set; anything in
    # embedded/ that doesn't have a matching extracted/ record is stale.
    extracted_ids = {d["doc_id"] for d in load_extracted_docs(args.work_bucket, cache_prefix)}
    embedded_ids = list_already_embedded(args.work_bucket, cache_prefix)
    stale_ids = embedded_ids - extracted_ids
    if stale_ids:
        logger.info(f"GC: removing {len(stale_ids)} stale embedded files (not in extracted set)")
        for doc_id in sorted(stale_ids):
            s3.delete_object(Bucket=args.work_bucket, Key=embedded_key(doc_id, cache_prefix))
        logger.info(f"GC: deleted {len(stale_ids)} stale embedded files")
    else:
        logger.info("GC: no stale embedded files found")


if __name__ == "__main__":
    main()
