"""Tests for citation → raw S3 key normalization.

The agent receives case citations like '109 Wis. 2d 290' and needs to
convert them to the raw bucket key: 'raw/case-law/wis-2d/109-wis-2d-290.txt'.
This mapping must match the slugification used by the upload script
so 92%+ of stubs resolve to real files.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "lambdas", "agentic_retrieval"))
from case_opinion import citation_to_raw_key


@pytest.mark.parametrize(
    "citation, expected",
    [
        ("109 Wis. 2d 290", "raw/case-law/wis-2d/109-wis-2d-290.txt"),
        ("766 F.3d 648", "raw/case-law/f-3d/766-f-3d-648.txt"),
        ("2000 WI App 182", "raw/case-law/wi-app/2000-wi-app-182.txt"),
        ("457 N.W.2d 514", "raw/case-law/n-w-2d/457-n-w-2d-514.txt"),
        ("2001 WI 92", "raw/case-law/wi/2001-wi-92.txt"),
        ("5 N.W.3d 952", "raw/case-law/n-w-3d/5-n-w-3d-952.txt"),
        ("424 U.S. 1", "raw/case-law/u-s/424-u-s-1.txt"),
        ("102 S. Ct. 2613", "raw/case-law/s-ct/102-s-ct-2613.txt"),
        ("123 F. Supp. 2d 456", "raw/case-law/f-supp-2d/123-f-supp-2d-456.txt"),
        # Strip trailing/leading whitespace
        ("  109 Wis. 2d 290  ", "raw/case-law/wis-2d/109-wis-2d-290.txt"),
        # Collapse multiple spaces
        ("109  Wis.   2d  290", "raw/case-law/wis-2d/109-wis-2d-290.txt"),
        # Already lowercased
        ("109 wis. 2d 290", "raw/case-law/wis-2d/109-wis-2d-290.txt"),
    ],
)
def test_citation_to_raw_key(citation: str, expected: str) -> None:
    assert citation_to_raw_key(citation) == expected


def test_citation_to_raw_key_unknown_reporter_goes_to_misc() -> None:
    result = citation_to_raw_key("99 Misc. Reporter 42")
    assert result.startswith("raw/case-law/misc/")
    assert result.endswith(".txt")
