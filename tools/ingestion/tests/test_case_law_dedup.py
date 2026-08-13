"""Guard the durable case-law duplication/title fixes.

Parallel-citation duplicates (same opinion cited as both `Wis. 2d` and
`N.W.2d`) once produced two graph nodes for one opinion, and legacy metadata
without a resolved case_name produced citation-only titles that render
nameless citation cards. Two durable fixes prevent recurrence:

  1. load.dedup_case_law_docs collapses parallel cites by source_url before any
     load phase runs, keeping the highest-priority reporter.
  2. extract's title derivation falls back to the CourtListener URL slug when
     metadata has no case_name, so a title never degrades to a bare citation.

The one-time cleanup of already-loaded state lives in ops/dedup_case_law.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from tools.ingestion.load import dedup_case_law_docs


def _case(doc_id: str, url: str) -> dict:
    return {"doc_id": doc_id, "doc_type": "case_law", "source_url": url}


def test_dedup_keeps_wis_2d_over_nw_and_neutral():
    lowes = "https://www.courtlistener.com/opinion/9375960/lowes/"
    docs = [
        _case("case-law-2023-wi-8", lowes),
        _case("case-law-405-wis-2d-616", lowes),
        _case("case-law-985-n-w-2d-69", lowes),
    ]
    kept = dedup_case_law_docs(docs)
    kept_ids = {d["doc_id"] for d in kept}
    assert kept_ids == {"case-law-405-wis-2d-616"}


def test_dedup_preserves_distinct_opinions():
    docs = [
        _case("case-law-405-wis-2d-616", "https://cl/opinion/1/a/"),
        _case("case-law-379-wis-2d-141", "https://cl/opinion/2/b/"),
    ]
    kept = dedup_case_law_docs(docs)
    assert {d["doc_id"] for d in kept} == {"case-law-405-wis-2d-616", "case-law-379-wis-2d-141"}


def test_dedup_passes_through_non_case_law_and_urlless():
    docs = [
        {"doc_id": "statutes-70", "doc_type": "statute", "source_url": "https://x"},
        _case("case-law-no-url", ""),
        _case("case-law-405-wis-2d-616", "https://cl/opinion/1/a/"),
        _case("case-law-985-n-w-2d-69", "https://cl/opinion/1/a/"),
    ]
    kept = {d["doc_id"] for d in dedup_case_law_docs(docs)}
    # non-case-law and url-less docs always survive; the parallel pair collapses.
    assert "statutes-70" in kept
    assert "case-law-no-url" in kept
    assert "case-law-405-wis-2d-616" in kept
    assert "case-law-985-n-w-2d-69" not in kept


def test_extract_title_falls_back_to_url_slug():
    """A case with no metadata case_name still gets a named title from the URL."""
    from tools.ingestion.extract import _case_name_from_url

    # This is the exact fallback extract.py applies when case_name is empty.
    url = "https://www.courtlistener.com/opinion/1571037/abbott-v-marker/"
    name = _case_name_from_url(url)
    citation = "295 Wis. 2d 636"
    title = f"{name}, {citation}" if name and name != citation else citation
    assert title == "Abbott v. Marker, 295 Wis. 2d 636"
