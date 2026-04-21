"""Tests for citation → raw S3 slug normalization.

The agent receives case citations like '109 Wis. 2d 290' and needs to
convert them to the raw bucket key format: 'case-law-109-wis-2d-290'.
This mapping must match the slugification used by the upload script
so 92%+ of stubs resolve to real files.
"""

import pytest

from packages.graphrag.lambdas.agentic_retrieval.case_opinion import citation_to_raw_slug


@pytest.mark.parametrize(
    "citation, expected",
    [
        ("109 Wis. 2d 290", "case-law-109-wis-2d-290"),
        ("766 F.3d 648", "case-law-766-f-3d-648"),
        ("2000 WI App 182", "case-law-2000-wi-app-182"),
        ("457 N.W.2d 514", "case-law-457-n-w-2d-514"),
        ("2001 WI 92", "case-law-2001-wi-92"),
        ("5 N.W.3d 952", "case-law-5-n-w-3d-952"),
        # Strip trailing/leading whitespace
        ("  109 Wis. 2d 290  ", "case-law-109-wis-2d-290"),
        # Collapse multiple spaces
        ("109  Wis.   2d  290", "case-law-109-wis-2d-290"),
        # Already lowercased
        ("109 wis. 2d 290", "case-law-109-wis-2d-290"),
    ],
)
def test_citation_to_raw_slug(citation: str, expected: str) -> None:
    assert citation_to_raw_slug(citation) == expected


def test_citation_to_raw_slug_empty() -> None:
    # Empty input returns a slug with just the prefix — caller should check before using
    assert citation_to_raw_slug("") == "case-law-"


def test_citation_to_raw_slug_punctuation_only() -> None:
    # All-punctuation input — after stripping becomes empty
    assert citation_to_raw_slug("...,,,") == "case-law-"
