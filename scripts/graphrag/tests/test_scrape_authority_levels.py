"""Regression guard: each scrape category's authority_level must match the
canonical authority_level of the framework it belongs to.

The bug this guards against: scrape_documents.py numbered its categories
sequentially and skipped case law (level 3, ingested by separate scripts),
producing a uniform off-by-one from admin_rules onward. That mislabeled
607 advisory nodes as FAQ (level 6 vs 7) and 58 faq_page nodes as WPAM
(level 5 vs 6) in the graph, which surfaced as wrong authority badges in
the UI. The framework levels in ingest_config.yaml are the single source
of truth; per-category authority_level must agree with them.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# scrape_documents.py creates a boto3 S3 client at import time; a region must
# be present or client construction raises NoRegionError during collection.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from scripts.graphrag.scrape_documents import DOCUMENT_SOURCES

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "ingest_config.yaml"


def _framework_authority_levels() -> dict[str, int]:
    """Map framework id -> authority_level from the ingestion config."""
    config = yaml.safe_load(_CONFIG_PATH.read_text())
    return {fw["id"]: fw["authority_level"] for fw in config["frameworks"]}


def test_every_category_authority_matches_its_framework():
    framework_levels = _framework_authority_levels()
    mismatches = []
    for category, cfg in DOCUMENT_SOURCES.items():
        fw_id = cfg["framework_id"]
        expected = framework_levels[fw_id]
        actual = cfg["authority_level"]
        if actual != expected:
            mismatches.append(
                f"{category}: framework {fw_id} is level {expected}, "
                f"but category sets authority_level={actual}"
            )
    assert not mismatches, "Authority-level/framework mismatches:\n" + "\n".join(
        mismatches
    )


def test_advisory_categories_are_gov_pub_level():
    """News and complex-inquiry pages are Gov. Pubs (level 7), not FAQ (6)."""
    for category in ("news_pages", "complex_inquiry_pages"):
        assert DOCUMENT_SOURCES[category]["authority_level"] == 7
