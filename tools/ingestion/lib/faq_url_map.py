"""Build and query a normalized-question -> source_url map from faqs.json.

At query time the agentic_retrieval Lambda only knows a FAQ's question text,
so the lookup key is the normalized question. Some live FAQ files drifted from
the manifest wording, so lookup falls back to an exact-answer match and then a
50-character question-prefix match before giving up (no URL -> no link).
"""

from __future__ import annotations

import re

_PREFIX_LEN = 50


def normalize_question(text: str) -> str:
    """Canonical FAQ question key. Must match the seed + lambda normalization."""
    if not text:
        return ""
    cleaned = text.replace("​", "").replace("\xa0", " ").replace("﻿", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned.rstrip("?.").strip()


def build_url_map(records: list[dict]) -> dict:
    """Return indexes for exact-question, exact-answer, and question-prefix lookup.

    `records` are faqs.json entries: {"Q", "A", "source_url"}. On duplicate
    keys, last write wins (only ~4 of ~633 questions map to >1 URL).
    """
    by_question: dict[str, str] = {}
    by_answer: dict[str, str] = {}
    by_prefix: dict[str, str] = {}
    for r in records:
        url = r.get("source_url")
        if not url:
            continue
        nq = normalize_question(r.get("Q", ""))
        na = normalize_question(r.get("A", ""))
        if nq:
            by_question[nq] = url
            by_prefix.setdefault(nq[:_PREFIX_LEN], url)
        if na:
            by_answer[na] = url
    return {"by_question": by_question, "by_answer": by_answer, "by_prefix": by_prefix}


def lookup_url(question: str, answer: str, url_map: dict) -> str | None:
    """Resolve a FAQ to its source URL, or None if unrecoverable."""
    nq = normalize_question(question)
    if nq in url_map["by_question"]:
        return url_map["by_question"][nq]
    na = normalize_question(answer)
    if na and na in url_map["by_answer"]:
        return url_map["by_answer"][na]
    if nq and nq[:_PREFIX_LEN] in url_map["by_prefix"]:
        return url_map["by_prefix"][nq[:_PREFIX_LEN]]
    return None
