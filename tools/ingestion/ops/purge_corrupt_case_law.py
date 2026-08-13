"""
Purge case-law nodes whose stored opinion text belongs to a DIFFERENT case
(citation→text mis-assignment from the Google Scholar fallback in
ingest_case_law.py — Scholar's fuzzy citation search returned the wrong
opinion, so the node's citation/title and its chunk text describe different
cases).

This is distinct from duplication (dedup_case_law_docket.py): these are not
extra copies of one opinion, they are single nodes carrying the WRONG opinion.
Because their chunks are wrong, they corrupt retrieval — a statute's
CITES→<citation> edge leads the agent to another case's holdings.

The set below was found by a corpus-wide scan (title case-name vs
opinion-body case-name, zero shared party token) and hand-verified against the
raw opinion text. Each entry records what the node CLAIMS vs what its text
actually IS. We delete the node + its chunks; the (wrong) statute CITES edges
drop with it. We deliberately do NOT re-point those edges — the citation
genuinely belongs to the claimed case, for which no correct node exists, so
re-pointing would just relocate the error. The dropped citations are logged
for a targeted re-ingest attempt.

Usage:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      uv run python tools/ingestion/ops/purge_corrupt_case_law.py \
        --graph-id g-ndvl4j73v4 \
        --work-bucket wis-work-bucket-c8e69250 \
        [--apply]   # default: dry-run
"""

import argparse
import json
import logging
import os

import boto3

logger = logging.getLogger("purge_corrupt_case_law")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Hand-verified corrupt nodes. doc_id -> (claims, actually_is).
CORRUPT = {
    "case-law-414-wis-2d-633": (
        "Wisconsin State Legislature v. Josh Kaul (414 Wis. 2d 633)",
        "Daniel Birge v. Simplicity Credit Union (docket 2024AP567)",
    ),
    "case-law-395-wis-2d-351": (
        "Adams Outdoor Advertising v. City of Madison (395 Wis. 2d 351)",
        "City of Waukesha v. City of Waukesha Board of Review (docket 2019AP1479)",
    ),
}


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
    ap.add_argument("--graph-id", required=True)
    ap.add_argument("--work-bucket", help="Purge loser caches so a reload can't restore them")
    ap.add_argument("--apply", action="store_true", help="Execute deletion (default dry-run)")
    args = ap.parse_args()

    client = boto3.client("neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    ids = list(CORRUPT)
    # Report current state: chunks + inbound statute CITES that will be dropped.
    for cid, (claims, actual) in CORRUPT.items():
        rows = run_query(
            client,
            args.graph_id,
            "MATCH (c:CaseLaw {id: $id}) "
            "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(c) "
            "OPTIONAL MATCH (s:Statute)-[:CITES]->(c) "
            "RETURN count(DISTINCT ch) AS chunks, count(DISTINCT s) AS statute_cites",
            {"id": cid},
        )
        r = rows[0] if rows else {"chunks": 0, "statute_cites": 0}
        exists = run_query(
            client, args.graph_id, "MATCH (c:CaseLaw {id:$id}) RETURN c.id AS id", {"id": cid}
        )
        logger.info(
            f"{cid}  exists={bool(exists)}  chunks={r['chunks']}  "
            f"statute_cites_dropped={r['statute_cites']}"
        )
        logger.info(f"    claims:  {claims}")
        logger.info(f"    is:      {actual}")

    if not args.apply:
        logger.info("DRY-RUN. Re-run with --apply to delete these nodes + chunks.")
        return

    res = run_query(
        client,
        args.graph_id,
        "UNWIND $ids AS cid "
        "MATCH (c:CaseLaw {id: cid}) "
        "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(c) "
        "DETACH DELETE ch, c RETURN count(DISTINCT c) AS d",
        {"ids": ids},
    )
    logger.info(f"Deleted {res[0]['d'] if res else 0} corrupt nodes (+ chunks)")

    if args.work_bucket:
        keys = [f"{p}/{cid}.json" for cid in ids for p in ("extracted", "embedded")]
        existing = []
        for k in keys:
            try:
                s3.head_object(Bucket=args.work_bucket, Key=k)
                existing.append(k)
            except s3.exceptions.ClientError:
                pass
        if existing:
            s3.delete_objects(
                Bucket=args.work_bucket, Delete={"Objects": [{"Key": k} for k in existing]}
            )
        logger.info(f"Purged {len(existing)} cache objects")
    logger.info(
        "Dropped citations (attempt targeted re-ingest later): "
        + ", ".join(claims for claims, _ in CORRUPT.values())
    )


if __name__ == "__main__":
    main()
