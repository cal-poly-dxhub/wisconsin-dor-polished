"""fetch_case_opinion tool: fetches full court opinion text from S3 by citation.

Case-law opinions are stored as raw .txt in s3://{RAW_BUCKET}/raw/case-law-*/.
Stubs (1-chunk metadata nodes) stay in the Neptune graph. The agent calls
this tool when a question needs actual opinion text, not just the citation.
"""

import re


CASE_LAW_PREFIX = "case-law-"


def citation_to_raw_slug(citation: str) -> str:
    """Normalize a legal citation to the raw S3 key slug.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
        '2000 WI App 182' -> 'case-law-2000-wi-app-182'

    The mapping matches the slugification used by the upload script so that
    most stubs resolve to real full-opinion files.
    """
    # Lowercase, then replace every non-alphanumeric char with a space,
    # collapse whitespace, join with hyphens.
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    tokens = normalized.split()
    return CASE_LAW_PREFIX + "-".join(tokens)
