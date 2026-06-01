"""fetch_case_opinion tool: fetches full court opinion text from S3 by citation.

Case-law opinions are stored as raw .txt in s3://{RAW_BUCKET}/raw/case-law-*/.
Stubs (1-chunk metadata nodes) stay in the Neptune graph. The agent calls
this tool when a question needs actual opinion text, not just the citation.
"""

import logging
import re
import urllib.parse

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)

CASE_LAW_PREFIX = "case-law-"

# Cap opinion text to keep agent context manageable. 40k chars ~ 10k tokens.
MAX_OPINION_CHARS = 40_000


def citation_to_raw_slug(citation: str) -> str:
    """Normalize a legal citation to the raw S3 key slug.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
        '2000 WI App 182' -> 'case-law-2000-wi-app-182'

    The mapping matches the slugification used by the upload script so that
    most stubs resolve to real full-opinion files.
    """
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    tokens = normalized.split()
    return CASE_LAW_PREFIX + "-".join(tokens)


def _scholar_url(citation: str) -> str:
    """Google Scholar search URL for a case citation."""
    q = urllib.parse.quote(citation)
    return (
        f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50"
        f"&as_vis=1&q={q}"
    )


# Public alias so other modules can build the same Google Scholar search URL
# without reaching into a private helper. _scholar_url stays for internal use.
def scholar_url(citation: str) -> str:
    """Public wrapper around _scholar_url for cross-module use."""
    return _scholar_url(citation)


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
            "scholar_url": _scholar_url(citation),
        }

    s3 = s3_client or boto3.client("s3")
    slug = citation_to_raw_slug(citation)
    raw_key = f"raw/{slug}/{slug}.txt"

    try:
        obj = s3.get_object(Bucket=raw_bucket, Key=raw_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            logger.info(f"No raw opinion for citation '{citation}' (key={raw_key})")
            return {
                "found": False,
                "citation": citation,
                "raw_key": raw_key,
                "scholar_url": _scholar_url(citation),
            }
        logger.warning(f"S3 error fetching opinion for '{citation}': {e}")
        return {
            "found": False,
            "citation": citation,
            "raw_key": raw_key,
            "scholar_url": _scholar_url(citation),
            "error": f"s3 error: {code}",
        }

    text = obj["Body"].read().decode("utf-8", errors="replace")
    if len(text) > MAX_OPINION_CHARS:
        text = text[:MAX_OPINION_CHARS] + "\n\n[Opinion truncated to fit context; full text available at the source link.]"

    return {
        "found": True,
        "citation": citation,
        "raw_key": raw_key,
        "text": text,
        "scholar_url": _scholar_url(citation),
    }
