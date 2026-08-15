"""Read structured DOR worksheet JSON sidecars from S3.

The offline extractor (tools/ingestion/worksheets/extract_worksheets.py) writes
one JSON per TID worksheet to ``s3://{RAW_BUCKET}/worksheets/{worksheet_id}.json``.
These describe each worksheet's labels, formulas (described, never evaluated),
and preparer instructions. The retrieval tools ``list_worksheets`` and
``get_worksheet`` read them here — no spreadsheet engine runs in the Lambda.

Reads are cached per warm container: the sidecars change only at the annual
content refresh, so re-fetching on every tool call would be wasteful.
"""

from __future__ import annotations

import json
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

WORKSHEET_PREFIX = "worksheets/"

# Registry of the worksheets we publish sidecars for. Keeping this list here
# (rather than a bucket LIST) makes list_worksheets deterministic and lets us
# describe a worksheet even before its sidecar is fetched.
WORKSHEET_REGISTRY = [
    {
        "worksheet_id": "worksheets-tidbase",
        "title": "TID Base Value Workbook",
        "summary": "Certify a new/amended TID base value (PE-606, PE-608, PE-619, PE-615A).",
    },
    {
        "worksheet_id": "worksheets-tidsub",
        "title": "TID Subtraction (Base Value) Workbook",
        "summary": "Determine TID base-value subtractions (current values, new construction).",
    },
    {
        "worksheet_id": "worksheets-decrement",
        "title": "TID Base Redetermination (Decrement) Worksheet",
        "summary": "Redetermine (decrement) a TID base value after equalized-value decline.",
    },
    {
        "worksheet_id": "worksheets-tidbase-ppremoval",
        "title": "TID Base Value — Personal Property Removal Workbook",
        "summary": "Adjust a TID base value for the statewide removal of personal property.",
    },
]

_KNOWN_IDS = {w["worksheet_id"] for w in WORKSHEET_REGISTRY}
_cache: dict[str, dict] = {}


def list_worksheets() -> list[dict]:
    """Return the registry of available worksheets (id, title, summary)."""
    return WORKSHEET_REGISTRY


def get_worksheet(
    worksheet_id: str, raw_bucket: str, sheet: str | None = None, s3_client=None
) -> dict:
    """Load one worksheet sidecar from S3.

    Returns the parsed JSON (optionally filtered to a single ``sheet``), or an
    ``{"error": ...}`` dict when the id is unknown or the sidecar is missing.
    """
    if worksheet_id not in _KNOWN_IDS:
        return {
            "error": f"Unknown worksheet '{worksheet_id}'",
            "available": sorted(_KNOWN_IDS),
        }
    if not raw_bucket:
        return {"error": "Raw bucket not configured"}

    doc = _cache.get(worksheet_id)
    if doc is None:
        s3 = s3_client or boto3.client("s3")
        key = f"{WORKSHEET_PREFIX}{worksheet_id}.json"
        try:
            body = s3.get_object(Bucket=raw_bucket, Key=key)["Body"].read()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                return {
                    "error": (
                        f"Worksheet '{worksheet_id}' structure not yet published. "
                        "Refer the user to the instruction PDF or tif-manual instead."
                    )
                }
            raise
        doc = json.loads(body)
        _cache[worksheet_id] = doc

    if sheet:
        matched = [s for s in doc.get("sheets", []) if s.get("sheet") == sheet]
        if not matched:
            available = [s.get("sheet") for s in doc.get("sheets", [])]
            return {"error": f"Sheet '{sheet}' not found", "available_sheets": available}
        return {**{k: v for k, v in doc.items() if k != "sheets"}, "sheets": matched}

    return doc
