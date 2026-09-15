"""
Purge the work-bucket caches of case-law dedup LOSERS so a full load cannot
resurrect them, and record them in ``{prefix}dedup/losers.json`` (which
``load.py`` also consults to skip any listed doc_id).

Why this exists
---------------
``ops/dedup_case_law_docket.py`` (Task 45, applied 2026-08-13) deleted 54
loser nodes from the graph. Its cache purge only runs when ``--work-bucket``
is passed, and it was run without it — so ``extracted/`` + ``embedded/`` still
hold every loser, and the next full ``load.py`` re-created all 54 nodes
(graph 1148 → 1202). This script closes that hole with two layers:

  1. delete ``extracted/``, ``embedded/`` and ``aliases/`` for each loser
     (nothing left to load), and
  2. write the loser ids to ``{prefix}dedup/losers.json`` so ``load.py``
     skips them even if a cache file is ever re-created.

How losers are computed
-----------------------
Exactly the way the docket pass did it, reusing its functions by import:
group case-law docs by the UNION of docket number (parsed from the raw opinion
text) and normalized case name, confirm each multi-member group with the
minimum pairwise word-set Jaccard over the opinion body (``--sim-threshold``,
0.6), and only take CONFIDENT groups (sim ≥ threshold AND ≤ 1 distinct case
name). Winner = CourtListener-sourced node if any, then reporter priority.
Corruption suspects and flagged-for-review groups are never touched.

The universe is the ``{prefix}extracted/case-law-*.json`` set — i.e. what
``load.py`` would actually load — rather than the live graph, so the result
does not depend on whether the losers currently exist as nodes.

The hand-verified corrupt ids from ``ops/purge_corrupt_case_law.py`` (Task 49)
are ALWAYS losers and can never be a group's winner — they were re-created
from raw by a later extract and, with bare titles, would otherwise out-rank
the correctly-cited copies of the same opinion on reporter priority.

``--include-ghosts`` additionally treats any NON-case-law extracted doc whose
``s3_key`` no longer exists in the raw bucket as a loser (e.g. the three
``statutes-document-{73,76,78}`` ids that predate manifest-derived ids and
duplicate ``statutes-{73,76,78}``). ``--extra-loser`` adds hand-picked ids.

DRY-RUN by default: lists losers with their winners and touches nothing.

Usage:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      uv run python tools/ingestion/ops/purge_dedup_losers.py \
        --work-bucket wis-work-bucket-c8e69250 \
        --raw-bucket wis-raw-bucket-c8e69250 \
        [--cache-prefix staging/] [--sim-threshold 0.6] \
        [--include-ghosts] [--extra-loser statutes-document-73 ...] \
        [--apply]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from botocore.config import Config  # noqa: E402
from ingest_case_law import _reporter_for_slug, _reporter_priority  # noqa: E402
from ops.dedup_case_law_docket import (  # noqa: E402
    DOCKET_RE,
    UnionFind,
    host_of,
    jaccard,
    norm_name,
    word_set,
)
from ops.purge_corrupt_case_law import CORRUPT  # noqa: E402

logger = logging.getLogger("purge_dedup_losers")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REASON_DOCKET = "docket_dedup"
REASON_CORRUPT = "corrupt_wrong_opinion"
REASON_GHOST = "ghost_no_raw_source"
REASON_MANUAL = "manual"

# Hand-verified citation→text mis-assignments (Task 49). They were purged once
# but re-created from their surviving raw .txt by a later extract run, and now
# carry BARE titles — so the docket pass's "≥2 distinct names" corruption gate
# no longer fires and reporter priority would make e.g. 414-wis-2d-633 (a Kaul
# citation carrying Birge's text) the WINNER over Birge's real citations.
# These ids are therefore never eligible to win and are always purged.
FORCED_LOSERS: frozenset[str] = frozenset(CORRUPT)


# --------------------------------------------------------------------------- #
# Pure grouping (no AWS) — mirrors dedup_case_law_docket.main() step for step.
# --------------------------------------------------------------------------- #
def classify_groups(
    nodes: list[dict],
    texts: dict[str, str | None],
    sim_threshold: float,
    forced_losers: frozenset[str] = FORCED_LOSERS,
):
    """Group case-law nodes and split into (confident, corruption, review).

    ``nodes``: ``[{"id", "title", "url"}, ...]``; ``texts``: id → raw opinion
    text (None when missing). Each returned record is
    ``{"docket", "winner", "losers", "min_sim", "cross_host", "names"}``.
    Ids in ``forced_losers`` can never be a group's winner.
    """
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        n["host"] = host_of(n.get("url"))

    docket_of: dict[str, str] = {}
    for n in nodes:
        t = texts.get(n["id"])
        if t:
            m = DOCKET_RE.search(t[:4000])
            if m:
                docket_of[n["id"]] = m.group(1).upper()
    name_of = {n["id"]: norm_name(n.get("title")) for n in nodes}

    uf = UnionFind([n["id"] for n in nodes])
    for keymap in (docket_of, {k: v for k, v in name_of.items() if v}):
        buckets: dict[str, list[str]] = defaultdict(list)
        for nid, k in keymap.items():
            buckets[k].append(nid)
        for ids in buckets.values():
            for other in ids[1:]:
                uf.union(ids[0], other)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[uf.find(n["id"])].append(n["id"])
    multi = [ids for ids in groups.values() if len(ids) > 1]

    def winner(ids: list[str]) -> str:
        eligible = [i for i in ids if i not in forced_losers] or ids
        cl = [i for i in eligible if by_id[i]["host"] == "cl"]
        pool = cl if cl else eligible
        return sorted(pool, key=lambda i: _reporter_priority(i.replace("case-law-", "")))[0]

    def group_min_sim(ids: list[str]) -> float | None:
        sets = [(i, word_set(texts.get(i) or "")) for i in ids]
        sets = [(i, s) for i, s in sets if s]
        if len(sets) < 2:
            return None
        mn = 1.0
        for a in range(len(sets)):
            for b in range(a + 1, len(sets)):
                mn = min(mn, jaccard(sets[a][1], sets[b][1]))
        return mn

    confident, corruption, review = [], [], []
    for ids in multi:
        w = winner(ids)
        sim = group_min_sim(ids)
        distinct_names = sorted({name_of[i] for i in ids if name_of[i]})
        rec = {
            "docket": next((docket_of[i] for i in ids if i in docket_of), None),
            "winner": w,
            "losers": [i for i in ids if i != w],
            "min_sim": sim,
            "cross_host": {by_id[i]["host"] for i in ids} >= {"cl", "legis"},
            "names": distinct_names,
        }
        if sim is None or sim < sim_threshold:
            review.append(rec)
        elif len(distinct_names) >= 2:
            corruption.append(rec)
        else:
            confident.append(rec)
    return confident, corruption, review


def merge_losers(existing: dict | None, new_entries: dict[str, dict]) -> dict:
    """Union a prior losers.json payload with new entries (new wins on conflict)."""
    losers = dict((existing or {}).get("losers", {}))
    losers.update(new_entries)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "losers": dict(sorted(losers.items())),
    }


# --------------------------------------------------------------------------- #
# S3 plumbing
# --------------------------------------------------------------------------- #
def _get_json(s3, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except s3.exceptions.ClientError as e:  # pragma: no cover — 404 via ClientError
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise


def list_extracted_ids(s3, work_bucket: str, cache_prefix: str) -> list[str]:
    prefix = f"{cache_prefix}extracted/"
    ids: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=work_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and not key.endswith("manifest.json"):
                ids.append(key.removeprefix(prefix).removesuffix(".json"))
    return ids


def load_case_law_nodes(s3, work_bucket: str, cache_prefix: str, ids: list[str]) -> list[dict]:
    def fetch(doc_id: str) -> dict:
        doc = _get_json(s3, work_bucket, f"{cache_prefix}extracted/{doc_id}.json") or {}
        return {"id": doc_id, "title": doc.get("title"), "url": doc.get("source_url")}

    with ThreadPoolExecutor(max_workers=32) as pool:
        return list(pool.map(fetch, ids))


def fetch_opinion_texts(s3, raw_bucket: str, ids: list[str]) -> dict[str, str | None]:
    def fetch(doc_id: str) -> tuple[str, str | None]:
        slug = doc_id.replace("case-law-", "")
        key = f"raw/case-law/{_reporter_for_slug(slug)}/{slug}.txt"
        try:
            body = s3.get_object(Bucket=raw_bucket, Key=key)["Body"].read()
            return doc_id, body.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return doc_id, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        return dict(pool.map(fetch, ids))


def find_ghosts(s3, work_bucket: str, raw_bucket: str, cache_prefix: str, ids: list[str]) -> dict:
    """Non-case-law extracted docs whose raw source object no longer exists."""

    def check(doc_id: str) -> tuple[str, str | None]:
        doc = _get_json(s3, work_bucket, f"{cache_prefix}extracted/{doc_id}.json") or {}
        s3_key = doc.get("s3_key") or ""
        if not s3_key:
            return doc_id, None
        try:
            s3.head_object(Bucket=raw_bucket, Key=s3_key)
            return doc_id, None
        except s3.exceptions.ClientError:
            return doc_id, s3_key

    ghosts: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=32) as pool:
        for doc_id, missing_key in pool.map(check, ids):
            if missing_key:
                ghosts[doc_id] = missing_key
    return ghosts


def cache_keys_for(doc_id: str, cache_prefix: str) -> list[str]:
    return [f"{cache_prefix}{sub}/{doc_id}.json" for sub in ("extracted", "embedded", "aliases")]


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--work-bucket", required=True)
    ap.add_argument("--raw-bucket", required=True)
    ap.add_argument("--cache-prefix", default="", help="e.g. 'staging/'")
    ap.add_argument("--sim-threshold", type=float, default=0.6)
    ap.add_argument(
        "--include-ghosts",
        action="store_true",
        help="Also treat non-case-law extracted docs with no raw source object as losers",
    )
    ap.add_argument(
        "--extra-loser", action="append", default=[], help="Hand-picked doc_id to add (repeatable)"
    )
    ap.add_argument("--apply", action="store_true", help="Delete caches + write losers.json")
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run
    logger.info(f"Mode: {'APPLY (mutating S3)' if apply else 'DRY-RUN (read-only)'}")

    s3 = boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(max_pool_connections=32),
    )
    prefix = args.cache_prefix

    all_ids = list_extracted_ids(s3, args.work_bucket, prefix)
    case_ids = [i for i in all_ids if i.startswith("case-law-")]
    logger.info(
        f"{len(all_ids)} extracted docs under '{prefix}extracted/'; {len(case_ids)} case law"
    )

    nodes = load_case_law_nodes(s3, args.work_bucket, prefix, case_ids)
    texts = fetch_opinion_texts(s3, args.raw_bucket, case_ids)
    logger.info(f"Fetched opinion text for {sum(1 for t in texts.values() if t)}/{len(nodes)}")

    confident, corruption, review = classify_groups(nodes, texts, args.sim_threshold)
    by_id = {n["id"]: n for n in nodes}

    entries: dict[str, dict] = {}
    for rec in confident:
        for lid in rec["losers"]:
            entries[lid] = {
                "winner": rec["winner"],
                "reason": REASON_DOCKET,
                "docket": rec["docket"],
                "min_sim": rec["min_sim"],
            }

    present_ids = set(all_ids)
    corrupt_present = sorted(i for i in FORCED_LOSERS if i in present_ids)
    for doc_id in corrupt_present:
        claims, actual = CORRUPT[doc_id]
        entries[doc_id] = {
            "winner": entries.get(doc_id, {}).get("winner"),
            "reason": REASON_CORRUPT,
            "claims": claims,
            "actually_is": actual,
        }

    ghosts: dict[str, str] = {}
    if args.include_ghosts:
        non_case = [i for i in all_ids if not i.startswith("case-law-")]
        ghosts = find_ghosts(s3, args.work_bucket, args.raw_bucket, prefix, non_case)
        for doc_id, missing_key in ghosts.items():
            entries[doc_id] = {
                "winner": None,
                "reason": REASON_GHOST,
                "missing_raw_key": missing_key,
            }
    for doc_id in args.extra_loser:
        entries.setdefault(doc_id, {"winner": None, "reason": REASON_MANUAL})

    print(
        f"\n===== CONFIDENT MERGES: {len(confident)} groups, "
        f"{sum(len(r['losers']) for r in confident)} losers ====="
    )
    for rec in sorted(confident, key=lambda r: r["min_sim"] or 0):
        w = by_id[rec["winner"]]
        xh = " [CROSS-HOST]" if rec["cross_host"] else ""
        print(f"\n  sim={rec['min_sim']:.2f}{xh}  docket={rec['docket']}")
        print(f"    KEEP {w['host']}:{rec['winner']}  | {w.get('title')}")
        for lid in rec["losers"]:
            print(f"    DROP {by_id[lid]['host']}:{lid}  | {by_id[lid].get('title')}")
    print(
        f"\n  (skipped: {len(corruption)} corruption-suspect groups, "
        f"{len(review)} flagged-for-review groups — never purged)"
    )
    print(f"\n===== KNOWN-CORRUPT (Task 49, forced losers): {len(corrupt_present)} present =====")
    for doc_id in corrupt_present:
        claims, actual = CORRUPT[doc_id]
        print(f"  DROP {doc_id}  | claims {claims!r} but text is {actual!r}")
    if args.include_ghosts:
        print(f"\n===== GHOSTS (no raw source): {len(ghosts)} =====")
        for doc_id, key in sorted(ghosts.items()):
            print(f"  DROP {doc_id}  | missing raw key: {key}")
    if args.extra_loser:
        print(f"\n===== MANUAL: {len(args.extra_loser)} =====")
        for doc_id in args.extra_loser:
            print(f"  DROP {doc_id}")

    keys = [k for lid in sorted(entries) for k in cache_keys_for(lid, prefix)]
    existing: list[str] = []
    for k in keys:
        try:
            s3.head_object(Bucket=args.work_bucket, Key=k)
            existing.append(k)
        except s3.exceptions.ClientError:
            pass
    losers_key = f"{prefix}dedup/losers.json"
    print("\n" + "=" * 60)
    print(
        f"SUMMARY: {len(entries)} loser doc_ids; {len(existing)} cache objects present "
        f"(of {len(keys)} candidate keys under extracted/ embedded/ aliases/)"
    )
    print(f"losers.json target: s3://{args.work_bucket}/{losers_key}")

    if not apply:
        print("\nDRY-RUN. Re-run with --apply to delete the cache objects and write losers.json.")
        return

    for i in range(0, len(existing), 1000):
        batch = existing[i : i + 1000]
        s3.delete_objects(Bucket=args.work_bucket, Delete={"Objects": [{"Key": k} for k in batch]})
    logger.info(f"Deleted {len(existing)} cache objects")

    payload = merge_losers(_get_json(s3, args.work_bucket, losers_key), entries)
    s3.put_object(
        Bucket=args.work_bucket,
        Key=losers_key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"Wrote {len(payload['losers'])} loser ids to s3://{args.work_bucket}/{losers_key}")
    logger.info(
        "APPLY complete. Loser nodes still in the graph are removed by the next load's "
        "Phase 9 (case law: not in extracted/ set) — or delete them by hand for ghosts."
    )


if __name__ == "__main__":
    main()
