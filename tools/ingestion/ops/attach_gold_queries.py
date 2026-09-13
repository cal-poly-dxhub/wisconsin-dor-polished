"""Attach real (gold) eval queries to the chunks that answer them.

Reads the recall eval YAML and writes ``chunk["gold_queries"] = [query, ...]``
into the extracted JSONs under ``{cache_prefix}extracted/``. embed.py's
``--embed-input enriched`` then prepends those queries (as ``Q: ...`` lines)
to the chunk text sent to Titan — the stored chunk text is untouched.

Per case / per expected doc:
  - ``expected_chunk_ids`` given  → attach to exactly those chunks.
  - doc has ≤ 3 chunks            → attach to all of them.
  - otherwise                     → the single best chunk by cosine between the
    Titan embedding of the query and each chunk's PLAIN-text embedding
    (computed on the fly for that doc only; no cache). ``expected_subsection``
    (if given) narrows the candidates to chunks whose heading/subheading
    mention it, when any do.

Idempotent: queries are de-duplicated and a doc is only rewritten when its
gold_queries actually change. ``--reset`` clears existing gold_queries first
(use when the YAML was edited).

Usage:
    AWS_PROFILE=<p> AWS_REGION=us-east-1 uv run python -m tools.ingestion.ops.attach_gold_queries \
        --work-bucket wis-work-bucket-c8e69250 --cache-prefix staging/ \
        --eval-yaml tools/ingestion/tests/recall_eval_queries.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from collections import defaultdict

import boto3
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from tools.ingestion import embed as embed_mod

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")

SMALL_DOC_CHUNKS = 3
_CHUNK_ID_RE = re.compile(r"_chunk_(\d+)$")


def extracted_key(doc_id: str, cache_prefix: str) -> str:
    return f"{cache_prefix}extracted/{doc_id}.json"


def load_eval_cases(path: str) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected a list of cases (or {{cases: [...]}})")
    out = []
    for c in cases:
        if not isinstance(c, dict) or not c.get("query"):
            continue
        out.append(c)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def chunk_index_from_id(chunk_id: str, doc_id: str) -> int | None:
    """``{doc_id}_chunk_0004`` → 4. Also accepts a bare int / numeric string."""
    if isinstance(chunk_id, int):
        return chunk_id
    s = str(chunk_id)
    if s.isdigit():
        return int(s)
    m = _CHUNK_ID_RE.search(s)
    if not m:
        return None
    stem = s[: m.start()]
    if stem and stem != doc_id:
        return None  # belongs to a different doc
    return int(m.group(1))


def _subsection_candidates(chunks: list[dict], subsection: str | None) -> list[int]:
    if not subsection:
        return list(range(len(chunks)))
    needle = str(subsection).lower().strip()
    hits = []
    for i, ch in enumerate(chunks):
        meta = ch.get("metadata") or {}
        hay = f"{meta.get('heading') or ''} {meta.get('subheading') or ''}".lower()
        if needle and needle in hay:
            hits.append(i)
    return hits or list(range(len(chunks)))


def choose_chunk_indices(
    doc: dict,
    case: dict,
    *,
    embed_fn,
    chunk_embeddings: dict[int, list[float]],
    query_embedding: list[float] | None,
) -> list[int]:
    """Decide which chunk indices in ``doc`` the case's query attaches to."""
    doc_id = doc["doc_id"]
    chunks = doc.get("chunks", [])
    if not chunks:
        return []

    explicit = case.get("expected_chunk_ids") or []
    if explicit:
        idxs = []
        for cid in explicit:
            i = chunk_index_from_id(cid, doc_id)
            if i is not None and 0 <= i < len(chunks):
                idxs.append(i)
        if idxs:
            return sorted(set(idxs))
        logger.warning(
            f"  {case.get('id')}: expected_chunk_ids {explicit} not in {doc_id}; using cosine"
        )

    if len(chunks) <= SMALL_DOC_CHUNKS:
        return list(range(len(chunks)))

    candidates = _subsection_candidates(chunks, case.get("expected_subsection"))
    if len(candidates) == 1:
        return candidates

    if query_embedding is None:
        query_embedding = embed_fn(case["query"])
    best_i, best_sim = candidates[0], -2.0
    for i in candidates:
        if i not in chunk_embeddings:
            chunk_embeddings[i] = embed_fn(chunks[i]["text"])
        sim = cosine(query_embedding, chunk_embeddings[i])
        if sim > best_sim:
            best_i, best_sim = i, sim
    logger.info(f"  {case.get('id')}: {doc_id} → chunk {best_i} (cos={best_sim:.3f})")
    return [best_i]


def attach_to_doc(doc: dict, cases: list[dict], *, embed_fn, reset: bool = False) -> bool:
    """Mutate ``doc`` in place; return True when gold_queries changed."""
    chunks = doc.get("chunks", [])
    before = [list(c.get("gold_queries") or []) for c in chunks]
    if reset:
        for c in chunks:
            c.pop("gold_queries", None)

    chunk_embeddings: dict[int, list[float]] = {}
    for case in cases:
        query = str(case["query"]).strip()
        q_emb = None
        idxs = choose_chunk_indices(
            doc,
            case,
            embed_fn=embed_fn,
            chunk_embeddings=chunk_embeddings,
            query_embedding=q_emb,
        )
        for i in idxs:
            existing = chunks[i].setdefault("gold_queries", [])
            if query.lower() not in {q.lower() for q in existing}:
                existing.append(query)

    for c in chunks:
        if "gold_queries" in c and not c["gold_queries"]:
            del c["gold_queries"]
    after = [list(c.get("gold_queries") or []) for c in chunks]
    return before != after


def _get_json(bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception:
        return None


def load_doc(work_bucket: str, doc_id: str, cache_prefix: str) -> tuple[dict | None, bool]:
    """Load the prefixed extracted JSON; seed from unprefixed extracted/ if absent."""
    doc = _get_json(work_bucket, extracted_key(doc_id, cache_prefix))
    if doc is not None:
        return doc, False
    if cache_prefix:
        doc = _get_json(work_bucket, extracted_key(doc_id, ""))
        if doc is not None:
            return doc, True
    return None, False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--cache-prefix", default="", help="e.g. 'staging/'")
    parser.add_argument("--eval-yaml", default="tools/ingestion/tests/recall_eval_queries.yaml")
    parser.add_argument("--config", default="tools/ingestion/config/ingest_config.yaml")
    parser.add_argument(
        "--tier", action="append", default=None, help="Only these tiers (repeatable)"
    )
    parser.add_argument("--reset", action="store_true", help="Clear existing gold_queries first")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = embed_mod.load_config(args.config)
    model_id = config.get("bedrock_embed_model", "amazon.titan-embed-text-v2:0")
    dimension = config.get("embed_dimension", 1024)

    def embed_fn(text: str) -> list[float]:
        return embed_mod.embed_text(text, model_id, dimension)

    cases = load_eval_cases(args.eval_yaml)
    if args.tier:
        cases = [c for c in cases if c.get("tier") in set(args.tier)]
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        for doc_id in case.get("expected_doc_ids") or []:
            by_doc[doc_id].append(case)
    logger.info(
        f"{len(cases)} cases → {len(by_doc)} expected docs "
        f"(prefix='{args.cache_prefix}', dry_run={args.dry_run})"
    )

    changed = missing = seeded_n = 0
    for doc_id in sorted(by_doc):
        doc, seeded = load_doc(args.work_bucket, doc_id, args.cache_prefix)
        if doc is None:
            logger.warning(f"  {doc_id}: no extracted JSON (prefixed or production); skipping")
            missing += 1
            continue
        did_change = attach_to_doc(doc, by_doc[doc_id], embed_fn=embed_fn, reset=args.reset)
        if seeded:
            seeded_n += 1
        if did_change or seeded:
            changed += 1
            n_attached = sum(len(c.get("gold_queries") or []) for c in doc.get("chunks", []))
            logger.info(
                f"  {doc_id}: {n_attached} gold-query attachments across "
                f"{sum(1 for c in doc.get('chunks', []) if c.get('gold_queries'))} chunks"
                f"{' (seeded from production extracted/)' if seeded else ''}"
            )
            if not args.dry_run:
                s3.put_object(
                    Bucket=args.work_bucket,
                    Key=extracted_key(doc_id, args.cache_prefix),
                    Body=json.dumps(doc, default=str).encode("utf-8"),
                    ContentType="application/json",
                )
        else:
            logger.info(f"  {doc_id}: unchanged")

    logger.info(
        f"Done: {changed} docs written, {seeded_n} seeded, {missing} missing, "
        f"{len(by_doc) - changed - missing} unchanged"
    )


if __name__ == "__main__":
    main()
