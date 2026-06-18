"""Guard extract.py's authority_level resolution.

extract.py baked a default of 6 (FAQ) for general docs and 3 for case-law
stubs into the embedded artifacts. Because downstream load honors an
explicit value, that stale default would survive a re-load. Authority must
fall back to the document's *framework* level (single source of truth from
ingest_config.yaml), never a misleading concrete default.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# Pre-register a fake pdf_chunking.pdfChunker so extract.py imports without
# loading the real one (which calls s3.head_bucket at module load).
_fake_module = MagicMock()
_fake_module.process_pdf_from_s3 = MagicMock()
sys.modules.setdefault("pdf_chunking", MagicMock())
sys.modules.setdefault("pdf_chunking.pdfChunker", _fake_module)

from tools.graphrag.extract import resolve_authority_level  # noqa: E402

_FRAMEWORKS = {
    "frameworks": [
        {"id": "FW-GOV-PUBS", "authority_level": 7},
        {"id": "FW-CASE-LAW", "authority_level": 3},
        {"id": "FW-FAQ", "authority_level": 6},
    ]
}


def test_explicit_metadata_authority_wins():
    meta = {"authority_level": "7", "framework_id": "FW-GOV-PUBS"}
    assert resolve_authority_level(meta, "FW-GOV-PUBS", _FRAMEWORKS) == 7


def test_missing_metadata_falls_back_to_framework_level():
    """An advisory with no explicit level resolves to 7 (Gov.Pub), not 6 (FAQ)."""
    meta = {}
    assert resolve_authority_level(meta, "FW-GOV-PUBS", _FRAMEWORKS) == 7


def test_missing_metadata_does_not_default_to_faq():
    meta = {}
    assert resolve_authority_level(meta, "FW-GOV-PUBS", _FRAMEWORKS) != 6


def test_case_law_resolves_to_three_via_framework():
    meta = {}
    assert resolve_authority_level(meta, "FW-CASE-LAW", _FRAMEWORKS) == 3


def test_unknown_framework_returns_none():
    meta = {}
    assert resolve_authority_level(meta, "FW-MYSTERY", _FRAMEWORKS) is None
