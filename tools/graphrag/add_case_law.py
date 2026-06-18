"""
Add a case-law opinion from CourtListener to S3 and Neptune.

Creates the raw S3 text file, metadata sidecar, Neptune node, and edges
(BELONGS_TO FW-CASE-LAW, bidirectional CITES to statutes).

IMPORTANT: CourtListener's cluster ID and opinion ID are DIFFERENT numbers.
The human-readable URL uses the cluster ID (e.g. /opinion/10601552/case-name/)
but the API endpoint for fetching opinion *text* uses the opinion ID. To find
the real opinion ID, fetch the cluster first:

    GET /api/rest/v4/clusters/{cluster_id}/
    -> sub_opinions: ["https://.../opinions/{REAL_OPINION_ID}/"]

If you pass the cluster ID to the opinions endpoint, you'll get a completely
unrelated case's text back (no error, just wrong data).

Usage:
    source .env  # needs COURTLISTENER_TOKEN
    AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/graphrag/add_case_law.py \
        --citation "2025 WI App 43" \
        --case-name "Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa" \
        --opinion-id 11068140 \
        --statutes WIS-STAT-70.11

    # Dry run (fetch + print, no upload):
    AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/graphrag/add_case_law.py \
        --citation "2025 WI App 43" \
        --case-name "Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa" \
        --opinion-id 11068140 \
        --statutes WIS-STAT-70.11 \
        --dry-run
"""

import argparse
import json
import os
import re

import boto3
import requests

BUCKET = "wis-raw-bucket-c8e69250"
GRAPH_ID = "g-ndvl4j73v4"
PREFIX = "raw/"


def make_doc_id(citation: str) -> str:
    clean = re.sub(r"[%\s.]+", "-", citation).strip("-").lower()
    return f"case-law-{clean}"


def fetch_opinion(opinion_id: int, token: str) -> str:
    """Fetch opinion text from CourtListener REST API.

    Uses the OPINION ID (not cluster ID). See module docstring for the
    distinction — using the wrong ID silently returns a different case.
    """
    url = f"https://www.courtlistener.com/api/rest/v4/opinions/{opinion_id}/"
    resp = requests.get(url, headers={"Authorization": f"Token {token}"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    for field in ("plain_text", "html_with_citations", "html", "html_lawbox", "html_columbia", "xml_harvard"):
        text = data.get(field, "")
        if text and text.strip():
            if field.startswith("html") or field.startswith("xml"):
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text

    raise RuntimeError(f"No opinion text found in any field for opinion {opinion_id}")


def upload_to_s3(s3, doc_id: str, text: str, citation: str, case_name: str, source_url: str, statutes: list[str]):
    doc_key = f"{PREFIX}{doc_id}/{doc_id}.txt"
    meta_key = f"{PREFIX}{doc_id}/{doc_id}.txt.metadata.json"

    s3.put_object(Bucket=BUCKET, Key=doc_key, Body=text.encode("utf-8"), ContentType="text/plain")
    print(f"  Uploaded opinion text ({len(text)} chars) -> s3://{BUCKET}/{doc_key}")

    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id,
            "doc_type": "case_law",
            "framework_id": "FW-CASE-LAW",
            "authority_level": "3",
            "category": "case_law",
            "citation": citation,
            "case_name": case_name,
            "source_url": source_url,
            "scholar_url": f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50&as_vis=1&q={citation.replace(' ', '+')}",
            "citing_statutes": json.dumps([{"file": f"{s.split('-')[-1]}.pdf", "pages": []} for s in statutes]),
        }
    }
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=json.dumps(metadata, indent=2).encode("utf-8"), ContentType="application/json")
    print(f"  Uploaded metadata -> s3://{BUCKET}/{meta_key}")


def create_neptune_node(neptune, doc_id: str, case_name: str, citation: str, source_url: str):
    query = """
        MERGE (n:CaseLaw {id: $id})
        SET n.title = $title,
            n.citation = $citation,
            n.doc_type = 'case_law',
            n.authority_level = 3,
            n.source_url = $source_url,
            n.source_key = $source_key,
            n.summary = ''
        RETURN n.id
    """
    neptune.execute_query(
        graphIdentifier=GRAPH_ID,
        queryString=query,
        language="OPEN_CYPHER",
        parameters={
            "id": doc_id,
            "title": f"{case_name}, {citation}",
            "citation": citation,
            "source_url": source_url,
            "source_key": f"{PREFIX}{doc_id}/{doc_id}.txt",
        },
    )
    print(f"  Created/updated CaseLaw node: {doc_id}")


def wire_edges(neptune, doc_id: str, statutes: list[str]):
    neptune.execute_query(
        graphIdentifier=GRAPH_ID,
        queryString="""
            MATCH (c:CaseLaw {id: $case_id}), (f:Framework {id: 'FW-CASE-LAW'})
            MERGE (c)-[:BELONGS_TO]->(f)
        """,
        language="OPEN_CYPHER",
        parameters={"case_id": doc_id},
    )
    print("  Wired BELONGS_TO -> FW-CASE-LAW")

    for stat_id in statutes:
        neptune.execute_query(
            graphIdentifier=GRAPH_ID,
            queryString="""
                MATCH (c:CaseLaw {id: $case_id}), (s {id: $stat_id})
                MERGE (c)-[:CITES]->(s)
            """,
            language="OPEN_CYPHER",
            parameters={"case_id": doc_id, "stat_id": stat_id},
        )
        neptune.execute_query(
            graphIdentifier=GRAPH_ID,
            queryString="""
                MATCH (s {id: $stat_id}), (c:CaseLaw {id: $case_id})
                MERGE (s)-[:CITES]->(c)
            """,
            language="OPEN_CYPHER",
            parameters={"case_id": doc_id, "stat_id": stat_id},
        )
        print(f"  Wired bidirectional CITES <-> {stat_id}")


def main():
    parser = argparse.ArgumentParser(description="Add a case-law opinion from CourtListener")
    parser.add_argument("--citation", required=True, help="Legal citation, e.g. '2025 WI App 43'")
    parser.add_argument("--case-name", required=True, help="Full case name")
    parser.add_argument("--opinion-id", required=True, type=int,
                        help="CourtListener OPINION ID (NOT cluster ID — see script header)")
    parser.add_argument("--statutes", nargs="*", default=[],
                        help="Neptune statute node IDs to CITES-link, e.g. WIS-STAT-70.11")
    parser.add_argument("--source-url", default=None,
                        help="CourtListener URL (auto-derived from opinion-id if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, don't upload")
    args = parser.parse_args()

    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token:
        raise RuntimeError("Set COURTLISTENER_TOKEN env var (source .env)")

    doc_id = make_doc_id(args.citation)
    source_url = args.source_url or f"https://www.courtlistener.com/opinion/{args.opinion_id}/"
    print(f"Doc ID: {doc_id}")

    print("\n1. Fetching opinion from CourtListener...")
    text = fetch_opinion(args.opinion_id, token)
    print(f"   Got {len(text)} chars")

    if args.dry_run:
        print(f"\n[DRY RUN] Would upload {len(text)} chars to s3://{BUCKET}/{PREFIX}{doc_id}/{doc_id}.txt")
        print(f"First 300 chars:\n{text[:300]}")
        return

    print("\n2. Uploading to S3...")
    s3 = boto3.client("s3")
    upload_to_s3(s3, doc_id, text, args.citation, args.case_name, source_url, args.statutes)

    print("\n3. Creating Neptune node...")
    region = os.environ.get("AWS_REGION", "us-east-1")
    neptune = boto3.client("neptune-graph", region_name=region)
    create_neptune_node(neptune, doc_id, args.case_name, args.citation, source_url)

    print("\n4. Wiring edges...")
    wire_edges(neptune, doc_id, args.statutes)

    print(f"\nDone! fetch_case_opinion('{args.citation}') will now return the full text.")


if __name__ == "__main__":
    main()
