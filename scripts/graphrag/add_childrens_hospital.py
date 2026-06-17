"""
One-off: fetch Children's Hospital v. City of Wauwatosa (2025 WI App 43)
from CourtListener, upload to S3, and create Neptune node + edges.

Usage:
    source .env
    AWS_PROFILE=widor AWS_REGION=us-east-1 python scripts/graphrag/add_childrens_hospital.py
"""

import json
import os
import re

import boto3
import requests

BUCKET = "wis-raw-bucket-c8e69250"
GRAPH_ID = "g-ndvl4j73v4"
PREFIX = "raw/"

CITATION = "2025 WI App 43"
CASE_NAME = "Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa"
OPINION_ID = 10601552
CL_OPINION_URL = f"https://www.courtlistener.com/api/rest/v4/opinions/{OPINION_ID}/"
SOURCE_URL = "https://www.courtlistener.com/opinion/10601552/childrens-hospital-of-wisconsin-inc-v-city-of-wauwatosa/"

# Statutes this case interprets (tax exemption for hospitals)
STATUTE_REFS = ["WIS-STAT-70.11"]


def make_doc_id(citation: str) -> str:
    clean = re.sub(r"[%\s.]+", "-", citation).strip("-").lower()
    return f"case-law-{clean}"


def fetch_opinion(token: str) -> str:
    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"

    resp = session.get(CL_OPINION_URL, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    for field in ("plain_text", "html_with_citations", "html", "html_lawbox", "html_columbia", "xml_harvard"):
        text = data.get(field, "")
        if text and text.strip():
            if field.startswith("html") or field.startswith("xml"):
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text

    raise RuntimeError("No opinion text found in any CL field")


def upload_to_s3(s3, doc_id: str, text: str):
    doc_key = f"{PREFIX}{doc_id}/{doc_id}.txt"
    meta_key = f"{PREFIX}{doc_id}/{doc_id}.txt.metadata.json"

    s3.put_object(Bucket=BUCKET, Key=doc_key, Body=text.encode("utf-8"), ContentType="text/plain")
    print(f"  Uploaded opinion text ({len(text)} chars) → s3://{BUCKET}/{doc_key}")

    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id,
            "doc_type": "case_law",
            "framework_id": "FW-CASE-LAW",
            "authority_level": "3",
            "category": "case_law",
            "citation": CITATION,
            "case_name": CASE_NAME,
            "source_url": SOURCE_URL,
            "scholar_url": f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50&as_vis=1&q={CITATION.replace(' ', '+')}",
            "citing_statutes": json.dumps([{"file": "70.pdf", "pages": []}]),
        }
    }
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=json.dumps(metadata, indent=2).encode("utf-8"), ContentType="application/json")
    print(f"  Uploaded metadata → s3://{BUCKET}/{meta_key}")


def create_neptune_node(neptune, doc_id: str):
    query = """
        CREATE (n:CaseLaw {
            id: $id,
            title: $title,
            citation: $citation,
            doc_type: 'case_law',
            authority_level: 3,
            source_url: $source_url,
            source_key: $source_key,
            summary: ''
        }) RETURN n.id
    """
    resp = neptune.execute_query(
        graphIdentifier=GRAPH_ID,
        queryString=query,
        language="OPEN_CYPHER",
        parameters={
            "id": doc_id,
            "title": f"{CASE_NAME}, {CITATION}",
            "citation": CITATION,
            "source_url": SOURCE_URL,
            "source_key": f"{PREFIX}{doc_id}/{doc_id}.txt",
        },
    )
    print(f"  Created CaseLaw node: {doc_id}")


def wire_edges(neptune, doc_id: str):
    # BELONGS_TO framework
    neptune.execute_query(
        graphIdentifier=GRAPH_ID,
        queryString="""
            MATCH (c:CaseLaw {id: $case_id}), (f:Framework {id: 'FW-CASE-LAW'})
            CREATE (c)-[:BELONGS_TO]->(f)
        """,
        language="OPEN_CYPHER",
        parameters={"case_id": doc_id},
    )
    print("  Wired BELONGS_TO → FW-CASE-LAW")

    # Bidirectional CITES to each statute
    for stat_id in STATUTE_REFS:
        neptune.execute_query(
            graphIdentifier=GRAPH_ID,
            queryString="""
                MATCH (c:CaseLaw {id: $case_id}), (s:Statute {id: $stat_id})
                CREATE (c)-[:CITES]->(s)
            """,
            language="OPEN_CYPHER",
            parameters={"case_id": doc_id, "stat_id": stat_id},
        )
        neptune.execute_query(
            graphIdentifier=GRAPH_ID,
            queryString="""
                MATCH (s:Statute {id: $stat_id}), (c:CaseLaw {id: $case_id})
                CREATE (s)-[:CITES]->(c)
            """,
            language="OPEN_CYPHER",
            parameters={"case_id": doc_id, "stat_id": stat_id},
        )
        print(f"  Wired bidirectional CITES ↔ {stat_id}")


def main():
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token:
        raise RuntimeError("Set COURTLISTENER_TOKEN env var (source .env)")

    doc_id = make_doc_id(CITATION)
    print(f"Doc ID: {doc_id}")

    print("\n1. Fetching opinion from CourtListener...")
    text = fetch_opinion(token)
    print(f"   Got {len(text)} chars")

    print("\n2. Uploading to S3...")
    s3 = boto3.client("s3")
    upload_to_s3(s3, doc_id, text)

    print("\n3. Creating Neptune node...")
    region = os.environ.get("AWS_REGION", "us-east-1")
    neptune = boto3.client("neptune-graph", region_name=region)
    create_neptune_node(neptune, doc_id)

    print("\n4. Wiring edges...")
    wire_edges(neptune, doc_id)

    print("\n✓ Done! The agent can now find this case via get_neighbors on WIS-STAT-70.11")
    print(f"  and fetch the full opinion via fetch_case_opinion('{CITATION}')")


if __name__ == "__main__":
    main()
