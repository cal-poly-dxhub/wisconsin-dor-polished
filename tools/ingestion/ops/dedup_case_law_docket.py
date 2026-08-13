"""
Cross-host case-law dedup: collapse duplicate opinions that the #14 source_url
pass could not catch because their duplicates live under different hosts
(CourtListener vs the Google Scholar / legis.wisconsin.gov fallback), so they
share no URL.

Since no metadata field is shared across hosts, grouping uses a UNION of two
content-derived signals:
  - docket number (e.g. "2019AP1987") parsed from the opinion caption
  - normalized case name (from the node title)
Neither signal alone is complete (docket ~14 cross-host groups, name ~7; union
~15), so we union them via union-find.

Because content signals risk FALSE MERGES, every candidate group is gated by a
text-similarity confirmation before it is eligible to merge. The key hazard:
a case's Court of Appeals and Supreme Court opinions can share the SAME docket
yet be DIFFERENT opinions — identical docket, divergent text. Word-set Jaccard
over the opinion body separates "same opinion under two citations" (high
overlap, even across Scholar/CL formatting) from "same case, two courts" (low
overlap). Groups below the confirmation threshold are FLAGGED, not merged.

Winner per group: prefer a CourtListener-sourced node (canonical opinion URL),
then reporter priority.

Three output buckets:
  - CONFIDENT MERGES: sim >= threshold AND ≤1 distinct case name → merged on --apply
  - CORRUPTION SUSPECTS: sim >= threshold but ≥2 distinct case names (identical
    text under different names = wrong-opinion mis-assignment) → never merged
  - FLAGGED FOR REVIEW: sim < threshold or missing text → never merged

DRY-RUN by default; --apply executes ONLY the confident merges (re-point
Statute CITES → winner, delete losers + their chunks, purge loser caches).

Usage:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      uv run python tools/ingestion/ops/dedup_case_law_docket.py \
        --work-bucket wis-work-bucket-c8e69250 \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --graph-id g-ndvl4j73v4 \
        [--sim-threshold 0.6]
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest_case_law import _reporter_for_slug, _reporter_priority  # noqa: E402

logger = logging.getLogger("dedup_case_law_docket")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DOCKET_RE = re.compile(r"\b(\d{4}AP\d+)", re.IGNORECASE)
CITE_TAIL_RE = re.compile(r",\s*\d.*$")
BARE_NAME_RE = re.compile(r"^\d+\s+(wis|n w|s ct|l ed|f|u s)\b|^\d{4} wi\b")
# Stop tokens dropped from case-name normalization so "Foo, LLC v. City of Bar"
# and "Foo v. Bar" collapse to the same key.
_NAME_STOP = {
    "llc",
    "inc",
    "co",
    "corp",
    "ltd",
    "lp",
    "dept",
    "department",
    "of",
    "the",
    "a",
    "an",
    "et",
    "al",
}


def host_of(url: str | None) -> str:
    if not url:
        return "none"
    if "courtlistener.com/opinion/" in url:
        return "cl"
    if "docs.legis.wisconsin.gov" in url:
        return "legis"
    return "other"


def norm_name(title: str | None) -> str | None:
    if not title:
        return None
    t = CITE_TAIL_RE.sub("", title).lower()
    # Strip apostrophes BEFORE the punctuation→space pass so "lowe's" and
    # "lowes" collapse to one token (otherwise "lowe s" != "lowes" and a clean
    # dup looks like two distinct names).
    t = t.replace("'", "").replace("’", "")
    t = " ".join(w for w in re.sub(r"[^a-z0-9 ]", " ", t).split() if w not in _NAME_STOP)
    if not t or BARE_NAME_RE.match(t):
        return None
    return t


def word_set(text: str) -> set[str]:
    # Lowercase alphanumeric tokens of length >=4; ignores punctuation/whitespace
    # differences between Scholar-scraped and CL-fetched renderings of one opinion.
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def neptune_client():
    return boto3.client("neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def run_query(client, graph_id, query, params=None):
    kwargs = {"graphIdentifier": graph_id, "language": "OPEN_CYPHER", "queryString": query}
    if params:
        kwargs["parameters"] = params
    resp = client.execute_query(**kwargs)
    payload = resp.get("payload")
    return json.loads(payload.read()).get("results", []) if payload else []


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--work-bucket", help="Needed with --apply to purge loser caches")
    ap.add_argument("--raw-bucket", required=True)
    ap.add_argument("--graph-id", required=True)
    ap.add_argument("--sim-threshold", type=float, default=0.6)
    ap.add_argument("--apply", action="store_true", help="Execute merges (default dry-run)")
    args = ap.parse_args()

    client = neptune_client()
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    nodes = run_query(
        client,
        args.graph_id,
        "MATCH (c:CaseLaw) RETURN c.id AS id, c.title AS title, c.source_url AS url",
    )
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        n["host"] = host_of(n.get("url"))
    logger.info(f"Loaded {len(nodes)} CaseLaw nodes")

    # Fetch opinion text once per node (for docket parse + similarity confirmation).
    def fetch(n):
        slug = n["id"].replace("case-law-", "")
        key = f"raw/case-law/{_reporter_for_slug(slug)}/{slug}.txt"
        try:
            return n["id"], s3.get_object(Bucket=args.raw_bucket, Key=key)["Body"].read().decode(
                "utf-8", "replace"
            )
        except Exception:  # noqa: BLE001
            return n["id"], None

    texts = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for nid, txt in pool.map(fetch, nodes):
            texts[nid] = txt
    have_txt = sum(1 for t in texts.values() if t)
    logger.info(f"Fetched opinion text for {have_txt}/{len(nodes)} nodes")

    # Signal maps
    docket_of = {}
    for n in nodes:
        t = texts.get(n["id"])
        if t:
            m = DOCKET_RE.search(t[:4000])
            if m:
                docket_of[n["id"]] = m.group(1).upper()
    name_of = {n["id"]: norm_name(n.get("title")) for n in nodes}

    # Union-find over docket-shared and name-shared links.
    uf = UnionFind([n["id"] for n in nodes])
    for keymap in (docket_of, {k: v for k, v in name_of.items() if v}):
        buckets = defaultdict(list)
        for nid, k in keymap.items():
            buckets[k].append(nid)
        for ids in buckets.values():
            for other in ids[1:]:
                uf.union(ids[0], other)

    groups = defaultdict(list)
    for n in nodes:
        groups[uf.find(n["id"])].append(n["id"])
    multi = {r: ids for r, ids in groups.items() if len(ids) > 1}

    def winner(ids):
        cl = [i for i in ids if by_id[i]["host"] == "cl"]
        pool = cl if cl else ids
        return sorted(pool, key=lambda i: _reporter_priority(i.replace("case-law-", "")))[0]

    # Confirm each group by minimum pairwise word-set Jaccard over opinion text.
    def group_min_sim(ids):
        sets = [(i, word_set(texts.get(i) or "")) for i in ids]
        sets = [(i, s) for i, s in sets if s]
        if len(sets) < 2:
            return None  # can't confirm (missing text) — treat as needs-review
        mn = 1.0
        for a in range(len(sets)):
            for b in range(a + 1, len(sets)):
                mn = min(mn, jaccard(sets[a][1], sets[b][1]))
        return mn

    confident, corruption, review = [], [], []
    for ids in multi.values():
        w = winner(ids)
        losers = [i for i in ids if i != w]
        sim = group_min_sim(ids)
        distinct_names = sorted({name_of[i] for i in ids if name_of[i]})
        rec = {
            "docket": next((docket_of[i] for i in ids if i in docket_of), None),
            "winner": w,
            "losers": losers,
            "min_sim": sim,
            "cross_host": {by_id[i]["host"] for i in ids} >= {"cl", "legis"},
            "names": distinct_names,
        }
        if sim is None or sim < args.sim_threshold:
            # Divergent text (e.g. appeals vs supreme sharing a docket) or a
            # member with no text to compare — not safe to merge.
            review.append(rec)
        elif len(distinct_names) >= 2:
            # Identical text under ≥2 DISTINCT case names is the signature of
            # citation→text mis-assignment (Scholar fetched the wrong opinion),
            # NOT a dup. Never auto-merge — route to the corruption task.
            corruption.append(rec)
        else:
            confident.append(rec)

    def emit(recs, label):
        print(f"\n===== {label}: {len(recs)} groups =====")
        for rec in sorted(recs, key=lambda r: (r["min_sim"] is None, r["min_sim"] or 0)):
            sim = f"{rec['min_sim']:.2f}" if rec["min_sim"] is not None else "n/a"
            xh = " [CROSS-HOST]" if rec["cross_host"] else ""
            print(f"\n  sim={sim}{xh}  docket={rec['docket']}  names={rec['names']}")
            w = by_id[rec["winner"]]
            print(f"    KEEP {w['host']}:{rec['winner']}  | {w.get('title')}")
            for lid in rec["losers"]:
                print(f"    DROP {by_id[lid]['host']}:{lid}  | {by_id[lid].get('title')}")

    total_redundant = sum(len(r["losers"]) for r in confident)
    emit(review, "FLAGGED FOR REVIEW (divergent text / missing text — not merged)")
    emit(corruption, "CORRUPTION SUSPECTS (identical text, ≥2 distinct names — NOT merged)")
    emit(confident, "CONFIDENT MERGES")
    print("\n" + "=" * 60)
    print(f"SUMMARY (threshold {args.sim_threshold}):")
    print(f"  candidate groups: {len(multi)}")
    print(f"  confident merges: {len(confident)}  ({total_redundant} nodes to delete)")
    print(f"  corruption suspects (skipped): {len(corruption)}")
    print(f"  flagged for review (skipped): {len(review)}")
    print(f"  cross-host confident: {sum(1 for r in confident if r['cross_host'])}")

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to execute the CONFIDENT MERGES only.")
        return

    logger.info(f"APPLY: merging {len(confident)} confident groups...")
    all_losers = []
    for rec in confident:
        w, losers = rec["winner"], rec["losers"]
        # Re-point Statute-[:CITES]->loser onto the winner (idempotent MERGE).
        run_query(
            client, args.graph_id,
            "UNWIND $losers AS lid "
            "MATCH (s:Statute)-[r:CITES]->(l:CaseLaw {id: lid}) "
            "MATCH (w:CaseLaw {id: $winner}) "
            "MERGE (s)-[:CITES]->(w) DELETE r",
            {"losers": losers, "winner": w},
        )
        all_losers.extend(losers)
    # Delete loser nodes + their chunks in batches.
    deleted = 0
    for i in range(0, len(all_losers), 100):
        batch = all_losers[i : i + 100]
        res = run_query(
            client, args.graph_id,
            "UNWIND $ids AS cid "
            "MATCH (c:CaseLaw {id: cid}) "
            "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(c) "
            "DETACH DELETE ch, c RETURN count(DISTINCT c) AS d",
            {"ids": batch},
        )
        deleted += res[0]["d"] if res else 0
    logger.info(f"  Deleted {deleted} loser nodes (+ chunks)")
    # Purge loser doc_ids from the work-bucket caches so a reload can't restore them.
    if args.work_bucket:
        keys = []
        for lid in all_losers:
            keys += [f"extracted/{lid}.json", f"embedded/{lid}.json"]
        existing = []
        for k in keys:
            try:
                s3.head_object(Bucket=args.work_bucket, Key=k)
                existing.append(k)
            except s3.exceptions.ClientError:
                pass
        for i in range(0, len(existing), 1000):
            s3.delete_objects(
                Bucket=args.work_bucket,
                Delete={"Objects": [{"Key": k} for k in existing[i : i + 1000]]},
            )
        logger.info(f"  Purged {len(existing)} cache objects")
    else:
        logger.warning("  --work-bucket not set; skipped cache purge (losers may reload)")
    logger.info("APPLY complete.")


if __name__ == "__main__":
    main()
