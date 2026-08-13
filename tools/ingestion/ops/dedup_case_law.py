"""
One-time remediation for case-law parallel-citation duplication and title rot.

Background
----------
``ingest_case_law.py`` deduplicates parallel reporters (same opinion cited as
both ``N Wis. 2d M`` and ``P N.W.2d Q``) *before* uploading to raw S3, so
``raw/case-law/`` holds one file per opinion. But the work-bucket caches
(``extracted/`` and ``embedded/``) accumulated both sides of each pair from
older, pre-dedup runs, and ``load.py`` enumerates ``embedded/`` — so the graph
ended up with duplicate ``CaseLaw`` nodes for the same opinion.

Three defects resulted:

  1. Duplicate nodes — both parallel cites became live ``CaseLaw`` nodes
     sharing one ``source_url``. The agent can cite the same opinion twice
     under two IDs.
  2. Orphan chunks — a prior ``load.py`` phase-9 GC did ``DETACH DELETE`` on
     stale duplicate *nodes* but left their ``Chunk`` nodes behind (chunks are
     separate nodes). Those chunks stay vector-searchable with no parent doc,
     so they render no citation card yet still surface in retrieval.
  3. Citation-only titles — legacy caches stored titles like
     ``"405 Wis. 2d 616, 405 Wis. 2d 616"`` (no case name), so cards show a
     bare reporter number instead of "Lowe's Home Centers v. City of Delavan".

This script fixes all three against the live graph and the work-bucket caches.
The durable prevention lives in ``extract.py`` (title fallback) and ``load.py``
(chunk-aware phase-9 GC + load-time source_url dedup); this script is the
one-time cleanup of state those fixes can't retroactively repair.

Order of operations (each independently ``--dry-run``-safe):
  A. Collapse duplicate nodes by ``source_url``: keep the reporter-priority
     winner, re-point inbound ``Statute-[:CITES]->loser`` edges onto the
     winner, then ``DETACH DELETE`` the loser (+ any of its chunks).
  B. Delete orphan case-law chunks (no ``EXTRACTED_FROM`` parent).
  C. Backfill titles: derive ``"{case_name}, {citation}"`` from the
     CourtListener URL slug (offline); for nodes whose URL carries no name
     (legis.wisconsin.gov), resolve the name via the CourtListener API.
  D. Purge loser doc_ids from ``extracted/`` and ``embedded/`` so a future
     load can't reintroduce them.

Usage:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      COURTLISTENER_TOKEN=<token> \
      uv run python tools/ingestion/ops/dedup_case_law.py \
        --work-bucket wis-work-bucket-c8e69250 \
        --graph-id g-ndvl4j73v4 \
        [--dry-run]           # default: dry-run; pass --apply to mutate
        [--skip-cl-lookup]    # skip the API step for name-less URLs
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import boto3

# Reuse the canonical citation/reporter/name helpers so this script and the
# ingestion pipeline never drift on slug parsing or reporter precedence.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest_case_law import (  # noqa: E402
    _case_name_from_url,
    _reporter_priority,
)

logger = logging.getLogger("dedup_case_law")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# A title is "bad" when it is just a bare reporter citation (optionally doubled),
# i.e. it carries no case name. Matches "405 Wis. 2d 616", "2023 WI 8",
# "722 N.W.2d 162", "129 S. Ct. 2527", "174 L. Ed. 2d 314", "88 F.3d 12", etc.
_BARE_CITE_RE = re.compile(
    r"^\d+\s+(Wis\.|N\.W\.|S\.\s?Ct\.|L\.\s?Ed\.|F\.|U\.S\.)|^\d{4}\s+WI\b",
    re.IGNORECASE,
)


def title_is_bare_citation(title: str | None) -> bool:
    return bool(title and _BARE_CITE_RE.search(str(title).strip()))


def make_title(case_name: str, citation: str) -> str:
    """Build a display title, mirroring extract.py's derivation."""
    case_name = (case_name or "").strip()
    citation = (citation or "").strip()
    if case_name and citation and case_name != citation:
        return f"{case_name}, {citation}"
    if case_name:
        return case_name
    return citation


# --------------------------------------------------------------------------- #
# Neptune helpers
# --------------------------------------------------------------------------- #
def make_neptune_client():
    return boto3.client("neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def run_query(client, graph_id: str, query: str, params: dict | None = None) -> list[dict]:
    kwargs = {"graphIdentifier": graph_id, "language": "OPEN_CYPHER", "queryString": query}
    if params:
        kwargs["parameters"] = params
    for attempt in range(8):
        try:
            resp = client.execute_query(**kwargs)
            payload = resp.get("payload")
            if payload is None:
                return []
            return json.loads(payload.read()).get("results", [])
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            is_throttle = (
                "Throttling" in name or "Unprocessable" in name or "resubmit" in str(e).lower()
            )
            if is_throttle and attempt < 7:
                wait = min(2**attempt, 30)
                logger.warning(f"  Throttled ({name}), retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
    return []


# --------------------------------------------------------------------------- #
# Step A: collapse duplicate nodes by source_url
# --------------------------------------------------------------------------- #
def dedup_nodes(client, graph_id: str, apply: bool) -> set[str]:
    """Collapse parallel-cite duplicates. Returns the set of removed loser doc_ids."""
    logger.info("== Step A: collapse duplicate CaseLaw nodes by source_url ==")
    nodes = run_query(client, graph_id, "MATCH (c:CaseLaw) RETURN c.id AS id, c.source_url AS url")
    by_url: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.get("url"):
            by_url[n["url"]].append(n["id"])

    losers: list[str] = []
    plan: list[tuple[str, list[str]]] = []  # (winner, [losers])
    for _url, ids in by_url.items():
        if len(ids) <= 1:
            continue
        ordered = sorted(ids, key=lambda i: _reporter_priority(i.replace("case-law-", "")))
        winner, group_losers = ordered[0], ordered[1:]
        plan.append((winner, group_losers))
        losers.extend(group_losers)

    logger.info(
        f"  {len(plan)} duplicate groups; {len(losers)} loser nodes to remove "
        f"(winners chosen by reporter priority)"
    )
    for winner, group_losers in plan[:15]:
        logger.info(f"    keep {winner}  <-  drop {group_losers}")
    if len(plan) > 15:
        logger.info(f"    ... and {len(plan) - 15} more groups")

    if not losers:
        return set()

    if not apply:
        logger.info("  [dry-run] would re-point Statute CITES edges and delete loser nodes+chunks")
        return set(losers)

    # Re-point any Statute-[:CITES]->loser onto the winner (winner may already
    # have the edge; MERGE is idempotent), then delete losers and their chunks.
    for winner, group_losers in plan:
        run_query(
            client,
            graph_id,
            "UNWIND $losers AS lid "
            "MATCH (s:Statute)-[r:CITES]->(l:CaseLaw {id: lid}) "
            "MATCH (w:CaseLaw {id: $winner}) "
            "MERGE (s)-[:CITES]->(w) "
            "DELETE r",
            {"losers": group_losers, "winner": winner},
        )
    deleted = 0
    for i in range(0, len(losers), 100):
        batch = losers[i : i + 100]
        res = run_query(
            client,
            graph_id,
            "UNWIND $ids AS cid "
            "MATCH (c:CaseLaw {id: cid}) "
            "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(c) "
            "DETACH DELETE ch, c "
            "RETURN count(DISTINCT c) AS deleted",
            {"ids": batch},
        )
        deleted += res[0]["deleted"] if res else 0
    logger.info(f"  Deleted {deleted} loser nodes (+ their chunks)")
    return set(losers)


# --------------------------------------------------------------------------- #
# Step B: delete orphan case-law chunks
# --------------------------------------------------------------------------- #
def delete_orphan_chunks(client, graph_id: str, apply: bool) -> None:
    logger.info("== Step B: delete orphan case-law chunks (no parent doc) ==")
    # Neptune openCypher lacks EXISTS{} subquery support here; use OPTIONAL MATCH.
    res = run_query(
        client,
        graph_id,
        "MATCH (ch:Chunk) WHERE ch.id STARTS WITH 'case-law-' "
        "OPTIONAL MATCH (ch)-[:EXTRACTED_FROM]->(c:CaseLaw) "
        "WITH ch, c WHERE c IS NULL "
        "RETURN count(ch) AS n",
    )
    orphan_count = res[0]["n"] if res else 0
    logger.info(f"  {orphan_count} orphan case-law chunks found")
    if not orphan_count or not apply:
        if orphan_count and not apply:
            logger.info("  [dry-run] would DETACH DELETE the orphan chunks")
        return

    total = 0
    while True:
        res = run_query(
            client,
            graph_id,
            "MATCH (ch:Chunk) WHERE ch.id STARTS WITH 'case-law-' "
            "OPTIONAL MATCH (ch)-[:EXTRACTED_FROM]->(c:CaseLaw) "
            "WITH ch, c WHERE c IS NULL "
            "WITH ch LIMIT 1000 "
            "DETACH DELETE ch "
            "RETURN count(ch) AS deleted",
        )
        n = res[0]["deleted"] if res else 0
        total += n
        if n == 0:
            break
        logger.info(f"    deleted {total} orphan chunks so far...")
    logger.info(f"  Deleted {total} orphan case-law chunks")


# --------------------------------------------------------------------------- #
# Step C: backfill titles
# --------------------------------------------------------------------------- #
def backfill_titles(client, graph_id: str, apply: bool, skip_cl_lookup: bool) -> None:
    logger.info("== Step C: backfill citation-only titles ==")
    nodes = run_query(
        client,
        graph_id,
        "MATCH (c:CaseLaw) RETURN c.id AS id, c.title AS title, "
        "c.citation AS citation, c.source_url AS url",
    )
    bad = [n for n in nodes if title_is_bare_citation(n.get("title"))]
    logger.info(f"  {len(bad)} nodes with citation-only titles")

    updates: list[dict] = []  # {id, title}
    needs_cl: list[dict] = []
    for n in bad:
        name = _case_name_from_url(n.get("url") or "")
        if name and not title_is_bare_citation(name) and len(name) > 3:
            updates.append({"id": n["id"], "title": make_title(name, n.get("citation") or "")})
        else:
            needs_cl.append(n)

    logger.info(f"  {len(updates)} recoverable offline from source_url slug")
    logger.info(f"  {len(needs_cl)} require CourtListener API lookup")

    if needs_cl and not skip_cl_lookup:
        cl_updates = resolve_names_via_courtlistener(needs_cl)
        updates.extend(cl_updates)
        logger.info(
            f"  Resolved {len(cl_updates)}/{len(needs_cl)} additional names via CourtListener"
        )
    elif needs_cl:
        logger.info("  --skip-cl-lookup set; leaving name-less nodes as citation-only")

    for u in updates[:15]:
        logger.info(f"    {u['id']}  ->  {u['title']!r}")
    if len(updates) > 15:
        logger.info(f"    ... and {len(updates) - 15} more")

    if not apply:
        logger.info(f"  [dry-run] would update {len(updates)} titles")
        return

    updated = 0
    for i in range(0, len(updates), 200):
        batch = updates[i : i + 200]
        run_query(
            client,
            graph_id,
            "UNWIND $rows AS row MATCH (c:CaseLaw {id: row.id}) SET c.title = row.title",
            {"rows": batch},
        )
        updated += len(batch)
    logger.info(f"  Updated {updated} titles")


def resolve_names_via_courtlistener(nodes: list[dict]) -> list[dict]:
    """Look up case names by citation via the CourtListener search API."""
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token:
        logger.warning("  COURTLISTENER_TOKEN not set; skipping API lookup")
        return []
    try:
        import requests
    except ImportError:
        logger.warning("  requests not available; skipping API lookup")
        return []
    from ingest_case_law import search_courtlistener  # local import to avoid load cost

    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"
    session.headers["User-Agent"] = "wisconsin-dor-case-law-dedup/1.0"

    out: list[dict] = []
    for n in nodes:
        citation = (n.get("citation") or "").strip()
        if not citation:
            continue
        result = search_courtlistener(citation, session)
        if not result:
            continue
        name = result.get("case_name") or ""
        if name and not title_is_bare_citation(name):
            out.append({"id": n["id"], "title": make_title(name, citation)})
        time.sleep(0.3)  # be polite to the API
    return out


# --------------------------------------------------------------------------- #
# Step D: purge loser entries from work-bucket caches
# --------------------------------------------------------------------------- #
def purge_caches(s3, work_bucket: str, loser_ids: set[str], apply: bool) -> None:
    logger.info("== Step D: purge loser doc_ids from extracted/ and embedded/ caches ==")
    if not loser_ids:
        logger.info("  No losers to purge")
        return
    keys = []
    for doc_id in sorted(loser_ids):
        keys.append(f"extracted/{doc_id}.json")
        keys.append(f"embedded/{doc_id}.json")

    # Confirm which actually exist so the count reflects reality.
    existing = []
    for key in keys:
        try:
            s3.head_object(Bucket=work_bucket, Key=key)
            existing.append(key)
        except s3.exceptions.ClientError:
            continue
    logger.info(f"  {len(existing)} cache objects to delete (of {len(keys)} candidates)")
    for key in existing[:15]:
        logger.info(f"    {key}")
    if len(existing) > 15:
        logger.info(f"    ... and {len(existing) - 15} more")

    if not apply:
        logger.info("  [dry-run] would delete the above cache objects")
        return

    for i in range(0, len(existing), 1000):
        batch = existing[i : i + 1000]
        s3.delete_objects(
            Bucket=work_bucket,
            Delete={"Objects": [{"Key": k} for k in batch]},
        )
    logger.info(f"  Deleted {len(existing)} cache objects")


# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Mutate state (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default)")
    parser.add_argument("--skip-cl-lookup", action="store_true", help="Skip CourtListener API step")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run
    mode = "APPLY (mutating)" if apply else "DRY-RUN (read-only)"
    logger.info(f"Mode: {mode}")

    client = make_neptune_client()
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    loser_ids = dedup_nodes(client, args.graph_id, apply)
    delete_orphan_chunks(client, args.graph_id, apply)
    backfill_titles(client, args.graph_id, apply, args.skip_cl_lookup)
    purge_caches(s3, args.work_bucket, loser_ids, apply)

    logger.info("Done." if apply else "Dry-run complete. Re-run with --apply to mutate.")


if __name__ == "__main__":
    main()
