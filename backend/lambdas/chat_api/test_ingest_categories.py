"""INGEST_CATEGORIES must agree with the ingestion pipeline's own config.

`POST /admin/ingest` writes a `.metadata.json` sidecar next to the uploaded
document, and `tools/ingestion/extract.py` reads `framework_id`, `doc_type`,
and `authority_level` straight off that sidecar. A framework id or doc type
that the pipeline does not know about therefore loads a document that hangs
off a Framework node nobody created, and no test anywhere else would notice.

This parses the real `ingest_config.yaml` and `document_manifest.yaml` (not
fixtures) so drift in either direction fails CI.
"""

import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("SESSIONS_TABLE_NAME", "test-sessions")
os.environ.setdefault("MESSAGES_TABLE_NAME", "test-messages")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "layers"))
sys.path.insert(0, os.path.dirname(__file__))

with patch.dict(os.environ, {"SESSIONS_TABLE_NAME": "test-sessions-table"}):
    from main import (
        INGEST_CATEGORIES,
        _AllowlistRedirectHandler,
        _is_allowed_ingest_url,
    )

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INGEST_CONFIG = _REPO_ROOT / "tools" / "ingestion" / "config" / "ingest_config.yaml"
_MANIFEST = _REPO_ROOT / "tools" / "ingestion" / "config" / "document_manifest.yaml"


@pytest.fixture(scope="module")
def ingest_config():
    assert _INGEST_CONFIG.exists(), f"ingest_config.yaml not found at {_INGEST_CONFIG}"
    return yaml.safe_load(_INGEST_CONFIG.read_text())


@pytest.fixture(scope="module")
def manifest_categories():
    assert _MANIFEST.exists(), f"document_manifest.yaml not found at {_MANIFEST}"
    return yaml.safe_load(_MANIFEST.read_text())["categories"]


@pytest.mark.parametrize("category", sorted(INGEST_CATEGORIES))
def test_framework_id_exists_in_ingest_config(ingest_config, category):
    known = {fw["id"] for fw in ingest_config["frameworks"]}
    framework_id = INGEST_CATEGORIES[category]["framework_id"]
    assert framework_id in known, (
        f"{category}: framework_id {framework_id!r} is not a framework in "
        f"ingest_config.yaml (known: {sorted(known)})"
    )


@pytest.mark.parametrize("category", sorted(INGEST_CATEGORIES))
def test_doc_type_exists_in_ingest_config(ingest_config, category):
    known = set(ingest_config["doc_types"])
    doc_type = INGEST_CATEGORIES[category]["doc_type"]
    assert doc_type in known, (
        f"{category}: doc_type {doc_type!r} is not a key of ingest_config.yaml "
        f"doc_types (known: {sorted(known)})"
    )


@pytest.mark.parametrize("category", sorted(INGEST_CATEGORIES))
def test_authority_level_matches_its_framework(ingest_config, category):
    by_id = {fw["id"]: fw for fw in ingest_config["frameworks"]}
    entry = INGEST_CATEGORIES[category]
    expected = by_id[entry["framework_id"]]["authority_level"]
    assert entry["authority_level"] == expected, (
        f"{category}: authority_level {entry['authority_level']} does not match "
        f"{entry['framework_id']} (level {expected}) in ingest_config.yaml"
    )


@pytest.mark.parametrize("category", sorted(INGEST_CATEGORIES))
def test_matches_the_scrapers_manifest_block(manifest_categories, category):
    """The scraper stamps the same three fields for the same category name, so
    a document ingested from the admin page must be indistinguishable from the
    same document scraped from the manifest."""
    assert category in manifest_categories, (
        f"{category} is offered by /admin/ingest but is not a category in document_manifest.yaml"
    )
    expected = manifest_categories[category]
    entry = INGEST_CATEGORIES[category]
    assert entry["framework_id"] == expected["framework_id"]
    assert entry["doc_type"] == expected["doc_type"]
    assert entry["authority_level"] == expected["authority_level"]


def _manifest_urls(manifest_categories):
    for block in manifest_categories.values():
        for entry in block.get("urls", []):
            yield entry if isinstance(entry, str) else entry["url"]


def test_every_manifest_source_is_on_the_ingest_allowlist(manifest_categories):
    """Re-ingesting any manifest document from the admin page must be allowed, so
    a new publisher added to the manifest has to be added to the allowlist too."""
    rejected = sorted(
        {
            urllib.parse.urlparse(url).hostname
            for url in _manifest_urls(manifest_categories)
            if not _is_allowed_ingest_url(url)
        }
    )
    assert not rejected, f"manifest hosts missing from INGEST_ALLOWED_HOSTS: {rejected}"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.revenue.wi.gov/documents/wpam26.pdf",  # not https
        "file:///proc/self/environ",
        "ftp://www.revenue.wi.gov/x.pdf",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/x.pdf",
        "https://evil.example.com/x.pdf",
        "https://www.revenue.wi.gov.evil.example.com/x.pdf",
        "https://user:pw@www.revenue.wi.gov/x.pdf",
        "https://www.revenue.wi.gov:8443/x.pdf",
        "https://[::1]/x.pdf",
        "not a url",
    ],
)
def test_ingest_url_allowlist_rejects(url):
    assert not _is_allowed_ingest_url(url)


def test_ingest_url_allowlist_accepts_source_hosts():
    assert _is_allowed_ingest_url("https://www.revenue.wi.gov/documents/wpam26.pdf")
    assert _is_allowed_ingest_url("https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf")
    assert _is_allowed_ingest_url("https://WWW.REVENUE.WI.GOV:443/x.pdf")


def test_redirect_off_the_allowlist_is_refused():
    handler = _AllowlistRedirectHandler()
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/")
