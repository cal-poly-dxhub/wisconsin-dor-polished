"""
Audit and clean up legacy document nodes lacking a public source_url.

Older ingestion runs used different doc-id schemes (statutes-77-document,
admin_rules-document-12, CH-70, wpam-...-vol-1-2011, ...). Re-ingestion under
the current manifest-driven scheme (statutes-77, admin_rules-tax-12,
wpam-wisconsin-property-assessment-manual-2011, ...) created NEW nodes instead
of updating the old ones, leaving duplicates with stale chunks and no
source_url. Those stale chunks still surface in vector search, and their
missing source_url is what forces the frontend onto the citation-resolver
(presigned S3) fallback.

This script:
1. Finds every non-stub, non-Chunk, non-Framework node whose source_url is
   missing or not an http(s) URL.
2. Maps each to its modern counterpart via per-label rules (chapter number,
   WPAM year, IAAO title tokens, ...) and verifies the counterpart exists in
   the graph with a public URL.
3. Classifies each node:
   - duplicate-delete: counterpart verified -> safe to DETACH DELETE the
     node and its chunks.
   - backfill: node IS the live doc but missing source_url -> set it from
     the counterpart/manifest URL.
   - manual: no confident counterpart -> listed for human investigation.
4. With --dry-run (default) prints the report. With --apply performs the
   deletions/backfills.

Usage:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 uv run python \
        tools/ingestion/ops/cleanup_legacy_docs.py --graph-id g-ndvl4j73v4 [--apply]
"""

import argparse
import json
import logging
import os
import re
import time

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None) -> dict:
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters

    for attempt in range(8):
        try:
            resp = client.execute_query(**kwargs)
            payload = resp.get("payload")
            if payload is None:
                return {}
            return json.loads(payload.read())
        except Exception as e:
            name = type(e).__name__
            msg = str(e)
            is_throttle = (
                "Throttling" in name or "Unprocessable" in name or "resubmit" in msg.lower()
            )
            if is_throttle and attempt < 7:
                wait = min(2**attempt, 30)
                logger.warning(f"  Throttled ({name}), retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
    return {}


def fetch_urlless_docs(client, graph_id: str) -> list[dict]:
    result = execute_query(
        client,
        graph_id,
        "MATCH (n) WHERE NOT n:Chunk AND NOT n:Framework "
        "AND (n.stub IS NULL OR n.stub = false) "
        "AND (n.source_url IS NULL OR NOT n.source_url STARTS WITH 'http') "
        "OPTIONAL MATCH (c:Chunk)-[:EXTRACTED_FROM]->(n) "
        "RETURN labels(n)[0] AS label, n.id AS id, n.title AS title, count(c) AS chunks "
        "ORDER BY chunks DESC",
    )
    return result.get("results", [])


def fetch_healthy_docs(client, graph_id: str) -> dict[str, dict]:
    """All non-stub docs WITH a public source_url, keyed by id."""
    result = execute_query(
        client,
        graph_id,
        "MATCH (n) WHERE NOT n:Chunk AND NOT n:Framework "
        "AND (n.stub IS NULL OR n.stub = false) "
        "AND n.source_url STARTS WITH 'http' "
        "OPTIONAL MATCH (c:Chunk)-[:EXTRACTED_FROM]->(n) "
        "RETURN labels(n)[0] AS label, n.id AS id, n.title AS title, "
        "n.source_url AS source_url, count(c) AS chunks",
    )
    return {row["id"]: row for row in result.get("results", [])}


_STATUTE_NUM_RE = re.compile(r"(?:statutes\D*?|CH-)(\d+)")
_ADMIN_NUM_RE = re.compile(r"admin_rules\D*?(\d+)")
_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})")

_TITLE_STOPWORDS = frozenset(
    {"standard", "on", "of", "and", "the", "iaao", "manual", "for", "a", "an", "to"}
)


def _title_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
        if t and t not in _TITLE_STOPWORDS
    }


def find_counterpart(doc: dict, healthy: dict[str, dict]) -> tuple[str | None, str]:
    """Return (counterpart_id, reasoning) for a legacy URL-less doc."""
    label = doc["label"]
    doc_id = doc["id"] or ""
    title = doc.get("title") or ""

    if label == "Statute":
        m = _STATUTE_NUM_RE.search(doc_id)
        if m:
            candidate = f"statutes-{m.group(1)}"
            if candidate in healthy:
                return candidate, f"statute chapter {m.group(1)}"
        return None, "no chapter number match"

    if label == "AdminRule":
        m = _ADMIN_NUM_RE.search(doc_id)
        if m:
            candidate = f"admin_rules-tax-{m.group(1)}"
            if candidate in healthy:
                return candidate, f"admin rule Tax {m.group(1)}"
        return None, "no Tax chapter number match"

    if label == "AssessmentManual":
        m = _YEAR_RE.search(doc_id) or _YEAR_RE.search(title)
        if m:
            candidate = f"wpam-wisconsin-property-assessment-manual-{m.group(1)}"
            if candidate in healthy:
                return candidate, f"WPAM edition {m.group(1)}"
        return None, "no WPAM year match"

    if label == "Constitution":
        candidates = [h for h in healthy.values() if h["label"] == "Constitution"]
        if len(candidates) == 1:
            return candidates[0]["id"], "single healthy Constitution doc"
        return None, f"{len(candidates)} healthy Constitution docs (need exactly 1)"

    if label == "USPAPStandard":
        candidates = [h for h in healthy.values() if h["label"] == "USPAPStandard"]
        if len(candidates) == 1:
            return candidates[0]["id"], "single healthy USPAP doc"
        return None, f"{len(candidates)} healthy USPAP docs (need exactly 1)"

    if label == "IAOStandard":
        legacy_tokens = _title_tokens(doc_id + " " + title)
        best_id, best_score = None, 0.0
        for h in healthy.values():
            if h["label"] != "IAOStandard":
                continue
            h_tokens = _title_tokens(h["id"] + " " + (h.get("title") or ""))
            if not legacy_tokens or not h_tokens:
                continue
            score = len(legacy_tokens & h_tokens) / len(legacy_tokens | h_tokens)
            if score > best_score:
                best_id, best_score = h["id"], score
        if best_id and best_score >= 0.4:
            return best_id, f"IAAO title match (jaccard {best_score:.2f})"
        return None, f"no confident IAAO title match (best {best_score:.2f})"

    # Malformed case-law node (id "case-law", title is the citation).
    if doc_id == "case-law" and title:
        slug = "-".join(re.sub(r"[^a-z0-9]", " ", title.lower()).split())
        candidate = f"case-law-{slug}"
        result_reason = f"citation slug from title '{title}'"
        return (candidate, result_reason) if candidate in healthy else (None, result_reason)

    return None, "no rule for this label"


def delete_doc_and_chunks(client, graph_id: str, doc_id: str) -> int:
    """Delete a document node and all its chunks. Returns chunks deleted."""
    # Delete chunks in batches to stay under query limits.
    deleted = 0
    while True:
        result = execute_query(
            client,
            graph_id,
            "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(n {id: $id}) "
            "WITH c LIMIT 200 DETACH DELETE c RETURN count(*) AS deleted",
            {"id": doc_id},
        )
        rows = result.get("results", [])
        batch = rows[0]["deleted"] if rows else 0
        deleted += batch
        if batch < 200:
            break
    execute_query(client, graph_id, "MATCH (n {id: $id}) DETACH DELETE n", {"id": doc_id})
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Audit/clean legacy URL-less document nodes")
    parser.add_argument("--graph-id", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Perform deletions/backfills (default: dry-run)"
    )
    parser.add_argument("--report-file", default="", help="Optional path to write JSON report")
    args = parser.parse_args()

    client = boto3.client(
        "neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    graph_id = args.graph_id

    urlless = fetch_urlless_docs(client, graph_id)
    healthy = fetch_healthy_docs(client, graph_id)
    logger.info(f"Found {len(urlless)} URL-less non-stub docs; {len(healthy)} healthy docs")

    duplicates: list[dict] = []
    manual: list[dict] = []
    for doc in urlless:
        counterpart_id, reason = find_counterpart(doc, healthy)
        entry = {
            "label": doc["label"],
            "id": doc["id"],
            "title": doc.get("title"),
            "chunks": doc["chunks"],
            "counterpart": counterpart_id,
            "reason": reason,
        }
        if counterpart_id:
            cp = healthy[counterpart_id]
            entry["counterpart_chunks"] = cp["chunks"]
            entry["counterpart_url"] = cp["source_url"]
            entry["action"] = "duplicate-delete"
            duplicates.append(entry)
        else:
            entry["action"] = "manual"
            manual.append(entry)

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n=== Legacy doc cleanup report ({mode}) ===\n")
    print(f"-- {len(duplicates)} duplicate legacy docs (delete node + chunks) --")
    for e in duplicates:
        print(
            f"  DELETE {e['label']:<17} {e['id']:<55} chunks={e['chunks']:<5} "
            f"-> keep {e['counterpart']} (chunks={e['counterpart_chunks']}; {e['reason']})"
        )
    print(f"\n-- {len(manual)} docs needing manual investigation --")
    for e in manual:
        print(
            f"  MANUAL {e['label']:<17} {e['id']:<55} chunks={e['chunks']:<5} ({e['reason']}) "
            f"title={e['title']!r}"
        )

    if args.report_file:
        with open(args.report_file, "w") as f:
            json.dump({"duplicates": duplicates, "manual": manual}, f, indent=2)
        logger.info(f"Report written to {args.report_file}")

    if not args.apply:
        print("\nDry run only — re-run with --apply to delete duplicates.")
        return

    total_chunks = 0
    for e in duplicates:
        logger.info(f"Deleting {e['id']} ({e['chunks']} chunks)...")
        total_chunks += delete_doc_and_chunks(client, graph_id, e["id"])
    logger.info(f"Deleted {len(duplicates)} legacy docs and {total_chunks} chunks.")

    remaining = fetch_urlless_docs(client, graph_id)
    logger.info(f"Remaining URL-less non-stub docs after cleanup: {len(remaining)}")
    for doc in remaining:
        logger.info(f"  STILL MISSING URL: {doc['label']} {doc['id']} (chunks={doc['chunks']})")


if __name__ == "__main__":
    main()
