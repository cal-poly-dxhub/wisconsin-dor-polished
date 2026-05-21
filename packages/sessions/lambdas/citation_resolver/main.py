"""Citation Resolver Lambda.

Mints short-lived (15 min) presigned URLs to PDFs in the GraphRAG raw
bucket on demand. Replaces eager URL minting in the agent so citation
cards in restored chat sessions stay clickable indefinitely while a
copied URL still expires within a meeting.

Allow-listed to keys under raw/ to keep accidental access to other
bucket prefixes (work/, embeddings/) impossible.
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

s3 = boto3.client("s3")
EXPIRES_IN = 900  # 15 minutes
ALLOWED_PREFIX = "raw/"


def _raw_bucket() -> str:
    """Read RAW_BUCKET at invocation time so per-test env overrides land.

    A module-level read would bind on first import and silently ignore later
    `@patch.dict(os.environ, ...)` decorations under pytest. Also fails fast
    if the CDK wired an empty string instead of a real bucket name.
    """
    bucket = os.environ.get("RAW_BUCKET", "")
    if not bucket:
        raise RuntimeError("RAW_BUCKET env var is unset or empty")
    return bucket


def _bad_request(reason: str) -> dict:
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "text/plain", "Cache-Control": "no-store"},
        "body": reason,
    }


def handler(event: dict, _context) -> dict:
    bucket = _raw_bucket()
    qs = event.get("queryStringParameters") or {}
    s3_key = qs.get("s3Key")
    page = qs.get("page")

    if not s3_key or not s3_key.startswith(ALLOWED_PREFIX):
        return _bad_request("invalid s3Key")

    page_num: int | None = None
    if page is not None:
        try:
            page_num = int(page)
        except ValueError:
            return _bad_request("page must be an integer")
        if page_num < 1:
            return _bad_request("page must be >= 1")

    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            logger.info(f"citation key not found: {s3_key}")
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "text/plain", "Cache-Control": "no-store"},
                "body": "not found",
            }
        # Any other ClientError (throttle, perms, 5xx) is a real failure.
        logger.error(f"head_object failed for {s3_key}: {code}", exc_info=True)
        raise

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=EXPIRES_IN,
    )
    if page_num:
        url = f"{url}#page={page_num}"

    return {
        "statusCode": 302,
        "headers": {
            "Location": url,
            # Browsers and CDNs MUST NOT cache the redirect. Otherwise a
            # second click after expiry would still get the dead URL.
            "Cache-Control": "no-store",
            # The token rides in the request URL; without this the browser
            # would send Referer: https://.../citation?...&token=... to S3.
            "Referrer-Policy": "no-referrer",
        },
        "body": "",
    }
