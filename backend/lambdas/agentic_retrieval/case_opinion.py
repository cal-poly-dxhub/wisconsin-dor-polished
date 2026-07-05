"""fetch_case_opinion tool: fetches full court opinion text from S3 by citation.

Case-law opinions are stored in s3://{RAW_BUCKET}/raw/case-law/{reporter}/{slug}.txt.
Stubs (1-chunk metadata nodes) stay in the Neptune graph. The agent calls
this tool when a question needs actual opinion text, not just the citation.
"""

import logging
import re
import urllib.parse

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

MAX_OPINION_CHARS = 40_000

_REPORTER_PATTERNS = [
    ("f-supp-3d", re.compile(r"\d+-f-supp-3d-\d+")),
    ("f-supp-2d", re.compile(r"\d+-f-supp-2d-\d+")),
    ("f-supp", re.compile(r"\d+-f-supp-\d+")),
    ("f-4th", re.compile(r"\d+-f-4th-\d+")),
    ("f-3d", re.compile(r"\d+-f-3d-\d+")),
    ("f-2d", re.compile(r"\d+-f-2d-\d+")),
    ("l-ed-2d", re.compile(r"\d+-l-ed-2d-\d+")),
    ("n-w-3d", re.compile(r"\d+-n-w-3d-\d+")),
    ("n-w-2d", re.compile(r"\d+-n-w-2d-\d+")),
    ("wis-2d", re.compile(r"\d+-wis-2d-\d+")),
    ("wi-app", re.compile(r"\d+-wi-app-\d+")),
    ("wi", re.compile(r"\d+-wi-\d+")),
    ("s-ct", re.compile(r"\d+-s-ct-\d+")),
    ("u-s", re.compile(r"\d+-u-s-\d+")),
]


def _reporter_for_slug(slug: str) -> str:
    for group, pattern in _REPORTER_PATTERNS:
        if pattern.fullmatch(slug):
            return group
    return "misc"


def _citation_to_slug(citation: str) -> str:
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    return "-".join(normalized.split())


def citation_to_doc_id(citation: str) -> str:
    """Normalize a legal citation to its Neptune doc_id.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
    """
    return f"case-law-{_citation_to_slug(citation)}"


def citation_to_raw_key(citation: str) -> str:
    """Normalize a legal citation to its S3 key.

    Examples:
        '109 Wis. 2d 290' -> 'raw/case-law/wis-2d/109-wis-2d-290.txt'
        '766 F.3d 648'    -> 'raw/case-law/f-3d/766-f-3d-648.txt'
        '2000 WI App 182' -> 'raw/case-law/wi-app/2000-wi-app-182.txt'
    """
    slug = _citation_to_slug(citation)
    reporter = _reporter_for_slug(slug)
    return f"raw/case-law/{reporter}/{slug}.txt"


def scholar_url(citation: str) -> str:
    """Google Scholar search URL for a case citation."""
    q = urllib.parse.quote(citation)
    return f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50&as_vis=1&q={q}"


def fetch_case_opinion(
    citation: str,
    raw_bucket: str,
    s3_client=None,
) -> dict:
    """Fetch the full text of a court opinion by citation.

    Args:
        citation: Legal citation, e.g. '109 Wis. 2d 290'.
        raw_bucket: S3 bucket name where raw opinions live.
        s3_client: Optional boto3 S3 client (injected for tests).

    Returns:
        dict with:
            found: True if opinion was fetched, False otherwise.
            citation: Echo of input.
            raw_key: S3 key that was queried.
            text: Opinion text (truncated if >MAX_OPINION_CHARS).
            scholar_url: Google Scholar search URL (always populated).
            error: Present only when the call could not proceed.
    """
    if not citation or not citation.strip():
        return {
            "found": False,
            "citation": citation,
            "error": "empty citation",
            "scholar_url": scholar_url(citation),
        }

    s3 = s3_client or boto3.client("s3")
    raw_key = citation_to_raw_key(citation)

    try:
        obj = s3.get_object(Bucket=raw_bucket, Key=raw_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "AccessDenied"):
            logger.info(f"No raw opinion for citation '{citation}' (key={raw_key}, code={code})")
            return {
                "found": False,
                "citation": citation,
                "raw_key": raw_key,
                "scholar_url": scholar_url(citation),
            }
        logger.warning(f"S3 error fetching opinion for '{citation}': {e}")
        return {
            "found": False,
            "citation": citation,
            "raw_key": raw_key,
            "scholar_url": scholar_url(citation),
            "error": f"s3 error: {code}",
        }

    text = obj["Body"].read().decode("utf-8", errors="replace")
    if len(text) > MAX_OPINION_CHARS:
        text = (
            text[:MAX_OPINION_CHARS]
            + "\n\n[Opinion truncated to fit context; full text available at the source link.]"
        )

    return {
        "found": True,
        "citation": citation,
        "raw_key": raw_key,
        "text": text,
        "scholar_url": scholar_url(citation),
    }
