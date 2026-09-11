"""Read structured WPAM decision-flowchart JSON sidecars from S3.

The WPAM prints a handful of decision flowcharts (agricultural classification,
mobile-home exempt/taxable, the general §70.11 exemption path, bible camps,
property held in trust, manufacturing classification). Each is a single
flattened raster image in the PDF — the text extractor sees *nothing* of the
chart's branch logic, so vector search and get_section cannot retrieve it. We
therefore hand-transcribe each chart into a structured sidecar
(``tools/ingestion/flowcharts/data/{flowchart_id}.json``, uploaded to
``s3://{RAW_BUCKET}/flowcharts/{flowchart_id}.json``) that captures the decision
tree as nodes + edges plus the per-step authorities (statutes, admin rules, case
law, WPAM pages) printed in each step.

The retrieval tools ``list_flowcharts`` and ``get_flowchart`` read these here —
mirroring the ``list_worksheets`` / ``get_worksheet`` idiom. Reads are cached per
warm container: the sidecars change only at the annual WPAM refresh.

Every chart carries the DOR's own disclaimer verbatim ("This flow chart provides
general information and may not apply in every situation. A thorough review of
each property is still required."). The tools present the decision path as
guidance; they never assert a definitive exempt/taxable determination.
"""

from __future__ import annotations

import json
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

FLOWCHART_PREFIX = "flowcharts/"

# Registry of the flowcharts we publish sidecars for. Keeping this list here
# (rather than a bucket LIST) makes list_flowcharts deterministic and lets us
# describe a chart — and match it in the pre-loop router — before its sidecar is
# fetched. ``router_description`` is the text the embedding router scores the
# user's query against; keep it topical (title + what it decides + governing
# statutes/vocabulary).
FLOWCHART_REGISTRY = [
    {
        "flowchart_id": "flowcharts-ag-classification",
        "title": "Determining Agricultural Classification",
        "summary": "Whether land qualifies for agricultural classification (sec. 70.32(2)(a)4): qualifying use, primary use, prior production season, Jan 1 compatibility.",
        "statute": "sec. 70.32(2)(a)4., Wis. Stats.",
        "wpam_page": "14-11",
        "router_description": "Determining agricultural classification. Decision tree for whether land qualifies for agricultural classification under sec. 70.32(2)(a)4 based on qualifying use, primary use, production season. Tax 18.05.",
    },
    {
        "flowchart_id": "flowcharts-manufacturing",
        "title": "Manufacturing Classification (Construction-Related Establishment)",
        "summary": "Whether a construction-related establishment or cabinet shop is manufacturing assessed by DOR: sales to contractors, employment in installation, SIC code.",
        "statute": None,
        "wpam_page": "17-7",
        "router_description": "Manufacturing classification. Decision tree for whether a construction-related establishment or cabinet shop is manufacturing, assessed by Department of Revenue, based on sales to contractors, employment in installation, and SIC code.",
    },
    {
        "flowchart_id": "flowcharts-mobile-home",
        "title": "Determining if a Mobile Home is Exempt or Taxable",
        "summary": "Whether a mobile home, recreational mobile home, or camping trailer is exempt or taxable: RMH definition, parking permit fee, dealer stock, motor vehicle, camping trailer, default real-property assessment (sec. 70.17(3)).",
        "statute": "secs. 66.0435, 70.111, 70.112, 70.17(3), Wis. Stats.",
        "wpam_page": "18-22",
        "router_description": "Determining if a mobile home is exempt or taxable. Decision tree for recreational mobile homes, camping trailers, manufactured homes: exempt vs taxable under sec. 70.11, 70.111, 70.112, 70.17 and parking permit fees 66.0435.",
    },
    {
        "flowchart_id": "flowcharts-exempt-70-11",
        "title": "Determining if Property is Exempt under sec. 70.11",
        "summary": "General property-tax exemption path under sec. 70.11: annual review, owner inquiry, Property Tax Exemption Request (Form PR-230) March 1 filing, meeting exemption requirements under specific state law.",
        "statute": "sec. 70.11, Wis. Stats.",
        "wpam_page": "19-2",
        "router_description": "Determining if property is exempt under sec. 70.11. General decision tree for property tax exemption eligibility, Property Tax Exemption Request form PR-230, exemption review, March 1 filing deadline.",
    },
    {
        "flowchart_id": "flowcharts-bible-camp",
        "title": "Bible Camps — Determining if Property is Exempt",
        "summary": "Whether Bible camp property is exempt under sec. 70.11(11): PR-230 filing, 40-acre limit, religious nonprofit corporation, religious purpose, pecuniary profit. (Specific camp exemption — NOT the general church exemption under sec. 70.11(4).)",
        "statute": "sec. 70.11(11), Wis. Stats.",
        "wpam_page": "19-25",
        "router_description": "Bible camp property tax exemption under sec. 70.11(11). Specifically for a Bible camp or religious summer camp owned by a religious nonprofit corporation, with a 40-acre exemption limit. This is the narrow camp exemption only; it does NOT cover ordinary churches, houses of worship, or the general religious-use exemption under sec. 70.11(4).",
    },
    {
        "flowchart_id": "flowcharts-trust-public",
        "title": "Property Held in Trust in Public Interest",
        "summary": "Whether property held in trust in the public interest is exempt under sec. 70.11(20): PR-230 filing, preservation use (native plants/animals, Indian mounds, geological formations), open to public, reasonable restrictions, pecuniary profit, county board determination.",
        "statute": "sec. 70.11(20), Wis. Stats.",
        "wpam_page": "19-27",
        "router_description": "Property held in trust in public interest under sec. 70.11(20). Decision tree for land preserving native wild plants, native wild animals, Indian mounds or works of ancient persons, geological or geographical formations, open to the public with reasonable restrictions, non-profit ownership.",
    },
]

_KNOWN_IDS = {f["flowchart_id"] for f in FLOWCHART_REGISTRY}
_cache: dict[str, dict] = {}


def list_flowcharts() -> list[dict]:
    """Return the registry of available flowcharts (id, title, summary, statute, page).

    The ``router_description`` field is dropped here — it is an internal routing
    anchor, not something the model needs when choosing a chart by topic.
    """
    return [
        {k: v for k, v in f.items() if k != "router_description"}
        for f in FLOWCHART_REGISTRY
    ]


def get_flowchart(flowchart_id: str, raw_bucket: str, s3_client=None) -> dict:
    """Load one flowchart sidecar from S3.

    Returns the parsed JSON (nodes, edges, per-step authorities, disclaimer), or
    an ``{"error": ...}`` dict when the id is unknown or the sidecar is missing.
    """
    if flowchart_id not in _KNOWN_IDS:
        return {
            "error": f"Unknown flowchart '{flowchart_id}'",
            "available": sorted(_KNOWN_IDS),
        }
    if not raw_bucket:
        return {"error": "Raw bucket not configured"}

    doc = _cache.get(flowchart_id)
    if doc is None:
        s3 = s3_client or boto3.client("s3")
        key = f"{FLOWCHART_PREFIX}{flowchart_id}.json"
        try:
            body = s3.get_object(Bucket=raw_bucket, Key=key)["Body"].read()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                return {
                    "error": (
                        f"Flowchart '{flowchart_id}' structure not yet published. "
                        "Refer the user to the WPAM page or narrative rules instead."
                    )
                }
            raise
        doc = json.loads(body)
        _cache[flowchart_id] = doc

    return doc
