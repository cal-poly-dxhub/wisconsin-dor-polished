"""Shared test fixtures for tools/ingestion/tests/.

With pdfChunker.py's AWS calls made lazy (no boto3 calls at import time),
most external modules import cleanly. This conftest only needs to ensure
that the extract.py imports (which touch boto3 at module level for its own
clients) don't fail when no AWS credentials are available in CI/local.
"""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
