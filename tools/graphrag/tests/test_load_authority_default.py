"""Guard load.py's authority_level resolution.

The original code defaulted a missing authority_level to 6 (FAQ), so any
document whose metadata lacked the field silently rendered as an FAQ in the
UI. Authority must instead fall back to the document's *framework* level
(the single source of truth), and only None when even that is unknown —
never a misleading concrete level.
"""

from __future__ import annotations

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from tools.graphrag.load import resolve_authority_level

_FRAMEWORKS = {
    "frameworks": [
        {"id": "FW-GOV-PUBS", "authority_level": 7},
        {"id": "FW-FAQ", "authority_level": 6},
    ]
}


def test_explicit_authority_level_wins():
    doc = {"authority_level": 3, "framework_id": "FW-GOV-PUBS"}
    assert resolve_authority_level(doc, _FRAMEWORKS) == 3


def test_falls_back_to_framework_level_when_missing():
    """A gov-pub doc with no explicit level inherits 7, not 6 (FAQ)."""
    doc = {"framework_id": "FW-GOV-PUBS"}
    assert resolve_authority_level(doc, _FRAMEWORKS) == 7


def test_missing_level_does_not_default_to_faq():
    doc = {"framework_id": "FW-GOV-PUBS"}
    assert resolve_authority_level(doc, _FRAMEWORKS) != 6


def test_unknown_framework_returns_none_not_six():
    doc = {"framework_id": "FW-MYSTERY"}
    assert resolve_authority_level(doc, _FRAMEWORKS) is None
