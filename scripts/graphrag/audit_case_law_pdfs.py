"""
Audit CourtListener PDF availability for case-law metadata.

This script is intentionally read-only against S3. It lists case-law metadata
objects, searches CourtListener by exact citation, records available PDF source
fields, and can validate that the preferred PDF URL returns PDF bytes.

Usage:
    python scripts/graphrag/audit_case_law_pdfs.py \
        --bucket wis-raw-bucket-c8e69250 \
        --profile widor \
        --sample-size 50 \
        --validate-pdfs \
        --output /tmp/case_law_pdf_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
CL_STORAGE_BASE = "https://storage.courtlistener.com/"
PREFERRED_OPINION_TYPES = ("combined-opinion", "lead-opinion")


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def s3_client(profile: str | None):
    if profile:
        return boto3.Session(profile_name=profile).client("s3")
    return boto3.client("s3")


def list_case_law_metadata(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "case-law-" in key and key.endswith(".txt.metadata.json"):
                keys.append(key)
    return sorted(keys)


def spread_sample(keys: list[str], sample_size: int | None, limit: int | None) -> list[str]:
    if limit:
        keys = keys[:limit]
    if not sample_size or sample_size >= len(keys):
        return keys
    if sample_size <= 1:
        return keys[:1]
    indices = sorted({
        round(i * (len(keys) - 1) / (sample_size - 1))
        for i in range(sample_size)
    })
    return [keys[i] for i in indices]


def courtlistener_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"
    session.headers["User-Agent"] = "wisconsin-dor-case-law-pdf-audit/1.0"
    return session


def pdf_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "wisconsin-dor-case-law-pdf-audit/1.0"
    return session


def choose_opinion(opinions: list[dict[str, Any]]) -> dict[str, Any]:
    for preferred in PREFERRED_OPINION_TYPES:
        for opinion in opinions:
            if opinion.get("type") == preferred:
                return opinion
    return opinions[0] if opinions else {}


def search_exact_citation(citation: str, session: requests.Session) -> dict[str, Any] | None:
    params = {"q": f'"{citation}"', "type": "o", "page_size": 5}
    response = session.get(CL_SEARCH_URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    for result in data.get("results") or []:
        if citation in (result.get("citation") or []):
            return result
    return None


def storage_pdf_url(local_path: str | None) -> str:
    if not local_path:
        return ""
    if not local_path.lower().endswith(".pdf"):
        return ""
    return CL_STORAGE_BASE + local_path.lstrip("/")


def validate_pdf_url(url: str, session: requests.Session) -> dict[str, Any]:
    if not url:
        return {"pdf_valid": False, "pdf_status": "", "pdf_content_type": "", "pdf_error": ""}
    try:
        response = session.get(
            url,
            headers={"Range": "bytes=0-15"},
            stream=True,
            timeout=25,
            allow_redirects=True,
        )
        first = next(response.iter_content(chunk_size=16), b"")
        content_type = response.headers.get("content-type", "")
        is_pdf = first.startswith(b"%PDF") or "application/pdf" in content_type.lower()
        response.close()
        return {
            "pdf_valid": is_pdf,
            "pdf_status": response.status_code,
            "pdf_content_type": content_type,
            "pdf_error": "",
        }
    except requests.RequestException as exc:
        return {
            "pdf_valid": False,
            "pdf_status": "",
            "pdf_content_type": "",
            "pdf_error": type(exc).__name__,
        }


def audit_key(
    s3,
    bucket: str,
    key: str,
    cl_session: requests.Session,
    pdf_check_session: requests.Session,
    validate_pdfs: bool,
) -> dict[str, Any]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    meta = json.loads(obj["Body"].read())
    attrs = meta.get("metadataAttributes", {})
    citation = attrs.get("citation", "")
    row: dict[str, Any] = {
        "metadata_key": key,
        "citation": citation,
        "current_source_url": attrs.get("source_url", ""),
        "match_status": "missing-citation" if not citation else "miss",
        "case_name": "",
        "courtlistener_url": "",
        "cluster_id": "",
        "opinion_id": "",
        "opinion_type": "",
        "local_path": "",
        "local_pdf_url": "",
        "download_url": "",
        "preferred_pdf_url": "",
        "preferred_pdf_source": "",
        "pdf_valid": "",
        "pdf_status": "",
        "pdf_content_type": "",
        "pdf_error": "",
    }
    if not citation:
        return row

    result = search_exact_citation(citation, cl_session)
    if not result:
        return row

    opinion = choose_opinion(result.get("opinions") or [])
    local_path = opinion.get("local_path") or ""
    local_pdf_url = storage_pdf_url(local_path)
    download_url = opinion.get("download_url") or ""
    preferred_pdf_url = local_pdf_url or download_url

    row.update({
        "match_status": "exact",
        "case_name": result.get("caseName") or result.get("caseNameFull") or "",
        "courtlistener_url": f"https://www.courtlistener.com{result.get('absolute_url', '')}",
        "cluster_id": result.get("cluster_id") or "",
        "opinion_id": opinion.get("id") or "",
        "opinion_type": opinion.get("type") or "",
        "local_path": local_path,
        "local_pdf_url": local_pdf_url,
        "download_url": download_url,
        "preferred_pdf_url": preferred_pdf_url,
        "preferred_pdf_source": "local_path" if local_pdf_url else ("download_url" if download_url else ""),
    })

    if validate_pdfs:
        row.update(validate_pdf_url(preferred_pdf_url, pdf_check_session))
    return row


def write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "metadata_key",
        "citation",
        "current_source_url",
        "match_status",
        "case_name",
        "courtlistener_url",
        "cluster_id",
        "opinion_id",
        "opinion_type",
        "local_path",
        "local_pdf_url",
        "download_url",
        "preferred_pdf_url",
        "preferred_pdf_source",
        "pdf_valid",
        "pdf_status",
        "pdf_content_type",
        "pdf_error",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "exact_matches": sum(1 for row in rows if row["match_status"] == "exact"),
        "misses": sum(1 for row in rows if row["match_status"] == "miss"),
        "with_local_path": sum(1 for row in rows if row["local_path"]),
        "with_download_url": sum(1 for row in rows if row["download_url"]),
        "with_any_pdf_url": sum(1 for row in rows if row["preferred_pdf_url"]),
        "valid_pdfs": sum(1 for row in rows if row["pdf_valid"] is True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit CourtListener PDF availability")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="raw/")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--validate-pdfs", action="store_true")
    parser.add_argument("--output", default="/tmp/case_law_pdf_audit.csv")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("COURTLISTENER_TOKEN")
    if not token:
        raise RuntimeError("COURTLISTENER_TOKEN is required in the environment or .env")

    s3 = s3_client(args.profile)
    cl_session = courtlistener_session(token)
    pdf_check_session = pdf_session()

    keys = list_case_law_metadata(s3, args.bucket, args.prefix)
    logger.info("Found %s case-law txt metadata files", len(keys))
    keys = spread_sample(keys, args.sample_size, args.limit)
    logger.info("Auditing %s metadata files", len(keys))

    rows: list[dict[str, Any]] = []
    for i, key in enumerate(keys, start=1):
        try:
            row = audit_key(
                s3,
                args.bucket,
                key,
                cl_session,
                pdf_check_session,
                args.validate_pdfs,
            )
        except Exception as exc:  # noqa: BLE001 - keep long audits moving.
            logger.warning("[%s/%s] failed %s: %s", i, len(keys), key, exc)
            row = {
                "metadata_key": key,
                "citation": "",
                "current_source_url": "",
                "match_status": "error",
                "case_name": "",
                "courtlistener_url": "",
                "cluster_id": "",
                "opinion_id": "",
                "opinion_type": "",
                "local_path": "",
                "local_pdf_url": "",
                "download_url": "",
                "preferred_pdf_url": "",
                "preferred_pdf_source": "",
                "pdf_valid": False,
                "pdf_status": "",
                "pdf_content_type": "",
                "pdf_error": type(exc).__name__,
            }
        rows.append(row)
        if i % 25 == 0 or i == len(keys):
            logger.info("Progress %s/%s summary=%s", i, len(keys), summarize(rows))
        time.sleep(args.delay)

    write_csv(args.output, rows)
    logger.info("Wrote %s", args.output)
    logger.info("Final summary: %s", summarize(rows))


if __name__ == "__main__":
    main()
