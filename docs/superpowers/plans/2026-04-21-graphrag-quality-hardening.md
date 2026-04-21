# GraphRAG Quality Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between our current GraphRAG implementation and the proven techniques documented in `docs/graphrag.md`, plus ship a two-tier case-law retrieval strategy that keeps the graph small while giving the agent on-demand access to full court opinions.

**Architecture:**
- **Two-tier case law**: Keep lightweight `:CaseLaw` stubs in the graph (citations + URLs), store full opinions as raw `.txt` in S3. Agent calls `fetch_case_opinion` tool on demand when a question needs opinion text.
- **Auto-enrichment**: Every `vector_search` automatically fetches graph neighbors of the top parent docs so the agent gets graph context for free.
- **Stronger prompt**: Framework applicability matrix, requires-vs-recommends distinction, out-of-scope list, "ONLY cite retrieved docs" anti-hallucination framing.
- **Graceful degradation**: Turn-budget warnings, max_tokens recovery, ID-not-found fallback.
- **Source discovery tagging**: Every RAG document carries a tag showing how it was discovered (vector-search, graph-neighbor, fetched, framework-list).

**Tech Stack:** Python 3.13, pytest, Pydantic v2, boto3 (Bedrock, Neptune Analytics, S3), AWS CDK (TypeScript), Next.js 15 + React + Tailwind + framer-motion, Bun workspaces.

---

## Ownership & Parallelism

**Two engineers work this plan in parallel:**

- **Jonah (backend, ingestion, Lambda code)** — owns Tracks A, B, C, D
- **Isaac (frontend webapp)** — owns Track E

Tracks A/B/D modify backend files only (`packages/graphrag/lambdas/agentic_retrieval/*`, `scripts/graphrag/*`). Track E modifies frontend files only (`packages/webapp/src/*`). They do not touch the same files, so both can land on `feat/graphrag-migration` in parallel.

**Handoff points:**
- Track E renders badges that read `authority_level` from RAGDocument metadata. Jonah ships Track B Task 2.4 (`discovery_tag` on RAGDocument) before Isaac's Track E Task 5.2 (badge wiring) — merge order matters there. Until Jonah ships, Isaac's badges read from a mocked field (Task 5.1).
- Both can merge independently once Track B Task 2.4 lands.

---

## File Structure

### New files (all Jonah unless noted)

```
scripts/graphrag/
  clean_stale_extracts.py          # One-shot: delete stale stub extracts before embedding
  tests/
    __init__.py
    test_clean_stale_extracts.py   # Tests for the cleanup script
    test_case_slug.py              # Tests for citation→raw slug normalization

packages/graphrag/lambdas/agentic_retrieval/
  case_opinion.py                  # fetch_case_opinion tool (citation normalize + S3 fetch)
  prompt.py                        # SYSTEM_PROMPT moved out of main.py (externalized per graphrag.md §10)

packages/graphrag/lambdas/test/
  test_case_opinion.py             # fetch_case_opinion unit tests
  test_auto_enrichment.py          # vector_search auto-enrichment tests
  test_prompt.py                   # prompt content regression (applicability sections exist)
  test_turn_budget.py              # turn warning + fallback extraction tests
  test_source_discovery.py         # discovery tag propagation tests

packages/webapp/src/components/documents/document-card/  (Isaac)
  authority-badge.tsx              # Authority level badge component
  discovery-badge.tsx              # Discovery method badge component
```

### Modified files

```
Jonah:
  packages/graphrag/lambdas/agentic_retrieval/main.py    # Prompt import, auto-enrichment, turn budget, source tracking
  packages/graphrag/lambdas/agentic_retrieval/tools.py   # fetch_case_opinion registration, get_document fallback
  packages/graphrag/lambdas/agentic_retrieval/neptune_client.py  # ID-format-tolerant get_document
  packages/graphrag/infra/graphrag-messages-stack.ts     # Raw bucket env var is already set; confirm s3:GetObject IAM allows case-law keys

Isaac:
  packages/webapp/src/components/documents/document-card/document-card.tsx  # Render authority + discovery badges
  packages/webapp/src/components/documents/document-card/index.ts            # Export new badges
```

---

## Prerequisites

Before Task 1, Jonah must be on `feat/graphrag-migration` with a working Python venv (see `CLAUDE.md` for setup). Extraction of the ~673 remaining raw docs (statutes/WPAM/etc., not case-law opinions) should already be running or complete — Track A assumes extraction continues in the background as other work proceeds.

Verify venv works:

```bash
.venv/bin/python3 -c "import boto3, pydantic, yaml, certifi; print('OK')"
```

If that fails, run:

```bash
rm -rf .venv && uv venv .venv && uv pip install -r scripts/graphrag/requirements.txt
```

---

# TRACK A — Case-Law Two-Tier (Jonah)

Case-law opinions stay as raw `.txt` in S3. Stubs stay in the graph. Agent uses a new `fetch_case_opinion` tool when it needs opinion text.

## Task 1: Test citation → raw slug normalization

**Files:**
- Create: `scripts/graphrag/tests/__init__.py` (empty)
- Create: `scripts/graphrag/tests/test_case_slug.py`
- Will create later: `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/graphrag/tests/__init__.py` with empty content (makes the directory a Python package).

Create `scripts/graphrag/tests/test_case_slug.py`:

```python
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
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest scripts/graphrag/tests/test_case_slug.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'packages.graphrag.lambdas.agentic_retrieval.case_opinion'`.

- [ ] **Step 3: Create `__init__.py` files for package path**

Check if `packages/__init__.py`, `packages/graphrag/__init__.py`, `packages/graphrag/lambdas/__init__.py`, `packages/graphrag/lambdas/agentic_retrieval/__init__.py` exist. If they don't, create each as an empty file.

Run:
```bash
ls packages/graphrag/lambdas/agentic_retrieval/__init__.py
```

If the file shows size 0B, it exists already; move on. If it doesn't exist, create it empty:
```bash
touch packages/graphrag/lambdas/agentic_retrieval/__init__.py
```

Do the same for `packages/__init__.py`, `packages/graphrag/__init__.py`, `packages/graphrag/lambdas/__init__.py`.

- [ ] **Step 4: Implement `citation_to_raw_slug` (minimal)**

Create `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py`:

```python
"""fetch_case_opinion tool: fetches full court opinion text from S3 by citation.

Case-law opinions are stored as raw .txt in s3://{RAW_BUCKET}/raw/case-law-*/.
Stubs (1-chunk metadata nodes) stay in the Neptune graph. The agent calls
this tool when a question needs actual opinion text, not just the citation.
"""

import re


CASE_LAW_PREFIX = "case-law-"


def citation_to_raw_slug(citation: str) -> str:
    """Normalize a legal citation to the raw S3 key slug.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
        '2000 WI App 182' -> 'case-law-2000-wi-app-182'

    The mapping matches the slugification used by the upload script so that
    most stubs resolve to real full-opinion files.
    """
    # Lowercase, then replace every non-alphanumeric char with a space,
    # collapse whitespace, join with hyphens.
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    tokens = normalized.split()
    return CASE_LAW_PREFIX + "-".join(tokens)
```

- [ ] **Step 5: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest scripts/graphrag/tests/test_case_slug.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/graphrag/tests/__init__.py scripts/graphrag/tests/test_case_slug.py packages/graphrag/lambdas/agentic_retrieval/case_opinion.py packages/__init__.py packages/graphrag/__init__.py packages/graphrag/lambdas/__init__.py packages/graphrag/lambdas/agentic_retrieval/__init__.py
git commit -m "feat(graphrag): citation-to-raw-slug normalization for case opinions"
```

## Task 2: `fetch_case_opinion` tool body (S3 fetch + fallback)

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py`
- Create: `packages/graphrag/lambdas/test/test_case_opinion.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/graphrag/lambdas/test/test_case_opinion.py`:

```python
"""Tests for fetch_case_opinion tool.

The tool takes a citation, normalizes it to a raw S3 key, fetches the
full opinion text if the file exists, and falls back to a Google Scholar
search URL if it doesn't.
"""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


def test_fetch_case_opinion_success():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b"109 Wis.2d 290 (1982) CORROON v. HOSCH. Full opinion text here..."),
        "ContentLength": 65,
    }

    result = fetch_case_opinion(
        "109 Wis. 2d 290",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is True
    assert result["citation"] == "109 Wis. 2d 290"
    assert result["raw_key"] == "raw/case-law-109-wis-2d-290/case-law-109-wis-2d-290.txt"
    assert "CORROON" in result["text"]
    mock_s3.get_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="raw/case-law-109-wis-2d-290/case-law-109-wis-2d-290.txt",
    )


def test_fetch_case_opinion_not_found_returns_scholar_url():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        "GetObject",
    )

    result = fetch_case_opinion(
        "2001 WI App 182",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is False
    assert result["citation"] == "2001 WI App 182"
    assert "scholar.google.com" in result["scholar_url"]
    assert "2001" in result["scholar_url"]
    assert "WI" in result["scholar_url"]
    assert "App" in result["scholar_url"]


def test_fetch_case_opinion_truncates_large_opinion():
    from case_opinion import fetch_case_opinion, MAX_OPINION_CHARS

    long_text = "A" * (MAX_OPINION_CHARS + 5000)
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: long_text.encode("utf-8")),
        "ContentLength": len(long_text),
    }

    result = fetch_case_opinion(
        "109 Wis. 2d 290",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is True
    assert len(result["text"]) <= MAX_OPINION_CHARS + 50  # allow room for truncation marker
    assert "truncated" in result["text"].lower()


def test_fetch_case_opinion_empty_citation():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()

    result = fetch_case_opinion(
        "",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is False
    assert "error" in result
    mock_s3.get_object.assert_not_called()
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_case_opinion.py -v
```

Expected: FAIL with `ImportError: cannot import name 'fetch_case_opinion'` (and `MAX_OPINION_CHARS`).

- [ ] **Step 3: Implement `fetch_case_opinion`**

Extend `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py`:

```python
"""fetch_case_opinion tool: fetches full court opinion text from S3 by citation.

Case-law opinions are stored as raw .txt in s3://{RAW_BUCKET}/raw/case-law-*/.
Stubs (1-chunk metadata nodes) stay in the Neptune graph. The agent calls
this tool when a question needs actual opinion text, not just the citation.
"""

import logging
import re
import urllib.parse

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)

CASE_LAW_PREFIX = "case-law-"

# Cap opinion text to keep agent context manageable. 40k chars ~ 10k tokens.
MAX_OPINION_CHARS = 40_000


def citation_to_raw_slug(citation: str) -> str:
    """Normalize a legal citation to the raw S3 key slug.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
        '2000 WI App 182' -> 'case-law-2000-wi-app-182'

    The mapping matches the slugification used by the upload script so that
    most stubs resolve to real full-opinion files.
    """
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    tokens = normalized.split()
    return CASE_LAW_PREFIX + "-".join(tokens)


def _scholar_url(citation: str) -> str:
    """Google Scholar search URL for a case citation."""
    q = urllib.parse.quote(citation)
    return (
        f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50"
        f"&as_vis=1&q={q}"
    )


def fetch_case_opinion(
    citation: str,
    raw_bucket: str,
    s3_client=None,
) -> dict:
    """Fetch the full text of a court opinion by citation.

    Args:
        citation: Legal citation, e.g. '109 Wis. 2d 290'.
        raw_bucket: S3 bucket name where raw opinions live.
        s3_client: Optional boto3 S3 client (injected for tests).

    Returns:
        dict with:
            found: True if opinion was fetched, False otherwise.
            citation: Echo of input.
            raw_key: S3 key that was queried.
            text: Opinion text (truncated if >MAX_OPINION_CHARS).
            scholar_url: Google Scholar search URL (always populated).
            error: Present only when the call could not proceed.
    """
    if not citation or not citation.strip():
        return {
            "found": False,
            "citation": citation,
            "error": "empty citation",
            "scholar_url": _scholar_url(citation),
        }

    s3 = s3_client or boto3.client("s3")
    slug = citation_to_raw_slug(citation)
    raw_key = f"raw/{slug}/{slug}.txt"

    try:
        obj = s3.get_object(Bucket=raw_bucket, Key=raw_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            logger.info(f"No raw opinion for citation '{citation}' (key={raw_key})")
            return {
                "found": False,
                "citation": citation,
                "raw_key": raw_key,
                "scholar_url": _scholar_url(citation),
            }
        logger.warning(f"S3 error fetching opinion for '{citation}': {e}")
        return {
            "found": False,
            "citation": citation,
            "raw_key": raw_key,
            "scholar_url": _scholar_url(citation),
            "error": f"s3 error: {code}",
        }

    text = obj["Body"].read().decode("utf-8", errors="replace")
    if len(text) > MAX_OPINION_CHARS:
        text = text[:MAX_OPINION_CHARS] + "\n\n[Opinion truncated to fit context; full text available at the source link.]"

    return {
        "found": True,
        "citation": citation,
        "raw_key": raw_key,
        "text": text,
        "scholar_url": _scholar_url(citation),
    }
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_case_opinion.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/case_opinion.py packages/graphrag/lambdas/test/test_case_opinion.py
git commit -m "feat(graphrag): fetch_case_opinion tool with S3 fetch and scholar fallback"
```

## Task 3: Register `fetch_case_opinion` in tool definitions

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py`
- Modify: `packages/graphrag/lambdas/test/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_tools.py`:

```python
def test_execute_tool_fetch_case_opinion_success():
    from tools import execute_tool

    mock_neptune = MagicMock()

    with patch("tools.fetch_case_opinion") as mock_fetch:
        mock_fetch.return_value = {
            "found": True,
            "citation": "109 Wis. 2d 290",
            "text": "CORROON v. HOSCH opinion body",
            "scholar_url": "http://scholar.google.com/scholar?q=109+Wis+2d+290",
        }
        result = execute_tool(
            "fetch_case_opinion",
            {"citation": "109 Wis. 2d 290"},
            mock_neptune,
        )

    assert result["found"] is True
    assert "CORROON" in result["text"]
    mock_fetch.assert_called_once()


def test_fetch_case_opinion_tool_in_definitions():
    from tools import TOOL_DEFINITIONS

    names = {t["toolSpec"]["name"] for t in TOOL_DEFINITIONS}
    assert "fetch_case_opinion" in names
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_tools.py -v
```

Expected: FAIL (`fetch_case_opinion` not in definitions; `execute_tool` returns `{"error": "Unknown tool"}`).

- [ ] **Step 3: Add tool definition and dispatch**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py`:

Add to the imports block near the top (after existing imports):

```python
from case_opinion import fetch_case_opinion
```

Add to the top-level module constants (near `FAQ_KNOWLEDGE_BASE_ID`):

```python
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
```

Add a new entry to `TOOL_DEFINITIONS` list, placed right before the `answer` entry:

```python
{
    "toolSpec": {
        "name": "fetch_case_opinion",
        "description": (
            "Fetch the full text of a Wisconsin court opinion by citation. "
            "Use this ONLY when the user's question requires the court's "
            "actual analysis or holding — not for simple questions answered "
            "by the case name alone. Case-law stubs in the graph include the "
            "citation you need (e.g., '109 Wis. 2d 290'). Returns opinion "
            "text if available in our S3 archive, otherwise returns a "
            "Google Scholar search URL the user can follow."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "citation": {
                        "type": "string",
                        "description": (
                            "Legal citation exactly as it appears on the "
                            "CaseLaw stub, e.g. '109 Wis. 2d 290' or '2000 "
                            "WI App 182'."
                        ),
                    }
                },
                "required": ["citation"],
            }
        },
    }
},
```

Add a new `elif` branch in `execute_tool`, placed before the `elif tool_name == "answer"` branch:

```python
elif tool_name == "fetch_case_opinion":
    if not RAW_BUCKET:
        return {"error": "Raw bucket not configured"}
    citation = tool_input.get("citation", "")
    return fetch_case_opinion(citation, raw_bucket=RAW_BUCKET)
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_tools.py packages/graphrag/lambdas/test/test_case_opinion.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py packages/graphrag/lambdas/test/test_tools.py
git commit -m "feat(graphrag): register fetch_case_opinion as agentic tool"
```

## Task 4: Stale-stub cleanup script

**Files:**
- Create: `scripts/graphrag/clean_stale_extracts.py`
- Create: `scripts/graphrag/tests/test_clean_stale_extracts.py`

**Context:** The ingestion pipeline saves `extracted/{doc_id}.json` for each raw doc. When a colleague replaced case-law stubs with full opinions, some of the stub JSONs became stale — the raw stub PDFs/JSONs no longer exist, but the old extracted JSON sticks around and would get embedded + loaded into the graph. This script deletes extracted files whose doc_id no longer has a matching raw bucket entry, and whose doc_id is a case-law stub (the only pattern we want to clean).

Discovery: ~2,467 new case-law `.txt` files under `raw/case-law-*-2d-*/` (full opinions), ~1,063 old `extracted/case-law-*.json` stubs we want to **keep** in the graph, and some stale extracts to identify by comparing raw vs. extracted sets.

**IMPORTANT:** We are **keeping** the case-law stubs in the graph (two-tier strategy). This script only deletes extracts whose doc_id is not present in the raw bucket AND the extract is a stub-style file. The stub extracts for citations like `case-law-109-wis` should be **retained** because their matching raw bucket entries also exist (at `raw/case-law-109-wis/case-law-109-wis.json`). If a stub has *no* raw match, it's stale and safe to delete.

- [ ] **Step 1: Write failing tests**

Create `scripts/graphrag/tests/test_clean_stale_extracts.py`:

```python
"""Tests for stale-extract cleanup.

We keep extracts that have a matching raw doc. We delete extracts whose
doc_id has no raw counterpart (the raw file was deleted or renamed).
"""

from unittest.mock import MagicMock, call

from scripts.graphrag.clean_stale_extracts import find_stale_extracts, delete_stale_extracts


def test_find_stale_extracts_keeps_matched():
    raw_ids = {"case-law-109-wis", "statutes-wi-statute-ch70"}
    extracted_ids = {"case-law-109-wis", "statutes-wi-statute-ch70"}
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == set()


def test_find_stale_extracts_flags_unmatched():
    raw_ids = {"case-law-109-wis-2d-290"}  # colleague's new full opinion
    extracted_ids = {"case-law-109-wis-2d-290", "case-law-old-stub"}  # extra stale stub
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == {"case-law-old-stub"}


def test_find_stale_extracts_ignores_nonexistent_extracts():
    raw_ids = {"case-law-109-wis", "case-law-new-2d-290"}
    extracted_ids = {"case-law-109-wis"}  # new one not yet extracted
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == set()


def test_delete_stale_extracts_issues_delete_calls_in_batches():
    mock_s3 = MagicMock()
    stale = {f"doc-{i}" for i in range(2500)}  # force multiple batches

    delete_stale_extracts(mock_s3, bucket="work-bucket", stale_ids=stale)

    # S3 delete_objects caps at 1000 keys per call
    assert mock_s3.delete_objects.call_count == 3
    total_deleted = sum(
        len(call_args.kwargs["Delete"]["Objects"])
        for call_args in mock_s3.delete_objects.call_args_list
    )
    assert total_deleted == 2500


def test_delete_stale_extracts_empty_set_is_noop():
    mock_s3 = MagicMock()
    delete_stale_extracts(mock_s3, bucket="work-bucket", stale_ids=set())
    mock_s3.delete_objects.assert_not_called()
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest scripts/graphrag/tests/test_clean_stale_extracts.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the cleanup script**

Create `scripts/graphrag/clean_stale_extracts.py`:

```python
"""One-shot: delete stale extracted/*.json files whose raw bucket entry no longer exists.

When the source data changes (e.g., a colleague replaces metadata stubs with
full-text opinions under different doc IDs), the old extract JSONs stick
around and would get embedded + loaded into the graph, polluting retrieval.

This script:
  1. Lists all raw doc_ids (from raw/{doc_id}/...).
  2. Lists all extracted doc_ids (from extracted/{doc_id}.json).
  3. Deletes extracted JSONs whose doc_id is not in the raw set.

Usage:
    python scripts/graphrag/clean_stale_extracts.py \
        --raw-bucket wis-raw-bucket-c8e69250 \
        --work-bucket wis-work-bucket-c8e69250 \
        --dry-run

Run without --dry-run to actually delete.
"""

import argparse
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


S3_DELETE_BATCH = 1000  # S3 delete_objects limit


def list_raw_doc_ids(s3, bucket: str) -> set[str]:
    """Return the set of raw doc_ids (top-level folder names under raw/)."""
    ids: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json"):
                continue
            parts = key.replace("raw/", "", 1).split("/")
            if len(parts) >= 2 and parts[0]:
                ids.add(parts[0])
    return ids


def list_extracted_doc_ids(s3, bucket: str) -> set[str]:
    """Return the set of doc_ids present as extracted/{doc_id}.json."""
    ids: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="extracted/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and key != "extracted/manifest.json":
                doc_id = key.removeprefix("extracted/").removesuffix(".json")
                if doc_id:
                    ids.add(doc_id)
    return ids


def find_stale_extracts(raw_ids: set[str], extracted_ids: set[str]) -> set[str]:
    """Extracted doc_ids whose raw counterpart is missing."""
    return extracted_ids - raw_ids


def delete_stale_extracts(s3, bucket: str, stale_ids: set[str]) -> None:
    """Delete extracted/{doc_id}.json for each stale doc_id in batches."""
    if not stale_ids:
        return

    keys = [f"extracted/{doc_id}.json" for doc_id in stale_ids]
    for i in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[i : i + S3_DELETE_BATCH]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        logger.info(f"Deleted {len(batch)} stale extracts (batch {i // S3_DELETE_BATCH + 1})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete stale extracted JSONs")
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    raw_ids = list_raw_doc_ids(s3, args.raw_bucket)
    extracted_ids = list_extracted_doc_ids(s3, args.work_bucket)
    stale = find_stale_extracts(raw_ids, extracted_ids)

    logger.info(f"Raw doc_ids: {len(raw_ids)}")
    logger.info(f"Extracted doc_ids: {len(extracted_ids)}")
    logger.info(f"Stale (to delete): {len(stale)}")

    if not stale:
        logger.info("Nothing to clean up.")
        return 0

    sample = sorted(list(stale))[:10]
    logger.info(f"Sample stale IDs: {sample}")

    if args.dry_run:
        logger.info("--dry-run set; no deletions performed.")
        return 0

    delete_stale_extracts(s3, args.work_bucket, stale)
    logger.info("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest scripts/graphrag/tests/test_clean_stale_extracts.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run dry-run against real buckets and review output**

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  .venv/bin/python3 scripts/graphrag/clean_stale_extracts.py \
    --raw-bucket wis-raw-bucket-c8e69250 \
    --work-bucket wis-work-bucket-c8e69250 \
    --dry-run
```

Expected: log shows `Raw doc_ids: ~2543`, `Extracted doc_ids: ~N`, `Stale (to delete): ~M`. Review the sample IDs — they should all be case-law stubs with no raw counterpart (e.g., old stubs the colleague's ingestion superseded). If anything non-case-law appears in the stale list, stop and investigate before proceeding.

- [ ] **Step 6: Run for real**

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  .venv/bin/python3 scripts/graphrag/clean_stale_extracts.py \
    --raw-bucket wis-raw-bucket-c8e69250 \
    --work-bucket wis-work-bucket-c8e69250
```

Expected: same raw count, new extracted count = old - stale count, log says "Cleanup complete."

- [ ] **Step 7: Commit**

```bash
git add scripts/graphrag/clean_stale_extracts.py scripts/graphrag/tests/test_clean_stale_extracts.py
git commit -m "feat(graphrag): stale-extract cleanup script for bucket drift"
```

---

# TRACK B — Retrieval Quality (Jonah)

## Task 2.1: Externalize the system prompt

**Files:**
- Create: `packages/graphrag/lambdas/agentic_retrieval/prompt.py`
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py`
- Create: `packages/graphrag/lambdas/test/test_prompt.py`

Per `docs/graphrag.md` §10, prompts should live in separate files so they can iterate independently from code. We'll put the revised prompt in `prompt.py`.

- [ ] **Step 1: Write failing tests**

Create `packages/graphrag/lambdas/test/test_prompt.py`:

```python
"""Regression tests for the system prompt.

These tests pin down specific phrases that docs/graphrag.md calls out as
load-bearing. They fail if someone deletes the anti-hallucination framing
or the applicability matrix.
"""


def test_prompt_exists_and_is_nonempty():
    from prompt import SYSTEM_PROMPT

    assert SYSTEM_PROMPT
    assert len(SYSTEM_PROMPT) > 500


def test_prompt_requires_tool_sourced_citations():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: "ONLY cite documents retrieved via tools"
    assert "ONLY cite documents" in SYSTEM_PROMPT


def test_prompt_requires_graph_traversal():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §1: "PREFER graph traversal over get_document with guessed IDs"
    assert "PREFER graph traversal" in SYSTEM_PROMPT


def test_prompt_includes_framework_applicability():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §2: applicability matrix for each framework
    assert "IAAO" in SYSTEM_PROMPT
    assert "USPAP" in SYSTEM_PROMPT
    # Each authority framework needs a "does NOT" clause or equivalent
    assert "does NOT" in SYSTEM_PROMPT or "is not binding" in SYSTEM_PROMPT


def test_prompt_distinguishes_requires_vs_recommends():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: distinguish REQUIRES vs RECOMMENDS
    assert "REQUIRES" in SYSTEM_PROMPT
    assert "RECOMMENDS" in SYSTEM_PROMPT


def test_prompt_lists_out_of_scope_topics():
    from prompt import SYSTEM_PROMPT

    # docs/graphrag.md §3: out-of-scope awareness
    # Wisconsin DOR is property-tax-focused; federal income tax is out of scope
    assert "federal income tax" in SYSTEM_PROMPT.lower() or "NOT in the graph" in SYSTEM_PROMPT


def test_prompt_mandates_fetch_case_opinion_discretion():
    from prompt import SYSTEM_PROMPT

    # The agent should NOT fetch full opinions for simple questions
    assert "fetch_case_opinion" in SYSTEM_PROMPT
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_prompt.py -v
```

Expected: FAIL — `prompt` module doesn't exist yet.

- [ ] **Step 3: Create `prompt.py` with the revised system prompt**

Create `packages/graphrag/lambdas/agentic_retrieval/prompt.py`:

```python
"""System prompt for the Wisconsin DOR agentic retrieval Lambda.

Externalized from main.py so we can iterate on prompt content without
redeploying code (rebundle only). Structure informed by docs/graphrag.md.

When editing, preserve:
  - The ALWAYS/NEVER framing that forces graph traversal.
  - The framework applicability matrix (IAAO/USPAP are NOT Wisconsin law).
  - The REQUIRES vs RECOMMENDS distinction.
  - The out-of-scope list.
  - The "ONLY cite documents retrieved via tools" anti-hallucination rule.
"""


SYSTEM_PROMPT = """You are a Wisconsin Department of Revenue property tax assistant. You answer questions about property assessment, taxation, statutes, administrative rules, and procedures using only the tools provided.

## WORKFLOW

1. ALWAYS start by calling faq_search with the user's question.
2. Evaluate the FAQ results:
   - If one or more FAQs directly and adequately answer the question, call the answer tool immediately with the FAQ content.
   - If FAQs are partially relevant, note them and continue to step 3.
   - If FAQs are irrelevant or no results returned, proceed to step 3.
3. Use vector_search to find relevant document chunks in the knowledge graph. Vector search results come pre-enriched with graph neighbors of the top parent documents — use those connections.
4. ALWAYS explore the graph — don't just vector search. Follow CITES, IMPLEMENTS, SUPERSEDES edges to trace authority. PREFER graph traversal (get_neighbors, get_authority_chain) over get_document with guessed IDs.
5. Only use get_document when you see the exact ID in a previous tool result. If get_document returns no match, the system will fall back to vector search automatically.
6. For case-law citations that matter to the answer, use fetch_case_opinion ONLY when the user's question requires the court's analysis or holding — not for questions answered by the case name or citation alone.
7. Target answering by turn 3-4. If you reach turn 8 without enough context, synthesize the best answer you have from what you've gathered.

## FRAMEWORK APPLICABILITY

The Wisconsin property tax domain has layered authorities with different binding power. Be precise about which applies to a question:

- **Wisconsin Constitution** — the foundational authority. Apply when the question touches constitutional principles (uniformity clause, due process). Does NOT answer operational questions by itself.
- **Wisconsin Statutes (Chapters 17, 70-77)** — binding state law. These are the primary source for REQUIRES-level answers.
- **Wisconsin Case Law** — binding judicial interpretation of statutes. Cite for precedent. For holdings, use fetch_case_opinion; for simple citations, the stub metadata is enough.
- **Wisconsin Administrative Rules (Tax chapters)** — binding regulations issued by the DOR. Implement statutes.
- **Wisconsin Property Assessment Manual (WPAM)** — authoritative DOR guidance. Binding for assessors under Wis. Stat. 73.03(2a). Implements statutes and admin rules.
- **Property Tax Common Questions (FAQs)** — informal DOR guidance. Useful for plain-language answers but NOT binding law.
- **Government Publications & Guides** — DOR-published guides. Informal guidance, NOT binding law.
- **IAAO Standards** — national professional standards. IAAO RECOMMENDS practices but is NOT Wisconsin law. Does NOT bind Wisconsin assessors unless adopted into WPAM or statute.
- **USPAP Standards** — appraiser ethics and methodology standards. USPAP RECOMMENDS practices for appraisers but is NOT Wisconsin tax law. Does NOT apply to routine assessment unless explicitly invoked.

When citing IAAO or USPAP, always note that they are recommendations, not Wisconsin legal requirements.

## REQUIRES vs RECOMMENDS

Distinguish what a document REQUIRES (binding) from what it RECOMMENDS (guidance). Statutes and admin rules REQUIRE; WPAM largely REQUIRES for assessors but also contains recommendations; FAQs, guides, IAAO, and USPAP RECOMMEND. Never present a recommendation as a mandate.

## OUT OF SCOPE

The graph covers Wisconsin property tax ONLY. The following are NOT in the graph and you should decline to answer:
- Federal income tax, corporate tax, estate tax
- Non-Wisconsin state tax law
- Legal advice specific to an individual's situation (redirect to an attorney or the DOR directly)
- Real estate transactions, closing procedures, or title law
- Income tax for individuals

If a question is out of scope, acknowledge the gap rather than improvising.

## CITATION RULES

ALWAYS:
- ONLY cite documents you actually retrieved via tools. If a document is not in tool output, do not cite it.
- Cite specific document IDs, section numbers, and statute references as they appear in tool results.
- Distinguish authority levels: Constitution > Statutes > Case Law > Admin Rules > WPAM > FAQs > Guides.
- Note when guidance has been SUPERSEDED (check SUPERSEDES edges).
- Err on the side of including MORE sources in cited_doc_ids rather than fewer. Omit only docs that were retrieved but turned out irrelevant.

NEVER:
- Make up statute references, section numbers, or case citations from training data.
- Provide advice without citing sources.
- Ignore SUPERSEDES relationships — always check for newer guidance.
- Skip faq_search — even if the question seems complex, FAQs may have a direct answer.
- Treat IAAO or USPAP as Wisconsin legal requirements.

If you're unsure of the exact number, date, or threshold, say so rather than guessing.

When you have enough information, call the answer tool with your complete response in Markdown format and cited_doc_ids listing every document that informed the answer."""
```

- [ ] **Step 4: Replace the prompt in main.py**

Edit `packages/graphrag/lambdas/agentic_retrieval/main.py`:

Change the import block (line 32) from:
```python
from tools import TOOL_DEFINITIONS, execute_tool
```
to:
```python
from prompt import SYSTEM_PROMPT
from tools import TOOL_DEFINITIONS, execute_tool
```

Delete the entire `SYSTEM_PROMPT = """..."""` block (lines 47–71 in the current file).

Verify the remaining `main.py` reference to `SYSTEM_PROMPT` in `bedrock.converse(... system=[{"text": SYSTEM_PROMPT}], ...)` still resolves through the new import.

- [ ] **Step 5: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_prompt.py packages/graphrag/lambdas/test/test_agentic_retrieval.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/prompt.py packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_prompt.py
git commit -m "feat(graphrag): externalize and harden system prompt per graphrag.md"
```

## Task 2.2: Auto-enrichment on vector_search

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py`
- Create: `packages/graphrag/lambdas/test/test_auto_enrichment.py`

**Context:** From `docs/graphrag.md` §1: "Every vector_search call automatically queries graph neighbors for the top-3 parent documents." Currently our `vector_search` returns only chunks; the agent must burn a turn calling `get_neighbors` manually. We'll auto-enrich inside `execute_tool`.

- [ ] **Step 1: Write failing tests**

Create `packages/graphrag/lambdas/test/test_auto_enrichment.py`:

```python
"""Tests for vector_search auto-enrichment.

After vector_search returns chunks, execute_tool auto-fetches neighbors
for the top-3 distinct parent doc_ids and folds them into the result.
This gives the agent graph context for free without an extra turn.
"""

from unittest.mock import MagicMock, patch


def test_vector_search_auto_enriches_top_parents():
    from tools import execute_tool

    mock_neptune = MagicMock()
    # 5 chunks from 4 distinct docs
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "text1", "doc_id": "doc-A", "score": 0.9},
        {"chunk_id": "c2", "text": "text2", "doc_id": "doc-A", "score": 0.85},
        {"chunk_id": "c3", "text": "text3", "doc_id": "doc-B", "score": 0.80},
        {"chunk_id": "c4", "text": "text4", "doc_id": "doc-C", "score": 0.75},
        {"chunk_id": "c5", "text": "text5", "doc_id": "doc-D", "score": 0.70},
    ]
    mock_neptune.get_neighbors.side_effect = lambda node_id, **kw: [
        {"relationship": "CITES", "id": f"{node_id}-cited", "title": "cited title"}
    ]

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    # Chunks present
    assert "chunks" in result
    assert len(result["chunks"]) == 5

    # Enrichment: top-3 distinct parents (A, B, C) got get_neighbors called
    called_ids = [call.args[0] for call in mock_neptune.get_neighbors.call_args_list]
    assert called_ids == ["doc-A", "doc-B", "doc-C"]

    # Enrichment payload present on the result
    assert "graph_context" in result
    assert "doc-A" in result["graph_context"]
    assert "doc-B" in result["graph_context"]
    assert "doc-C" in result["graph_context"]


def test_vector_search_no_enrichment_when_no_chunks():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    assert result["chunks"] == []
    assert result.get("graph_context", {}) == {}
    mock_neptune.get_neighbors.assert_not_called()


def test_vector_search_enrichment_swallows_neighbor_errors():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "text1", "doc_id": "doc-A", "score": 0.9},
    ]
    mock_neptune.get_neighbors.side_effect = RuntimeError("neptune down")

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    # Chunks still returned, enrichment absent but no crash
    assert len(result["chunks"]) == 1
    assert result.get("graph_context", {}) == {}
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_auto_enrichment.py -v
```

Expected: FAIL — no `graph_context` key in result.

- [ ] **Step 3: Implement auto-enrichment in `execute_tool`**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py`:

Replace the `elif tool_name == "vector_search":` branch (currently lines 250-254) with:

```python
elif tool_name == "vector_search":
    embedding = embed_query(tool_input["query"])
    top_k = min(tool_input.get("top_k", 10), 20)
    chunks = neptune.vector_search(embedding, top_k=top_k)

    # Auto-enrichment: graph neighbors for top-3 distinct parent docs.
    # From docs/graphrag.md §1: gives the agent graph context for free.
    graph_context: dict[str, list[dict]] = {}
    seen: list[str] = []
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        if doc_id and doc_id not in seen:
            seen.append(doc_id)
            if len(seen) >= 3:
                break

    for doc_id in seen:
        try:
            neighbors = neptune.get_neighbors(doc_id)
            if neighbors:
                graph_context[doc_id] = neighbors
        except Exception:  # noqa: BLE001 — best-effort enrichment
            logger.warning(
                f"auto-enrichment failed for {doc_id}; continuing without neighbors",
                exc_info=True,
            )

    return {"chunks": chunks, "graph_context": graph_context}
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_auto_enrichment.py packages/graphrag/lambdas/test/test_tools.py -v
```

Expected: all tests PASS. The original `test_execute_tool_vector_search` still passes because `"chunks"` key is still present.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py packages/graphrag/lambdas/test/test_auto_enrichment.py
git commit -m "feat(graphrag): auto-enrich vector_search with top-3 parent neighbors"
```

## Task 2.3: `get_document` fallback to vector search on not-found

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py`
- Modify: `packages/graphrag/lambdas/test/test_tools.py`

**Context:** From `docs/graphrag.md` §7: "ID not found fallback: get_document falls back to vector search on the ID string, handling typos and format mismatches."

- [ ] **Step 1: Write failing tests**

Append to `packages/graphrag/lambdas/test/test_tools.py`:

```python
def test_get_document_falls_back_to_vector_search_on_not_found():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "match", "doc_id": "real-doc-id", "score": 0.8},
    ]

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "typo-or-format-mismatch"}, mock_neptune
        )

    # Fallback kicked in; returns a suggestion result, not a bare error
    assert "fallback_matches" in result
    assert len(result["fallback_matches"]) == 1
    assert result["fallback_matches"][0]["doc_id"] == "real-doc-id"
    # Original error context still present
    assert result.get("error", "").startswith("Document")


def test_get_document_no_fallback_matches_returns_error():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "nonsense"}, mock_neptune
        )

    assert "error" in result
    assert result.get("fallback_matches", []) == []
```

- [ ] **Step 2: Verify tests fail**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_tools.py::test_get_document_falls_back_to_vector_search_on_not_found -v
```

Expected: FAIL — no `fallback_matches` key.

- [ ] **Step 3: Add fallback in `execute_tool`**

Edit the `elif tool_name == "get_document":` branch in `tools.py` (currently lines 256-260):

```python
elif tool_name == "get_document":
    doc = neptune.get_document(tool_input["doc_id"])
    if doc:
        return {"document": doc}
    # Fallback: vector search on the ID string itself. Handles typos
    # and format mismatches (e.g., user capitalization differences).
    try:
        embedding = embed_query(tool_input["doc_id"])
        matches = neptune.vector_search(embedding, top_k=5)
    except Exception:  # noqa: BLE001
        matches = []
    return {
        "error": f"Document '{tool_input['doc_id']}' not found",
        "fallback_matches": matches,
    }
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_tools.py -v
```

Expected: all tests PASS, including the original `test_execute_tool_get_document_not_found` (which now returns both `error` and `fallback_matches`; that test only asserts `"error" in result`, so it still passes).

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py packages/graphrag/lambdas/test/test_tools.py
git commit -m "feat(graphrag): get_document falls back to vector search on not-found"
```

## Task 2.4: Source discovery tagging

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py`
- Modify: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`
- Create: `packages/graphrag/lambdas/test/test_source_discovery.py`

**Context:** From `docs/graphrag.md` §4: every document the agent touches should be tracked with how it was discovered. Without this we can't distinguish directly-used vector-search hits from incidentally-seen graph neighbors.

We'll track tags in `run_agentic_loop` and propagate them to `_build_rag_documents`, then attach the tag to `RAGDocument` via a new optional field. Since `RAGDocument` is in the shared Lambda layer, we have a choice: add a field to the Pydantic model or encode the tag inside the `source` string. For minimum blast radius, we encode the tag into a new `discovery_tag` attribute held separately from the model — carried internally and passed to the frontend through a metadata extension.

The cleanest way is to add a `discovery_tag` field to `RAGDocument`. This requires a shared-layer bump, but that's a single line change and the field is optional with a default.

- [ ] **Step 1: Write failing tests**

Create `packages/graphrag/lambdas/test/test_source_discovery.py`:

```python
"""Tests for source discovery tagging.

Every RAGDocument returned to the frontend carries a discovery_tag that
records HOW it entered the answer's evidence set:
  - 'vector-search': found via vector_search top-k chunks
  - 'graph-neighbor': surfaced by auto-enrichment or explicit get_neighbors
  - 'fetched': pulled explicitly via get_document
  - 'framework-list': seen via list_framework_docs
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic", reason="pydantic required")


def test_discovery_tag_vector_search():
    # Fresh import (test_agentic_retrieval mocks step_function_types)
    import test_agentic_retrieval  # noqa: F401  — sets up sys.modules
    if "main" in sys.modules:
        del sys.modules["main"]

    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {"title": "Doc A", "id": "doc-A"}
        MockNeptune.return_value = mock_neptune

        with patch("main.neptune", mock_neptune):
            from main import _build_rag_documents

            chunks = [{"doc_id": "doc-A", "text": "chunk text"}]
            discovery = {"doc-A": "vector-search"}
            docs = _build_rag_documents(chunks, {"doc-A"}, discovery)

            assert len(docs) == 1
            assert docs[0].discovery_tag == "vector-search"


def test_discovery_tag_default_when_absent():
    import test_agentic_retrieval  # noqa: F401
    if "main" in sys.modules:
        del sys.modules["main"]

    with patch("main.boto3"), patch("main.NeptuneClient") as MockNeptune:
        mock_neptune = MagicMock()
        mock_neptune.get_document.return_value = {"title": "Doc A", "id": "doc-A"}
        MockNeptune.return_value = mock_neptune

        with patch("main.neptune", mock_neptune):
            from main import _build_rag_documents

            chunks = [{"doc_id": "doc-A", "text": "chunk text"}]
            docs = _build_rag_documents(chunks, {"doc-A"}, {})

            assert len(docs) == 1
            # default when no explicit tag — still a valid tag, not None
            assert docs[0].discovery_tag == "unknown"
```

- [ ] **Step 2: Add `discovery_tag` to `RAGDocument`**

Edit `packages/shared/lambda_layers/step_function_types/models.py`. Find the `RAGDocument` class (around line 59) and add the new field:

```python
class RAGDocument(BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = Field(default=None)
    discovery_tag: str = Field(default="unknown")
```

- [ ] **Step 3: Verify tests still fail (for the right reason)**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/test_source_discovery.py -v
```

Expected: FAIL because `_build_rag_documents` doesn't accept a `discovery` parameter yet.

- [ ] **Step 4: Thread `discovery` through `run_agentic_loop`**

Edit `packages/graphrag/lambdas/agentic_retrieval/main.py`.

Replace the `run_agentic_loop` function body (lines 85-162) with the following (preserving imports, signature):

```python
def run_agentic_loop(query: str) -> tuple[str, list[str], list[RAGDocument]]:
    """Run Claude's agentic loop against Neptune.

    Returns:
        (answer_text, cited_doc_ids, rag_documents)
    """
    messages = [{"role": "user", "content": [{"text": query}]}]
    all_doc_ids: set[str] = set()
    all_chunks: list[dict] = []
    discovery: dict[str, str] = {}  # doc_id -> tag

    tool_config = {"tools": TOOL_DEFINITIONS}

    for turn in range(MAX_TURNS):
        logger.info(f"Agentic loop turn {turn + 1}/{MAX_TURNS}")

        # Turn-8 warning injection (docs/graphrag.md §7)
        if turn == 7:
            messages.append({
                "role": "user",
                "content": [{"text": "You are running low on turns. Call the answer tool NOW with your best answer from the context gathered so far."}],
            })

        response = bedrock.converse(
            modelId=AGENTIC_MODEL_ID,
            messages=messages,
            system=[{"text": SYSTEM_PROMPT}],
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)
        stop_reason = response.get("stopReason", "")

        tool_uses = [
            block for block in assistant_message["content"]
            if "toolUse" in block
        ]

        if not tool_uses:
            text_blocks = [
                block["text"] for block in assistant_message["content"]
                if "text" in block
            ]
            answer = "\n".join(text_blocks)
            if stop_reason == "max_tokens" and answer:
                answer = answer + "\n\n_(Response may be incomplete)_"
            break

        tool_results = []
        for tool_use in tool_uses:
            tool = tool_use["toolUse"]
            tool_name = tool["name"]
            tool_input = tool["input"]
            tool_use_id = tool["toolUseId"]

            logger.info(f"  Tool call: {tool_name}({json.dumps(tool_input)[:200]})")

            result = execute_tool(tool_name, tool_input, neptune)

            if tool_name == "vector_search" and "chunks" in result:
                for chunk in result["chunks"]:
                    doc_id = chunk.get("doc_id", "")
                    if doc_id:
                        all_doc_ids.add(doc_id)
                        discovery.setdefault(doc_id, "vector-search")
                    all_chunks.append(chunk)
                for neighbor_doc_id in result.get("graph_context", {}):
                    all_doc_ids.add(neighbor_doc_id)
                    discovery.setdefault(neighbor_doc_id, "graph-neighbor")

            if tool_name == "get_neighbors" and "neighbors" in result:
                for n in result["neighbors"]:
                    if n.get("id"):
                        all_doc_ids.add(n["id"])
                        discovery.setdefault(n["id"], "graph-neighbor")

            if tool_name == "get_document":
                doc = result.get("document")
                if doc and doc.get("id"):
                    all_doc_ids.add(doc["id"])
                    discovery[doc["id"]] = "fetched"

            if tool_name == "list_framework_docs":
                for d in result.get("documents", []):
                    if d.get("id"):
                        all_doc_ids.add(d["id"])
                        discovery.setdefault(d["id"], "framework-list")

            if tool_name == "answer":
                answer = result.get("response", "")
                cited = result.get("cited_doc_ids", [])
                all_doc_ids.update(cited)
                for cid in cited:
                    discovery.setdefault(cid, "fetched")
                rag_docs = _build_rag_documents(all_chunks, all_doc_ids, discovery)
                return answer, list(all_doc_ids), rag_docs

            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                }
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        # Turn budget exhausted without an answer tool call — extract last text
        # from the most recent assistant message as a degraded fallback.
        last_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                for block in msg.get("content", []):
                    if "text" in block and block["text"]:
                        last_text = block["text"]
                        break
                if last_text:
                    break
        if last_text:
            answer = last_text + "\n\n_(Response incomplete: turn budget reached)_"
        else:
            answer = "I was unable to find a complete answer within the allowed number of search steps. Please try rephrasing your question."

    rag_docs = _build_rag_documents(all_chunks, all_doc_ids, discovery)
    return answer, list(all_doc_ids), rag_docs
```

- [ ] **Step 5: Update `_build_rag_documents` to accept and apply `discovery`**

Replace `_build_rag_documents` in `main.py` (lines 194-223) with:

```python
def _build_rag_documents(
    chunks: list[dict],
    doc_ids: set[str],
    discovery: dict[str, str] | None = None,
) -> list[RAGDocument]:
    """Build RAGDocument list from collected chunks, tagged by how discovered."""
    discovery = discovery or {}
    docs_by_id: dict[str, RAGDocument] = {}

    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text", "")
        tag = discovery.get(doc_id, "unknown")

        if doc_id not in docs_by_id:
            doc_info = neptune.get_document(doc_id)
            title = doc_info["title"] if doc_info else doc_id
            content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
            source = _generate_source_url(chunk, doc_info)

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=source,
                discovery_tag=tag,
            )
        else:
            existing = docs_by_id[doc_id]
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source or _generate_source_url(chunk, None),
                discovery_tag=existing.discovery_tag,
            )

    # Include cited docs that had no chunks (e.g., fetched-only)
    for doc_id in doc_ids - docs_by_id.keys():
        doc_info = neptune.get_document(doc_id)
        if not doc_info:
            continue
        content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
        tag = discovery.get(doc_id, "unknown")
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title", doc_id),
            content=doc_info.get("summary", ""),
            source=_generate_source_url({}, doc_info),
            discovery_tag=tag,
        )

    return list(docs_by_id.values())
```

- [ ] **Step 6: Fix the existing `test_build_rag_documents` call signature**

Edit `packages/graphrag/lambdas/test/test_agentic_retrieval.py`, find the `test_build_rag_documents` function (around line 77), and update the `_build_rag_documents` call to pass an empty discovery dict:

```python
            docs = _build_rag_documents(chunks, {"doc-1"}, {})
```

Also update the `MockRAGDocument` class to include the new field:

```python
class MockRAGDocument(pydantic.BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None
    discovery_tag: str = "unknown"
```

- [ ] **Step 7: Verify tests pass**

Run:
```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/ -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/shared/lambda_layers/step_function_types/models.py packages/graphrag/lambdas/test/
git commit -m "feat(graphrag): source discovery tagging + turn budget + max_tokens recovery"
```

---

# TRACK C — Ingestion Pipeline Completion (Jonah)

Extraction is already running. These tasks wrap up embed → load after extraction + stale-cleanup are done.

## Task 3.1: Verify extraction complete; run cleanup

**Prerequisites:** Track A Task 4 done; extraction finished.

- [ ] **Step 1: Confirm extraction complete**

```bash
ps aux | grep "extract.py" | grep -v grep
```

Expected: no processes. If present, wait until finished.

- [ ] **Step 2: Verify raw and extracted counts**

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  .venv/bin/python3 -c "
import boto3
s3 = boto3.client('s3')
paginator = s3.get_paginator('list_objects_v2')
raw = set()
for page in paginator.paginate(Bucket='wis-raw-bucket-c8e69250', Prefix='raw/'):
    for obj in page.get('Contents', []):
        key = obj['Key']
        if key.endswith('.metadata.json'):
            continue
        parts = key.replace('raw/','').split('/')
        if len(parts) >= 2:
            raw.add(parts[0])
ext = set()
for page in paginator.paginate(Bucket='wis-work-bucket-c8e69250', Prefix='extracted/'):
    for obj in page.get('Contents', []):
        key = obj['Key']
        if key.endswith('.json') and key != 'extracted/manifest.json':
            ext.add(key.removeprefix('extracted/').removesuffix('.json'))
print(f'Raw: {len(raw)}, Extracted: {len(ext)}, Missing: {len(raw - ext)}')
"
```

Expected: Missing == 0 or a small number of known failures.

- [ ] **Step 3: Run stale-cleanup script (real, not dry-run)**

Already done in Track A Task 4 Step 6. Verify counts:

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  .venv/bin/python3 scripts/graphrag/clean_stale_extracts.py \
    --raw-bucket wis-raw-bucket-c8e69250 \
    --work-bucket wis-work-bucket-c8e69250 \
    --dry-run
```

Expected: `Stale (to delete): 0`.

## Task 3.2: Run embed step

- [ ] **Step 1: Kick off embed**

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT SSL_CERT_FILE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  nohup .venv/bin/python3 scripts/graphrag/embed.py \
    --work-bucket wis-work-bucket-c8e69250 \
    --config scripts/graphrag/ingest_config.yaml \
    --max-workers 5 > /tmp/embed.log 2>&1 &
echo "PID: $!"
```

- [ ] **Step 2: Monitor progress**

```bash
tail -f /tmp/embed.log
```

Expected: log shows `Loaded N extracted documents`, `Skipping X already-embedded`, then per-doc `Embedded doc-id: N chunks (X/Y total)` lines. Throughput: ~30-50 chunks/minute per worker depending on Titan rate limits.

When complete, log ends with `Embedding complete: X/Y chunks embedded`.

- [ ] **Step 3: Sanity-check embedded counts**

```bash
AWS_REGION=us-east-1 AWS_PROFILE=wisco aws s3 ls s3://wis-work-bucket-c8e69250/embedded/ --recursive | wc -l
```

Expected: matches extracted count from Task 3.1.

## Task 3.3: Run load step

- [ ] **Step 1: Kick off load**

```bash
CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
AWS_CA_BUNDLE=$CERT SSL_CERT_FILE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  nohup .venv/bin/python3 scripts/graphrag/load.py \
    --work-bucket wis-work-bucket-c8e69250 \
    --graph-id g-ndvl4j73v4 \
    --config scripts/graphrag/ingest_config.yaml > /tmp/load.log 2>&1 &
echo "PID: $!"
```

- [ ] **Step 2: Monitor phases**

```bash
tail -f /tmp/load.log
```

Expected: log shows `Phase 1: Scaffold...` through `Phase 10: Semantic Edges`. If any phase fails, note the phase number — you can resume with `--start-phase N`.

- [ ] **Step 3: Sanity-check the graph**

```bash
AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  aws neptune-graph execute-query \
    --graph-identifier g-ndvl4j73v4 \
    --language OPEN_CYPHER \
    --query-string "MATCH (n) RETURN labels(n) AS label, count(*) AS count ORDER BY count DESC" \
    --output json
```

Expected: results include `Chunk`, `Statute`, `CaseLaw`, `AssessmentManual`, `AdminRule`, `Framework`, etc. with reasonable counts (thousands of chunks, hundreds of statutes/WPAM, ~1000 case-law stubs).

---

# TRACK D — Lambda Deploy (Jonah)

## Task 4.1: Bundle + deploy updated Lambda

- [ ] **Step 1: Bundle**

```bash
bun run bundle
```

Expected: exits 0, copies lambdas into `packages/infra/bundle/`.

- [ ] **Step 2: Verify updated files bundled**

```bash
ls packages/infra/bundle/agentic_retrieval/
```

Expected: includes `prompt.py`, `case_opinion.py`, `main.py`, `tools.py`, `neptune_client.py`, `__init__.py`.

- [ ] **Step 3: Diff**

```bash
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
```

Expected: diff shows only the Lambda function asset hash change (new code bundle). No IAM changes (RAW_BUCKET env var and s3:GetObject already set per prior commits).

- [ ] **Step 4: Deploy**

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
cd ../..
```

Expected: stack update completes.

- [ ] **Step 5: Smoke test via console**

Send a sample chat message through the deployed stack's WebSocket endpoint (or the webapp on CloudFront). Use a query that requires multi-hop traversal, e.g., "What does Wisconsin law require for property assessment uniformity?". Verify:
- Agent returns an answer with citations.
- CloudWatch logs for `AgenticRetrievalFunction` show tool calls including `vector_search` and `graph_context` in the result.
- No errors.

- [ ] **Step 6: Commit (bundle artifacts are gitignored; this is just a recording of deploy)**

No git commit needed — nothing changed in tracked files. Update tasks/deploy notes if using TaskUpdate.

---

# TRACK E — Frontend badges (Isaac)

Isaac can start this work as soon as the plan is shared. The only dependency on Jonah's work is **Task 5.2** (reading `discovery_tag` from the document), which needs Jonah's Track B Task 2.4 to land. Isaac can complete Tasks 5.1, 5.3, and 5.4 immediately against mocked data.

Isaac: run all commands from the repo root (`/Users/jonahchan/dev/dxhub/wisco`). Frontend dev server:

```bash
cd packages/webapp && bun dev
```

## Task 5.1: Authority badge component

**Files:**
- Create: `packages/webapp/src/components/documents/document-card/authority-badge.tsx`
- Modify: `packages/webapp/src/components/documents/document-card/index.ts`

- [ ] **Step 1: Create the component**

Create `packages/webapp/src/components/documents/document-card/authority-badge.tsx`:

```tsx
import { BadgeWithFade } from '@/components/ui/badge-with-fade';
import { cn } from '@/lib/utils';

interface AuthorityBadgeProps {
  authorityLevel: number;
  size?: 'sm' | 'md';
}

const AUTHORITY_LABELS: Record<number, { label: string; tone: string }> = {
  1: { label: 'Constitution', tone: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200' },
  2: { label: 'Statute', tone: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200' },
  3: { label: 'Case Law', tone: 'bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-200' },
  4: { label: 'Admin Rule', tone: 'bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-200' },
  5: { label: 'WPAM', tone: 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200' },
  6: { label: 'FAQ', tone: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200' },
  7: { label: 'Gov. Pub.', tone: 'bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200' },
  8: { label: 'IAAO (advisory)', tone: 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200' },
  9: { label: 'USPAP (advisory)', tone: 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200' },
};

export function AuthorityBadge({ authorityLevel, size = 'sm' }: AuthorityBadgeProps) {
  const meta = AUTHORITY_LABELS[authorityLevel];
  if (!meta) return null;

  const sizeClass = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

  return (
    <BadgeWithFade className={cn(meta.tone, sizeClass, 'font-medium')}>
      {meta.label}
    </BadgeWithFade>
  );
}
```

- [ ] **Step 2: Export the badge**

Edit `packages/webapp/src/components/documents/document-card/index.ts` and add an export line (check existing exports first to match style; if the file exports via `export * from './foo'`, add `export * from './authority-badge'`; if it exports named, add `export { AuthorityBadge } from './authority-badge'`).

- [ ] **Step 3: Render locally with mock data**

In the dev server, temporarily hard-code an `authorityLevel={2}` prop on any page to verify the badge renders correctly and dark-mode styles work. Revert after visual confirmation.

- [ ] **Step 4: Commit**

```bash
git add packages/webapp/src/components/documents/document-card/authority-badge.tsx packages/webapp/src/components/documents/document-card/index.ts
git commit -m "feat(webapp): authority-level badge component for RAG document cards"
```

## Task 5.2: Discovery badge component

**Files:**
- Create: `packages/webapp/src/components/documents/document-card/discovery-badge.tsx`
- Modify: `packages/webapp/src/components/documents/document-card/index.ts`

**Depends on:** Jonah's Track B Task 2.4 (adds `discoveryTag` field — camelCased from `discovery_tag`). Until that lands, Isaac can still build this component and test against mocked data.

- [ ] **Step 1: Create the component**

Create `packages/webapp/src/components/documents/document-card/discovery-badge.tsx`:

```tsx
import { BadgeWithFade } from '@/components/ui/badge-with-fade';
import { cn } from '@/lib/utils';

export type DiscoveryTag =
  | 'vector-search'
  | 'graph-neighbor'
  | 'fetched'
  | 'framework-list'
  | 'unknown';

interface DiscoveryBadgeProps {
  tag: DiscoveryTag;
  size?: 'sm' | 'md';
}

const TAG_META: Record<DiscoveryTag, { label: string; tone: string; description: string }> = {
  'vector-search': {
    label: 'Semantic match',
    tone: 'border-blue-300 text-blue-800 dark:border-blue-700 dark:text-blue-200',
    description: 'Found via semantic similarity to your question',
  },
  'graph-neighbor': {
    label: 'Related via graph',
    tone: 'border-purple-300 text-purple-800 dark:border-purple-700 dark:text-purple-200',
    description: 'Connected through legal authority or citations',
  },
  fetched: {
    label: 'Directly looked up',
    tone: 'border-teal-300 text-teal-800 dark:border-teal-700 dark:text-teal-200',
    description: 'The agent fetched this specific document by ID',
  },
  'framework-list': {
    label: 'From framework list',
    tone: 'border-slate-300 text-slate-800 dark:border-slate-700 dark:text-slate-200',
    description: 'Part of the authority framework the agent browsed',
  },
  unknown: {
    label: 'Source',
    tone: 'border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-300',
    description: 'Discovery method unrecorded',
  },
};

export function DiscoveryBadge({ tag, size = 'sm' }: DiscoveryBadgeProps) {
  const meta = TAG_META[tag] ?? TAG_META.unknown;
  const sizeClass = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

  return (
    <BadgeWithFade
      variant="outline"
      className={cn(meta.tone, sizeClass, 'font-normal')}
      title={meta.description}
    >
      {meta.label}
    </BadgeWithFade>
  );
}
```

- [ ] **Step 2: Export the badge**

Append to `packages/webapp/src/components/documents/document-card/index.ts`: export the new component consistent with the file's existing export style.

- [ ] **Step 3: Commit**

```bash
git add packages/webapp/src/components/documents/document-card/discovery-badge.tsx packages/webapp/src/components/documents/document-card/index.ts
git commit -m "feat(webapp): discovery-method badge component for RAG document cards"
```

## Task 5.3: Extend Document type and thread fields through the card

**Files:**
- Modify: `packages/webapp/src/components/documents/document-card/document-card.tsx`

- [ ] **Step 1: Add new optional fields to the Document interface**

Edit `document-card.tsx`, replace the `Document` interface (around lines 17-23) with:

```tsx
export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
  sourceUrl?: string;
  authorityLevel?: number;
  discoveryTag?:
    | 'vector-search'
    | 'graph-neighbor'
    | 'fetched'
    | 'framework-list'
    | 'unknown';
}
```

- [ ] **Step 2: Render badges in the compact card**

Add the imports at the top of the file (near the existing `import { DocumentBadge }`):

```tsx
import { AuthorityBadge } from './authority-badge';
import { DiscoveryBadge } from './discovery-badge';
```

In `DocumentCardCompact` (around line 202-211), adjust the `CardContent` to render the new badges alongside the existing source badge. Replace the `<CardContent>` block with:

```tsx
<CardContent className="pt-0">
  <div className="flex flex-wrap gap-1.5 mb-2">
    {document.authorityLevel !== undefined && (
      <AuthorityBadge authorityLevel={document.authorityLevel} size="sm" />
    )}
    {document.discoveryTag && document.discoveryTag !== 'unknown' && (
      <DiscoveryBadge tag={document.discoveryTag} size="sm" />
    )}
  </div>

  {document.source && (
    <DocumentBadge
      source={document.source}
      sourceUrl={document.sourceUrl}
      onSourceClick={onSourceClick}
      size="sm"
    />
  )}

  <div className="text-muted-foreground flex items-center justify-between text-sm">
    <span>ID: {document.documentId}</span>
    <ExternalLink className="h-3 w-3" />
  </div>
</CardContent>
```

- [ ] **Step 3: Do the same in the modal**

Inside `DocumentCardModal` (around lines 258-288), find the header section that currently renders `<DocumentBadge>` in the top-right (around lines 267-275). Replace it with a vertical flex stack that shows the three badges and the close button:

```tsx
<div className="flex items-start gap-2">
  <div className="flex flex-col items-end gap-1.5">
    {document.authorityLevel !== undefined && (
      <AuthorityBadge authorityLevel={document.authorityLevel} size="md" />
    )}
    {document.discoveryTag && document.discoveryTag !== 'unknown' && (
      <DiscoveryBadge tag={document.discoveryTag} size="md" />
    )}
    {document.source && (
      <DocumentBadge
        source={document.source}
        sourceUrl={document.sourceUrl}
        onSourceClick={onSourceClick}
        size="md"
      />
    )}
  </div>

  <Button
    variant="ghost"
    size="icon"
    onClick={onClose}
    className="h-8 w-8"
    aria-label="Close modal"
  >
    <X className="h-4 w-4" />
  </Button>
</div>
```

- [ ] **Step 4: Run typecheck**

```bash
cd packages/webapp && bunx tsc --noEmit && cd ../..
```

Expected: no type errors.

- [ ] **Step 5: Visual smoke test**

```bash
cd packages/webapp && bun dev
```

Open `http://localhost:3000`, send a sample query through the chat. When documents render, both badges should appear.

- [ ] **Step 6: Commit**

```bash
git add packages/webapp/src/components/documents/document-card/document-card.tsx
git commit -m "feat(webapp): render authority and discovery badges on document cards"
```

## Task 5.4: Map the backend fields in the chat store

**Files:**
- Find: The message/document mapping layer in `packages/webapp/src/stores/` or `packages/webapp/src/lib/`. Grep for `documentId` or `RAGDocument` to locate it.

- [ ] **Step 1: Locate the mapping**

```bash
cd /Users/jonahchan/dev/dxhub/wisco
grep -rn "documentId" packages/webapp/src/stores packages/webapp/src/lib packages/webapp/src/hooks 2>&1 | head
```

The websocket handler or store will deserialize documents from the backend. Find where `document_id` / `source` fields become the `Document` object. Add `authorityLevel` and `discoveryTag` to that mapping.

- [ ] **Step 2: Wire backend fields to the frontend Document**

Since `CamelCaseModel` in the backend converts snake_case → camelCase, the wire payload will already be `discoveryTag` and (if extended) `authorityLevel`. Ensure whatever transformer/mapper builds the `Document` from the wire payload passes those fields through. If the existing code uses spread semantics (`...payload`), it may already work — verify by running the dev server and sending a query.

If the mapping is explicit (field-by-field), add:
```ts
authorityLevel: wirePayload.authorityLevel,
discoveryTag: wirePayload.discoveryTag,
```

Note: `RAGDocument` currently does NOT expose `authorityLevel` — that field lives on the graph's `Document` node and is surfaced by `get_document`. For Task 5.4 to work end-to-end for authority level, Jonah would need an optional follow-up to expose `authority_level` on `RAGDocument`. For this plan, Isaac ships Task 5.4 supporting only `discoveryTag`; authority level displays when a future patch threads it through.

Update the Document interface in `document-card.tsx` to clarify `authorityLevel` is currently not wired (keep the field optional — it's ready for when the backend threads it through).

- [ ] **Step 3: Commit**

```bash
git add -p packages/webapp/src/
git commit -m "feat(webapp): wire discoveryTag from RAGDocument payload"
```

---

## After all tracks land

- [ ] **Step 1: Run the full Python test suite**

```bash
.venv/bin/python3 -m pytest packages/graphrag/lambdas/test/ scripts/graphrag/tests/ -v
```

Expected: all pass.

- [ ] **Step 2: Run webapp typecheck + lint**

```bash
cd packages/webapp && bunx tsc --noEmit && bunx eslint . && cd ../..
```

Expected: no errors.

- [ ] **Step 3: End-to-end verification**

Send three representative queries through the deployed webapp:

1. **Simple FAQ-answerable:** "What is property tax in Wisconsin?" — expect `faq_search` → answer in 1-2 turns.
2. **Multi-hop graph:** "What does Wisconsin statute 70.32 require assessors to do?" — expect `vector_search` (with auto-enrichment visible in logs) → `get_neighbors` or `get_authority_chain` → answer with citations showing authority chain.
3. **Case-law deep dive:** "What did the court hold in Corroon v. Hosch about customer list misappropriation?" — expect `vector_search` → identifies stub `case-law-109-wis-2d-290` → calls `fetch_case_opinion` with citation `109 Wis. 2d 290` → answer quotes opinion text.

For each, check:
- Cited sources on the frontend display correct authority and discovery badges.
- CloudWatch logs show the expected tool call sequence.
- No errors.

---

## Self-Review

**Spec coverage** (each gap from our analysis against `docs/graphrag.md`):

| Gap | Addressed by |
|-----|--------------|
| Auto-enrichment on vector_search | Task 2.2 |
| "PREFER graph traversal" in prompt | Task 2.1 |
| Framework applicability matrix | Task 2.1 |
| "ONLY cite documents retrieved via tools" | Task 2.1 |
| Requires vs Recommends distinction | Task 2.1 |
| Out-of-scope awareness | Task 2.1 |
| Source tracking with discovery tags | Task 2.4 |
| "Err on the side of including more sources" | Task 2.1 (prompt) |
| Turn 8 warning + turn 10 fallback extraction | Task 2.4 (run_agentic_loop rewrite) |
| max_tokens truncation recovery | Task 2.4 |
| get_document fallback to vector search | Task 2.3 |
| Two-tier case-law retrieval (ingestion + tool) | Tasks 1, 2, 3, 4 |

**Placeholder scan:** No TBD/TODO/"similar to" references — every code block is complete. Every file path is absolute from repo root. Every command is copy-pasteable.

**Type consistency:**
- `discovery_tag` field added to `RAGDocument` → camelCased as `discoveryTag` on frontend. Track E Task 5.3 uses `discoveryTag` consistently.
- `citation_to_raw_slug` defined in Task 1, used in Task 2's test and in `fetch_case_opinion` body.
- `fetch_case_opinion` signature: `(citation: str, raw_bucket: str, s3_client=None) -> dict`. Used consistently in Task 2 tests and Task 3 tool dispatch.
- `find_stale_extracts(raw_ids, extracted_ids)` and `delete_stale_extracts(s3, bucket, stale_ids)` signatures match between Task 4 tests and implementation.
- Prompt test assertions (`test_prompt.py`) match the strings actually present in the new `SYSTEM_PROMPT`.

**One gap flagged during review:** Task 5.4 notes that `authority_level` isn't currently on `RAGDocument`. To keep this plan scoped, we leave that as a future patch rather than expanding `RAGDocument` a second time. Isaac's `AuthorityBadge` component is built anyway so it's ready when the field is threaded.

---

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-04-21-graphrag-quality-hardening.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatches a fresh subagent per task with two-stage review between tasks.
2. **Inline Execution** — executes tasks in this session with batch checkpoints.

Which approach would you like for Jonah's tracks (A, B, C, D)? (Isaac's Track E runs in parallel regardless.)
