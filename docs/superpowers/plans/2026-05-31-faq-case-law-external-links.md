# FAQ & Court Case External Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FAQ source cards link back to their `revenue.wi.gov` page and court-case cards link to Google Scholar, instead of pointing at plain-text copies in S3.

**Architecture:** Court cases reuse the existing `_scholar_url(citation)` helper — we set `source_url=scholar_url` and `s3_key=None` on case-law cards so the frontend opens the public URL. FAQs gain a `source_url` resolved at query time from a new DynamoDB table (`FaqUrlTable`) keyed on normalized question text, seeded from `documents/faqs.json` and kept current by the FAQ extract script. The `source_url` is threaded through the full WebSocket contract (Pydantic → websocket model → resource streaming → Zod → frontend → chat-history) to a new "View on revenue.wi.gov" button on the FAQ card.

**Tech Stack:** Python 3.12 Lambdas (Pydantic v2, boto3), AWS CDK (TypeScript), DynamoDB, Next.js/React + Zod, pytest + bun test.

---

## Background: key facts the implementer needs

- **Repo root:** `/Users/jonahchan/dev/dxhub/wisco`. Branch: `feat/graphrag-migration`.
- **Python tests run with:** `uv run pytest <path> -v`. GraphRAG lambda tests live in `packages/graphrag/lambdas/test/` and import the lambda module directly because `packages/graphrag/lambdas/test/conftest.py` inserts `../agentic_retrieval` onto `sys.path`. So `from main import ...` and `from case_opinion import ...` work inside those tests.
- **Messages lambda tests** live in `packages/messages/lambdas/test/` and add `../` plus the shared layer to `sys.path` at the top of each test file (see `test_resource_streaming.py`).
- **Frontend tests run with:** `bun test <path>` from `packages/webapp/`. They import from `bun:test`.
- **WebSocket contract is three-sided** (per CLAUDE.md): Python `websocket_utils/models.py` → Zod `packages/messages/types/message-types.ts` → frontend handler `packages/webapp/src/hooks/use-websocket-chat.ts` + store type `packages/webapp/src/stores/types.ts`. A new field must be added on every side or the frontend rejects the message.
- **`_scholar_url`** already exists at `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py:41` and returns `http://scholar.google.com/scholar?...&q=<urlencoded citation>`.
- **`neptune.get_document(doc_id)`** already returns a `citation` field (`neptune_client.py:209`).
- **`_is_case_law_stub(doc_id)`** exists at `main.py:1288` — true when `doc_id` starts with `case-law-`.
- **`MAX_FAQS = 3`** — at most 3 FAQs reach the resource, so query-time lookups are tiny.
- **Normalization** for FAQ question keys must match the seed script. Canonical rule used everywhere in this plan:
  `re.sub(r"\s+", " ", text.replace("​","").replace(" "," ").replace("﻿","")).strip().lower().rstrip("?.").strip()`
- **Coverage:** ~92% of live FAQ files match a manifest URL on exact normalized question; fuzzy recovery (exact-answer match, then 50-char question-prefix match) lifts this to ~94.5%. The rest gracefully show no link.

## File Structure

**Create:**
- `scripts/graphrag/faq_url_map.py` — shared FAQ-URL normalization + manifest-loading helpers (one responsibility: turn `faqs.json` into a normalized-question → url map, with fuzzy recovery). Importable by both the seed script and tests.
- `scripts/graphrag/seed_faq_url_table.py` — CLI that loads the map and upserts it into `FaqUrlTable`.
- `scripts/graphrag/tests/test_faq_url_map.py` — unit tests for the normalization + fuzzy recovery.

**Modify:**
- `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py` — export `_scholar_url` under a public name (`scholar_url`) so `main.py` can import it without touching a private symbol.
- `packages/graphrag/lambdas/agentic_retrieval/main.py` — case-law cards link to Scholar; FAQ resource gains URL lookup; chat-history persists FAQ `sourceUrl`.
- `packages/shared/lambda_layers/step_function_types/models.py` — `FAQ.source_url`.
- `packages/shared/lambda_layers/websocket_utils/models.py` — `FAQ.source_url`.
- `packages/messages/lambdas/resource_streaming/main.py` — map `source_url` into the `FAQMessage`.
- `packages/messages/types/message-types.ts` — `FAQSchema.sourceUrl`.
- `packages/webapp/src/stores/types.ts` — `FAQ.sourceUrl`.
- `packages/webapp/src/components/documents/document-card/faq-card.tsx` — render the link button.
- `scripts/graphrag/extract_faq_qa_pairs.py` — optional `--faq-url-table` upsert during refresh.
- `packages/graphrag/infra/graphrag-stack.ts` — create `FaqUrlTable`, expose name.
- `packages/graphrag/infra/graphrag-messages-stack.ts` — accept table, grant read, set env var.
- `packages/infra/lib/stack.ts` — pass the table from GraphRAGStack into GraphRAGMessagesStack.

**Tests:**
- `packages/graphrag/lambdas/test/test_case_opinion.py` (extend)
- `packages/graphrag/lambdas/test/test_agentic_retrieval.py` (extend)
- `packages/messages/lambdas/test/test_resource_streaming.py` (extend)
- `packages/webapp/src/components/documents/document-card/test/faq-card.test.tsx` (create)
- `scripts/graphrag/tests/test_faq_url_map.py` (create)

---

## Part A — Court cases link to Google Scholar

### Task 1: Expose a public `scholar_url` from case_opinion

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py:41`
- Test: `packages/graphrag/lambdas/test/test_case_opinion.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_case_opinion.py`:

```python
def test_scholar_url_public_alias_encodes_citation():
    from case_opinion import scholar_url

    url = scholar_url("109 Wis. 2d 290")
    assert url.startswith("http://scholar.google.com/scholar?")
    assert "q=109%20Wis.%202d%20290" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/graphrag/lambdas/test/test_case_opinion.py::test_scholar_url_public_alias_encodes_citation -v`
Expected: FAIL with `ImportError: cannot import name 'scholar_url'`

- [ ] **Step 3: Add the public alias**

In `packages/graphrag/lambdas/agentic_retrieval/case_opinion.py`, immediately after the `_scholar_url` function definition (after line 47), add:

```python
# Public alias so other modules can build the same Google Scholar search URL
# without reaching into a private helper. _scholar_url stays for internal use.
def scholar_url(citation: str) -> str:
    """Public wrapper around _scholar_url for cross-module use."""
    return _scholar_url(citation)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/graphrag/lambdas/test/test_case_opinion.py::test_scholar_url_public_alias_encodes_citation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/case_opinion.py packages/graphrag/lambdas/test/test_case_opinion.py
git commit -m "feat(graphrag): expose public scholar_url helper from case_opinion"
```

---

### Task 2: Opinion cards link to Google Scholar, not the S3 .txt

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:25` (import), `:1480-1495` (`_build_opinion_card`)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

Context: `_build_opinion_card` currently does
`source_url=None if raw_key else (scholar_url or None)` and `s3_key=raw_key or None`,
so when the opinion `.txt` exists it links to S3. We always link to Scholar instead.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_build_opinion_card_links_to_scholar_not_s3(monkeypatch):
    import main

    # get_document is consulted for title/authority; stub it.
    monkeypatch.setattr(
        main.neptune,
        "get_document",
        lambda doc_id: {"title": "Corroon v. Hosch", "authority_level": 3},
    )

    payload = {
        "citation": "109 Wis. 2d 290",
        "raw_key": "raw/case-law-109-wis-2d-290/case-law-109-wis-2d-290.txt",
        "text": "Full opinion text...",
        "scholar_url": "http://scholar.google.com/scholar?q=109%20Wis.%202d%20290",
    }
    card = main._build_opinion_card("case-law-109-wis-2d-290", payload)

    # Even though raw_key is present, the user link must be Google Scholar.
    assert card.s3_key is None
    assert card.source_url == "http://scholar.google.com/scholar?q=109%20Wis.%202d%20290"
    # Opinion text still feeds downstream synthesis.
    assert card.content == "Full opinion text..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_build_opinion_card_links_to_scholar_not_s3 -v`
Expected: FAIL — `card.s3_key` equals the raw key (not None) and `source_url` is None.

- [ ] **Step 3: Update `_build_opinion_card`**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`, change the `RAGDocument` returned by `_build_opinion_card` (lines 1480-1495). Replace the `source_url=` and `s3_key=` lines:

```python
    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        # Always link the user to Google Scholar for the citation, even when
        # the opinion .txt is archived in S3. The S3 object is a flat text
        # blob with no page anchor, so linking to the public opinion loses
        # nothing and gives a properly formatted, citable source. The opinion
        # text still rides in `content` to inform synthesis.
        source_url=scholar_url or _scholar_url_fn(citation),
        s3_key=None,
        start_page=None,
        end_page=None,
        discovery_tag="opinion-fetched",
        authority_level=doc_info.get("authority_level") if doc_info else 3,
        edition_year=None,
    )
```

Note: `scholar_url` here is the local variable already assigned at the top of the function (`scholar_url = payload.get("scholar_url", "")`). `_scholar_url_fn` is the imported helper used as a fallback when the payload lacks one. Update the import at `main.py:25` from:

```python
from case_opinion import citation_to_raw_slug
```

to:

```python
from case_opinion import citation_to_raw_slug, scholar_url as _scholar_url_fn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_build_opinion_card_links_to_scholar_not_s3 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): opinion cards link to Google Scholar instead of S3 text"
```

---

### Task 3: Case-law stub cards link to Google Scholar

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py` — both `RAGDocument` constructions in `_build_rag_documents` (the chunk path ~1528 and the no-chunk path ~1607)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

Context: not every case-law card comes from a fetched opinion. Stubs discovered via the graph are built in `_build_rag_documents`. For those, derive the Scholar URL from the node's `citation` and null the `s3_key`.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_build_rag_documents_case_law_stub_links_to_scholar(monkeypatch):
    import main

    monkeypatch.setattr(
        main.neptune,
        "get_document",
        lambda doc_id: {
            "title": "Some Case v. Other",
            "authority_level": 3,
            "citation": "200 Wis. 2d 1",
            "source_url": "https://docs.legis.wisconsin.gov/document/courts/200%20Wis.%202d%201",
            "s3_key": "raw/case-law-200-wis-2d-1/case-law-200-wis-2d-1.txt",
        },
    )

    chunks = [
        {
            "doc_id": "case-law-200-wis-2d-1",
            "text": "stub summary text",
            "s3_key": "raw/case-law-200-wis-2d-1/case-law-200-wis-2d-1.txt",
        }
    ]
    docs = main._build_rag_documents(chunks, {"case-law-200-wis-2d-1"})
    assert len(docs) == 1
    card = docs[0]
    assert card.s3_key is None
    assert card.source_url.startswith("http://scholar.google.com/scholar?")
    assert "q=200%20Wis.%202d%201" in card.source_url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_build_rag_documents_case_law_stub_links_to_scholar -v`
Expected: FAIL — `card.s3_key` is the raw key and `source_url` is the legis URL.

- [ ] **Step 3: Add a case-law override helper and apply it in both build paths**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`, add a helper just above `_build_rag_documents` (before line 1498):

```python
def _case_law_link_override(
    doc_id: str, doc_info: dict | None, s3_key: str | None, source_url: str | None
) -> tuple[str | None, str | None]:
    """For case-law docs, return (source_url, s3_key) that link to Google
    Scholar and drop the S3 reference.

    Case-law S3 objects are flat .txt with no page anchor; linking to the
    public Scholar search for the citation is strictly better. Non-case-law
    docs pass through unchanged. Falls back to the incoming values when the
    node has no citation to build a Scholar URL from.
    """
    if not _is_case_law_stub(doc_id):
        return source_url, s3_key
    citation = (doc_info or {}).get("citation")
    if not citation:
        return source_url, s3_key
    return _scholar_url_fn(citation), None
```

Then, in the **chunk path** (the first `RAGDocument(...)` inside `_build_rag_documents`, ~line 1528), replace the `gov_url` / `s3_key` assignment block. Currently:

```python
            gov_url = chunk.get("source_url") or (doc_info or {}).get("source_url")
            s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key")
```

becomes:

```python
            gov_url = chunk.get("source_url") or (doc_info or {}).get("source_url")
            s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key")
            gov_url, s3_key = _case_law_link_override(doc_id, doc_info, s3_key, gov_url)
```

In the **no-chunk path** (the second `RAGDocument(...)`, ~line 1607), it currently passes
`source_url=doc_info.get("source_url")` and `s3_key=doc_info.get("s3_key")` directly. Replace those two argument values by computing them first. Immediately before that `docs_by_id[doc_id] = RAGDocument(` (line ~1607) insert:

```python
        nochunk_url, nochunk_s3 = _case_law_link_override(
            doc_id, doc_info, doc_info.get("s3_key"), doc_info.get("source_url")
        )
```

and change the constructor's two lines to:

```python
            source_url=nochunk_url,
            s3_key=nochunk_s3,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_build_rag_documents_case_law_stub_links_to_scholar -v`
Expected: PASS

- [ ] **Step 5: Run the full case-law + retrieval test files to check no regressions**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py packages/graphrag/lambdas/test/test_case_opinion.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): case-law stub cards link to Google Scholar"
```

---

## Part B — FAQ external links

### Task 4: FAQ-URL map helper (normalization + fuzzy recovery)

**Files:**
- Create: `scripts/graphrag/faq_url_map.py`
- Test: `scripts/graphrag/tests/test_faq_url_map.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/graphrag/tests/test_faq_url_map.py`:

```python
import json

from scripts.graphrag.faq_url_map import normalize_question, build_url_map, lookup_url


def test_normalize_question_collapses_and_strips():
    assert normalize_question("  Is  THIS a Test?  ") == "is this a test"
    assert normalize_question("Already clean.") == "already clean"
    # nbsp / zero-width / bom noise collapses to a single space
    assert normalize_question("a ​b") == "a b"


def test_build_url_map_exact_match():
    records = [
        {"Q": "What is X?", "A": "X is a thing.", "source_url": "https://example.gov/x"},
    ]
    url_map = build_url_map(records)
    assert lookup_url("what is x", "anything", url_map) == "https://example.gov/x"


def test_lookup_recovers_by_answer_then_prefix():
    records = [
        {"Q": "What is the exact original question?", "A": "Unique answer body.",
         "source_url": "https://example.gov/a"},
        {"Q": "A very long question that differs only after fifty characters of text here",
         "A": "Other.", "source_url": "https://example.gov/b"},
    ]
    url_map = build_url_map(records)
    # Answer match: question text drifted but answer is identical.
    assert lookup_url("totally different wording", "Unique answer body.", url_map) == "https://example.gov/a"
    # Prefix match: first 50 normalized chars line up, tail differs.
    drifted = "A very long question that differs only after fifty CHARACTERS differ now"
    assert lookup_url(drifted, "nope", url_map) == "https://example.gov/b"


def test_lookup_orphan_returns_none():
    url_map = build_url_map([{"Q": "Known?", "A": "Yes.", "source_url": "https://example.gov/k"}])
    assert lookup_url("completely unknown question", "and answer", url_map) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/graphrag/tests/test_faq_url_map.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.graphrag.faq_url_map'`

- [ ] **Step 3: Implement the helper**

Create `scripts/graphrag/faq_url_map.py`:

```python
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
    cleaned = text.replace("​", "").replace(" ", " ").replace("﻿", "")
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
```

Also create an empty `scripts/graphrag/tests/__init__.py` if pytest import of `scripts.graphrag.faq_url_map` requires package resolution. Verify first:

Run: `uv run python -c "import scripts.graphrag.faq_url_map"`
If that errors with ModuleNotFound, run: `touch scripts/__init__.py scripts/graphrag/__init__.py scripts/graphrag/tests/__init__.py` — but only the ones that don't already exist (check with `ls`). If `scripts/graphrag/__init__.py` does not exist and other `scripts/graphrag/tests/*.py` already import as `from scripts.graphrag...`, follow whatever the existing test files do; otherwise adjust the test import to `from faq_url_map import ...` and run pytest with `cd scripts/graphrag && uv run pytest tests/test_faq_url_map.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/graphrag/tests/test_faq_url_map.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/graphrag/faq_url_map.py scripts/graphrag/tests/test_faq_url_map.py
git commit -m "feat(graphrag): FAQ-URL map helper with fuzzy recovery"
```

---

### Task 5: Add `source_url` to the FAQ shared model + WebSocket model

**Files:**
- Modify: `packages/shared/lambda_layers/step_function_types/models.py:48-51`
- Modify: `packages/shared/lambda_layers/websocket_utils/models.py:62-65`
- Test: `packages/messages/lambdas/test/test_resource_streaming.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/messages/lambdas/test/test_resource_streaming.py`:

```python
def test_faq_models_carry_source_url():
    from step_function_types.models import FAQ as SfnFAQ
    from websocket_utils.models import FAQ as WsFAQ

    sfn = SfnFAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x")
    assert sfn.source_url == "https://revenue.wi.gov/x"

    ws = WsFAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x")
    # CamelCaseModel aliasing -> sourceUrl on the wire.
    dumped = ws.model_dump(by_alias=True)
    assert dumped["sourceUrl"] == "https://revenue.wi.gov/x"

    # Backward compatible: omitting source_url is allowed and defaults to None.
    assert SfnFAQ(faq_id="faq_2", question="Q?", answer="A.").source_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/messages/lambdas/test/test_resource_streaming.py::test_faq_models_carry_source_url -v`
Expected: FAIL — `FAQ.__init__() got an unexpected keyword argument 'source_url'`

- [ ] **Step 3: Add the field to both models**

In `packages/shared/lambda_layers/step_function_types/models.py`, change the `FAQ` class (lines 48-51) to:

```python
class FAQ(BaseModel):
    faq_id: str
    question: str
    answer: str
    # Public revenue.wi.gov source page for this FAQ, resolved at query time
    # from the FAQ-URL table. None when no URL could be matched (no link shown).
    source_url: str | None = None
```

In `packages/shared/lambda_layers/websocket_utils/models.py`, change the `FAQ` class (lines 62-65) to:

```python
class FAQ(WebSocketMessage):
    faq_id: str
    question: str
    answer: str
    # Mirrors the shared FAQ model; serialized as `sourceUrl` for the frontend.
    source_url: str | None = None
```

(`WebSocketMessage`/`CamelCaseModel` already applies the camelCase alias generator, so `source_url` → `sourceUrl` automatically — confirm by the test above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/messages/lambdas/test/test_resource_streaming.py::test_faq_models_carry_source_url -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/shared/lambda_layers/step_function_types/models.py packages/shared/lambda_layers/websocket_utils/models.py packages/messages/lambdas/test/test_resource_streaming.py
git commit -m "feat(shared): add source_url to FAQ models"
```

---

### Task 6: Resource streaming maps FAQ `source_url` onto the WebSocket message

**Files:**
- Modify: `packages/messages/lambdas/resource_streaming/main.py:144-150`
- Test: `packages/messages/lambdas/test/test_resource_streaming.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/messages/lambdas/test/test_resource_streaming.py`:

```python
def test_stream_faq_message_includes_source_url():
    import asyncio
    from unittest.mock import AsyncMock
    from resource_streaming.main import _stream_resources_async
    from step_function_types.models import StreamResourcesJob

    job = StreamResourcesJob(
        query_id="q1",
        session_id="s1",
        documents={"documents": []},
        faqs={"faqs": [
            {"faq_id": "faq_1", "question": "Q?", "answer": "A.",
             "source_url": "https://revenue.wi.gov/x"},
        ]},
    )
    ws = AsyncMock()
    asyncio.run(_stream_resources_async(job, ws))

    sent = [c.args[0] for c in ws.send_json.call_args_list]
    faq_msgs = [m for m in sent if getattr(m, "response_type", None) == "faq"]
    assert len(faq_msgs) == 1
    assert faq_msgs[0].content.faqs[0].source_url == "https://revenue.wi.gov/x"
```

Note: confirm the `StreamResourcesJob` field names (`faqs`/`documents`) against `step_function_types/models.py`; adjust the constructor to match if they differ. If `StreamResourcesJob` requires non-empty documents, pass `documents={"documents": []}` as shown.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/messages/lambdas/test/test_resource_streaming.py::test_stream_faq_message_includes_source_url -v`
Expected: FAIL — `source_url` is `None` because the mapping drops it.

- [ ] **Step 3: Map the field**

In `packages/messages/lambdas/resource_streaming/main.py`, the FAQ construction (lines 144-150) currently lists `faq_id`, `question`, `answer`. Add `source_url`:

```python
                faqs=[
                    FAQ(
                        faq_id=faq.faq_id,
                        question=faq.question,
                        answer=faq.answer,
                        source_url=faq.source_url,
                    )
                    for faq in faq_resource.faqs
                ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/messages/lambdas/test/test_resource_streaming.py::test_stream_faq_message_includes_source_url -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/messages/lambdas/resource_streaming/main.py packages/messages/lambdas/test/test_resource_streaming.py
git commit -m "feat(messages): stream FAQ source_url over WebSocket"
```

---

### Task 7: Make the Zod FAQ schema the single source of truth (refactor) + add `sourceUrl`

**Files:**
- Modify: `packages/messages/types/message-types.ts:38-42`
- Modify: `packages/webapp/src/stores/types.ts:72-76`
- Modify: `packages/webapp/src/components/documents/document-card/faq-card.tsx:17-21`

Context (the refactor): the `FAQ` shape is currently hand-declared in **three**
places — the Zod schema (`message-types.ts`, the canonical wire contract,
exported as `@messages/websocket-interface`), the store interface
(`stores/types.ts`), and a third copy inside `faq-card.tsx` (re-exported via
`components/documents/index.ts` and `document-list/index.ts`). Adding a field
means editing all three or silently losing data — exactly the drift hazard the
CLAUDE.md WebSocket-contract rule warns about. We collapse the two duplicates to
import the Zod-inferred type, so `sourceUrl` is added in **one** place and the
type can't drift. `packages/messages` is already a workspace dependency of the
webapp (`"@messages/websocket-interface": "workspace:*"`) and the WebSocket hook
already imports from it, so the dependency direction is established. (The
`Document` interface is duplicated the same way; leaving it as a follow-up to
keep this change FAQ-scoped.)

- [ ] **Step 1: Add `sourceUrl` to the single source of truth (Zod schema)**

In `packages/messages/types/message-types.ts`, change `FAQSchema` (lines 38-42) to:

```typescript
export const FAQSchema = z.object({
  faqId: z.string(),
  question: z.string(),
  answer: z.string(),
  // Public revenue.wi.gov page for this FAQ; absent/null when unmatched.
  sourceUrl: optStr,
});
```

The exported `export type FAQ = z.infer<typeof FAQSchema>;` (line 117) now
carries `sourceUrl?: string` automatically — no second edit needed there.

- [ ] **Step 2: Consolidate the store FAQ type**

In `packages/webapp/src/stores/types.ts`, delete the hand-written `FAQ`
interface (lines 72-76) and replace it with a re-export of the canonical type.
Add this import near the top of the file (with the other imports):

```typescript
import type { FAQ } from '@messages/websocket-interface';
```

and where the interface used to be, re-export it so existing
`import { FAQ } from '@/stores/types'` consumers keep working:

```typescript
export type { FAQ };
```

The `FAQContent` interface just below (lines 78-80) keeps referencing `FAQ` and
still compiles.

- [ ] **Step 3: Consolidate the faq-card FAQ type**

In `packages/webapp/src/components/documents/document-card/faq-card.tsx`, delete
the local `export interface FAQ { ... }` (lines 17-21) and replace it with a
re-export of the canonical type. Add to the imports at the top:

```typescript
import type { FAQ } from '@messages/websocket-interface';
```

and add a re-export so `components/documents/index.ts` and
`document-list/index.ts` (`export type { FAQ } from './document-card/faq-card'`)
still resolve:

```typescript
export type { FAQ };
```

- [ ] **Step 4: Type-check both packages compile**

Run: `bunx tsc --noEmit -p packages/messages/tsconfig.json`
Expected: no errors.

Run: `cd packages/webapp && bunx tsc --noEmit`
Expected: no new errors. The three FAQ declarations now resolve to one type;
confirm no error mentions `FAQ` re-export or `sourceUrl`. (Pre-existing
unrelated errors, if any, are out of scope.)

- [ ] **Step 5: Commit**

```bash
git add packages/messages/types/message-types.ts packages/webapp/src/stores/types.ts packages/webapp/src/components/documents/document-card/faq-card.tsx
git commit -m "refactor(webapp): single-source the FAQ type from Zod schema + add sourceUrl"
```

Note: the WebSocket handler at `packages/webapp/src/hooks/use-websocket-chat.ts:89`
passes the validated `faq` object straight through (`data: faq`), so `sourceUrl`
flows to the store with no handler change. Session resume
(`use-session-resume.ts:99`) passes `msg.resources` through untyped, so
rehydration is unaffected.

---

### Task 8: FAQ card renders the "View on revenue.wi.gov" link

**Files:**
- Modify: `packages/webapp/src/components/documents/document-card/faq-card.tsx`
- Test: `packages/webapp/src/components/documents/document-card/test/faq-card.test.tsx`

Context: after Task 7, `faq-card.tsx` imports the `FAQ` type from
`@messages/websocket-interface` (which already includes `sourceUrl?: string`),
so no interface edit is needed here — we only add the link button to the compact
card and the modal, shown when `faq.sourceUrl` exists.

- [ ] **Step 1: Write the failing test**

Create `packages/webapp/src/components/documents/document-card/test/faq-card.test.tsx`:

```tsx
/** @bun */
import { describe, test, expect } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { FAQCardCompact } from '../faq-card';

describe('FAQCardCompact source link', () => {
  const base = { faqId: 'faq_1', question: 'Q?', answer: 'A.' };

  test('renders a revenue.wi.gov link when sourceUrl is present', () => {
    const html = renderToString(
      <FAQCardCompact
        faq={{ ...base, sourceUrl: 'https://www.revenue.wi.gov/Pages/FAQS/x.aspx' }}
        isExpanded={false}
        onClick={() => {}}
      />
    );
    expect(html).toContain('https://www.revenue.wi.gov/Pages/FAQS/x.aspx');
    expect(html).toContain('revenue.wi.gov');
  });

  test('renders no source link when sourceUrl is absent', () => {
    const html = renderToString(
      <FAQCardCompact faq={base} isExpanded={false} onClick={() => {}} />
    );
    expect(html).not.toContain('href=');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/webapp && bun test src/components/documents/document-card/test/faq-card.test.tsx`
Expected: FAIL — `sourceUrl` not on the FAQ interface / no link rendered.

- [ ] **Step 3: Render the link (the FAQ type already has `sourceUrl` from Task 7)**

In `packages/webapp/src/components/documents/document-card/faq-card.tsx`:

a) Add an `ExternalLink` to the lucide import (line 13):

```tsx
import { ExternalLink, Maximize2, X } from 'lucide-react';
```

b) In `FAQCardCompact`, replace the badge footer (lines 186-188) with a footer that also renders the link when present:

```tsx
        <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pb-3">
          <AuthorityBadge authorityLevel={6} size="sm" />
          {faq.sourceUrl && (
            <a
              href={faq.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={event => event.stopPropagation()}
              className="text-primary hover:text-primary/80 ml-auto inline-flex items-center gap-1 text-xs font-medium"
            >
              View on revenue.wi.gov
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
```

c) In `FAQCardModal`, add the same link in the top bar next to the authority badge (lines 226-229), inside the existing `<div className="flex items-center gap-2">`:

```tsx
            <div className="flex items-center gap-2">
              <AuthorityBadge authorityLevel={6} size="sm" />
              {faq.sourceUrl && (
                <a
                  href={faq.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-xs font-medium"
                >
                  View on revenue.wi.gov
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/webapp && bun test src/components/documents/document-card/test/faq-card.test.tsx`
Expected: PASS (2 tests). If `renderToString` needs `react-dom/server` and it isn't resolvable, switch the import to `import { renderToString } from 'react-dom/server.browser';` — `react-dom` 19 is already a dependency.

- [ ] **Step 5: Commit**

```bash
git add packages/webapp/src/components/documents/document-card/faq-card.tsx packages/webapp/src/components/documents/document-card/test/faq-card.test.tsx
git commit -m "feat(webapp): FAQ card links to revenue.wi.gov source page"
```

---

### Task 9: Query-time FAQ URL lookup in the agentic retrieval Lambda

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py` — env var + lookup in `_build_faq_resource` (lines 634-654)
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

Context: `_build_faq_resource` is the single chokepoint feeding both FAQ call sites. We resolve each parsed FAQ's `source_url` via a `BatchGetItem` against `FaqUrlTable`. The table is keyed on `normalized_question` (PK). On any miss/error, `source_url` stays `None`.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_build_faq_resource_attaches_source_url(monkeypatch):
    import main

    # Fake DynamoDB table returning a URL for the normalized question.
    class FakeTable:
        def __init__(self):
            self.requested = []

        def get_item(self, Key):
            self.requested.append(Key["normalized_question"])
            if Key["normalized_question"] == "is x a y":
                return {"Item": {"normalized_question": "is x a y",
                                 "source_url": "https://revenue.wi.gov/x"}}
            return {}

    fake = FakeTable()
    monkeypatch.setattr(main, "FAQ_URL_TABLE", "FaqUrlTable")
    monkeypatch.setattr(main, "_faq_url_table", lambda: fake)

    results = [
        {"text": "Q: Is X a Y?\nA: Yes it is.", "source_uri": "s3://b/faq_1.txt"},
        {"text": "Q: Unknown thing?\nA: No idea.", "source_uri": "s3://b/faq_2.txt"},
    ]
    resource = main._build_faq_resource(results)
    by_q = {f.question: f.source_url for f in resource.faqs}
    assert by_q["Is X a Y?"] == "https://revenue.wi.gov/x"
    assert by_q["Unknown thing?"] is None


def test_build_faq_resource_tolerates_missing_table(monkeypatch):
    import main

    monkeypatch.setattr(main, "FAQ_URL_TABLE", "")  # not configured
    results = [{"text": "Q: Anything?\nA: Sure.", "source_uri": "s3://b/faq_1.txt"}]
    resource = main._build_faq_resource(results)
    assert resource.faqs[0].source_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -k build_faq_resource -v`
Expected: FAIL — `main` has no `FAQ_URL_TABLE` / `_faq_url_table` and `FAQ` has no `source_url` set.

- [ ] **Step 3: Implement the lookup**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`:

a) Near the other env-var/table definitions (after `CHAT_HISTORY_TABLE = ...` at line 487), add:

```python
FAQ_URL_TABLE = os.environ.get("FAQ_URL_TABLE_NAME", "")


def _faq_url_table():
    """Return the FAQ-URL DynamoDB Table resource (separate fn so tests can patch)."""
    return dynamodb_resource.Table(FAQ_URL_TABLE)


def _normalize_faq_question(text: str) -> str:
    """Canonical FAQ question key — must match scripts/graphrag/faq_url_map.py."""
    if not text:
        return ""
    cleaned = text.replace("​", "").replace(" ", " ").replace("﻿", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned.rstrip("?.").strip()


def _lookup_faq_url(question: str) -> str | None:
    """Resolve a FAQ's public source URL by normalized question; None on miss/error."""
    if not FAQ_URL_TABLE:
        return None
    try:
        resp = _faq_url_table().get_item(
            Key={"normalized_question": _normalize_faq_question(question)}
        )
        item = resp.get("Item")
        return item.get("source_url") if item else None
    except Exception:  # noqa: BLE001
        logger.warning("FAQ URL lookup failed", exc_info=True)
        return None
```

b) In `_build_faq_resource` (lines 634-654), attach the URL when building each `FAQ`:

```python
        question, answer = parsed
        faqs.append(
            FAQ(
                faq_id=_faq_id_from_uri(entry.get("source_uri", "")),
                question=question,
                answer=answer,
                source_url=_lookup_faq_url(question),
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -k build_faq_resource -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): resolve FAQ source_url from FaqUrlTable at query time"
```

---

### Task 10: Persist FAQ `sourceUrl` in chat history

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:572-581`
- Test: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

Context: `save_chat_history` writes a `resources` list so restored sessions keep their cards. The FAQ branch (lines 572-581) writes only `faqId/question/answer`. Add `sourceUrl` so restored FAQ cards keep their link.

- [ ] **Step 1: Write the failing test**

Append to `packages/graphrag/lambdas/test/test_agentic_retrieval.py`:

```python
def test_save_chat_history_persists_faq_source_url(monkeypatch):
    import main
    from step_function_types.models import FAQ, FAQResource

    captured = {}

    class FakeTable:
        def put_item(self, Item):
            captured["item"] = Item

    monkeypatch.setattr(main, "CHAT_HISTORY_TABLE", "ChatHistory")
    monkeypatch.setattr(main.dynamodb_resource, "Table", lambda name: FakeTable())

    faq_resource = FAQResource(faqs=[
        FAQ(faq_id="faq_1", question="Q?", answer="A.", source_url="https://revenue.wi.gov/x"),
    ])
    main.save_chat_history("s1", "q1", "the query", "the answer",
                           rag_documents=None, faq_resource=faq_resource)

    faq_res = [r for r in captured["item"]["resources"] if r["type"] == "faq"]
    assert faq_res[0]["data"]["sourceUrl"] == "https://revenue.wi.gov/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_save_chat_history_persists_faq_source_url -v`
Expected: FAIL — `KeyError: 'sourceUrl'`

- [ ] **Step 3: Add `sourceUrl` to the persisted FAQ dict**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`, the FAQ branch of `save_chat_history` (lines 572-581). Replace the appended dict with one that conditionally includes `sourceUrl` (matching the document branch's omit-when-None style):

```python
        if faq_resource:
            for faq in faq_resource.faqs:
                faq_data: dict = {
                    "faqId": faq.faq_id,
                    "question": faq.question,
                    "answer": faq.answer,
                }
                if faq.source_url is not None:
                    faq_data["sourceUrl"] = faq.source_url
                resources.append({"type": "faq", "data": faq_data})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py::test_save_chat_history_persists_faq_source_url -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): persist FAQ sourceUrl in chat history"
```

---

## Part C — Infrastructure & seeding

### Task 11: Create `FaqUrlTable` in the GraphRAG stack

**Files:**
- Modify: `packages/graphrag/infra/graphrag-stack.ts`
- Test: `packages/infra/test/infra.test.ts` (extend the existing CDK assertion suite)

- [ ] **Step 1: Write the failing test**

First inspect the existing test to mirror its style:

Run: `sed -n '1,60p' packages/infra/test/infra.test.ts`

Then append an assertion that the synthesized template contains the FAQ URL table. Add to `packages/infra/test/infra.test.ts` (inside the existing describe/template setup — adapt names to what the file already defines for the synthesized `Template`):

```typescript
test('FaqUrlTable exists with normalized_question hash key', () => {
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    KeySchema: [{ AttributeName: 'normalized_question', KeyType: 'HASH' }],
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/infra && bunx jest -t 'FaqUrlTable'`
Expected: FAIL — no DynamoDB table with that key schema.

- [ ] **Step 3: Define the table and expose its name**

In `packages/graphrag/infra/graphrag-stack.ts`:

a) Add the import at the top (after line 4):

```typescript
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
```

b) Add a public readonly field (after line 14):

```typescript
  public readonly faqUrlTable: dynamodb.Table;
```

c) Create the table inside the constructor (after the `faqBucket` block, ~line 40):

```typescript
    // Maps a normalized FAQ question to its public revenue.wi.gov URL.
    // Seeded from documents/faqs.json and refreshed by extract_faq_qa_pairs.py.
    const faqUrlTable = new dynamodb.Table(this, 'FaqUrlTable', {
      partitionKey: {
        name: 'normalized_question',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.faqUrlTable = faqUrlTable;
```

d) Add a CfnOutput (near the other outputs, ~line 96):

```typescript
    new cdk.CfnOutput(this, 'FaqUrlTableName', {
      value: faqUrlTable.tableName,
      description: 'DynamoDB table mapping normalized FAQ question -> source URL',
    });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/infra && bunx jest -t 'FaqUrlTable'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/infra/graphrag-stack.ts packages/infra/test/infra.test.ts
git commit -m "feat(infra): add FaqUrlTable to GraphRAG stack"
```

---

### Task 12: Wire `FaqUrlTable` into the agentic retrieval Lambda

**Files:**
- Modify: `packages/graphrag/infra/graphrag-messages-stack.ts` (props, env var, grant)
- Modify: `packages/infra/lib/stack.ts:62-81` (pass the table)

- [ ] **Step 1: Add the prop to the messages stack interface**

In `packages/graphrag/infra/graphrag-messages-stack.ts`, add to `GraphRAGMessagesStackProps` (after line 22):

```typescript
  faqUrlTable: cdk.aws_dynamodb.ITable;
```

- [ ] **Step 2: Set the env var on the Lambda**

In the same file, add to the Lambda's `environment` block (after line 64, `FAQ_KNOWLEDGE_BASE_ID`):

```typescript
          FAQ_URL_TABLE_NAME: props.faqUrlTable.tableName,
```

- [ ] **Step 3: Grant read access**

In the same file, after the `props.sessionsTable.grantReadData(agenticRetrievalHandler);` line (line 86), add:

```typescript
    // Read access to the FAQ-URL table so _build_faq_resource can attach the
    // public revenue.wi.gov link to each FAQ at query time.
    props.faqUrlTable.grantReadData(agenticRetrievalHandler);
```

- [ ] **Step 4: Pass the table from the root stack**

In `packages/infra/lib/stack.ts`, add to the `GraphRAGMessagesStack` props (after line 79, `faqKnowledgeBaseId: ...`):

```typescript
        faqUrlTable: graphRAGStack.faqUrlTable,
```

- [ ] **Step 5: Verify the CDK app synthesizes**

Run: `cd packages/infra && AWS_PROFILE=wisco AWS_REGION=us-east-1 bunx cdk synth -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --quiet`
Expected: synth succeeds (no TypeScript or CDK errors). If `bun run bundle` is required before synth (Docker bundling), run `bun run bundle` first.

- [ ] **Step 6: Commit**

```bash
git add packages/graphrag/infra/graphrag-messages-stack.ts packages/infra/lib/stack.ts
git commit -m "feat(infra): grant FaqUrlTable read + env var to agentic retrieval"
```

---

### Task 13: FAQ-URL table seed script

**Files:**
- Create: `scripts/graphrag/seed_faq_url_table.py`
- (Uses `scripts/graphrag/faq_url_map.py` from Task 4 and `documents/faqs.json`.)

- [ ] **Step 1: Implement the seed CLI**

Create `scripts/graphrag/seed_faq_url_table.py`:

```python
"""Seed the FaqUrlTable from documents/faqs.json.

Builds the normalized-question -> source_url map (with fuzzy recovery) and
upserts one item per normalized question into the DynamoDB table. Idempotent:
re-running overwrites items in place.

Usage:
    AWS_REGION=us-east-1 AWS_PROFILE=wisco python scripts/graphrag/seed_faq_url_table.py \\
        --table <FaqUrlTableName> --faqs documents/faqs.json
    # --dry-run prints counts without writing.

Find the table name from stack outputs:
    aws cloudformation describe-stacks --stack-name WisconsinBotGraphRAG \\
        --profile wisco --region us-east-1 \\
        --query "Stacks[0].Outputs[?contains(OutputKey,'FaqUrlTable')].OutputValue" --output text
"""

from __future__ import annotations

import argparse
import json
import logging
import os

import boto3

from faq_url_map import build_url_map  # when run from scripts/graphrag/
# If invoked from repo root with the package layout, fall back:
# from scripts.graphrag.faq_url_map import build_url_map

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", required=True, help="FaqUrlTable name")
    parser.add_argument("--faqs", default="documents/faqs.json")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    with open(args.faqs) as f:
        records = json.load(f)
    url_map = build_url_map(records)
    by_question = url_map["by_question"]
    logger.info("Loaded %d FAQ records -> %d unique normalized questions",
                len(records), len(by_question))

    if args.dry_run:
        logger.info("[DRY] would upsert %d items into %s", len(by_question), args.table)
        for nq, url in list(by_question.items())[:5]:
            logger.info("[DRY]   %r -> %s", nq[:60], url)
        return 0

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    table = session.resource("dynamodb", region_name=args.region).Table(args.table)
    written = 0
    with table.batch_writer() as batch:
        for nq, url in by_question.items():
            batch.put_item(Item={"normalized_question": nq, "source_url": url})
            written += 1
    logger.info("Upserted %d items into %s", written, args.table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test the seed script in dry-run (no AWS calls)**

Run: `cd scripts/graphrag && uv run python seed_faq_url_table.py --table dummy --faqs ../../documents/faqs.json --dry-run`
Expected: logs "Loaded 638 FAQ records -> ~633 unique normalized questions" and "[DRY] would upsert ... items". No exceptions.

- [ ] **Step 3: Commit**

```bash
git add scripts/graphrag/seed_faq_url_table.py
git commit -m "feat(graphrag): FaqUrlTable seed script from faqs.json"
```

---

### Task 14: Keep the table current during FAQ refresh

**Files:**
- Modify: `scripts/graphrag/extract_faq_qa_pairs.py` (add `--faq-url-table` upsert in the write phase, ~lines 302-320)

Context: the extract script already holds `(source_url, q, a)` for every scraped FAQ. When `--faq-url-table` is supplied, upsert each into the table during the write loop, so future scrapes keep links current without a lambda redeploy.

- [ ] **Step 1: Add the CLI arg**

In `scripts/graphrag/extract_faq_qa_pairs.py`, in `main()` argparse block (near line 277), add:

```python
    parser.add_argument(
        "--faq-url-table",
        default=None,
        help="If set, upsert normalized-question -> source_url into this DynamoDB table",
    )
```

- [ ] **Step 2: Upsert during the write phase**

In the write phase (after the boto3 `s3` session is created, ~line 303), add a table handle and write into it inside the existing `for source_url, q, a in records:` loop (~line 308). Reuse the shared normalizer:

```python
    from faq_url_map import normalize_question

    faq_url_table = None
    if args.faq_url_table:
        faq_url_table = session.resource(
            "dynamodb", region_name=args.region
        ).Table(args.faq_url_table)
```

Then inside the loop, after the existing S3 `put_object` (or in the dry-run branch), add:

```python
        if faq_url_table is not None and not args.dry_run:
            faq_url_table.put_item(
                Item={"normalized_question": normalize_question(q), "source_url": source_url}
            )
```

Note: `--region` defaults to `us-west-2` in this script (the FAQ master bucket region), but the table lives in `us-east-1`. Document in the arg help that callers must pass `--region us-east-1` when using `--faq-url-table`, OR add a separate `--table-region` arg defaulting to `us-east-1`. Implement the `--table-region` option to avoid coupling the two:

```python
    parser.add_argument("--table-region", default="us-east-1",
                        help="Region of --faq-url-table (FAQ master bucket is us-west-2)")
```

and use `region_name=args.table_region` when building `faq_url_table`.

- [ ] **Step 3: Verify the script still parses and dry-runs**

Run: `cd scripts/graphrag && uv run python extract_faq_qa_pairs.py --help`
Expected: help text lists `--faq-url-table` and `--table-region`, no import errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/graphrag/extract_faq_qa_pairs.py
git commit -m "feat(graphrag): extract_faq_qa_pairs can refresh FaqUrlTable"
```

---

## Part D — Verify & deploy

### Task 15: Full test sweep

- [ ] **Step 1: Run all Python tests touched by this work**

Run:
```bash
uv run pytest packages/graphrag/lambdas/test/ packages/messages/lambdas/test/ scripts/graphrag/tests/test_faq_url_map.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run frontend tests**

Run: `cd packages/webapp && bun test src/components/documents/document-card/test/faq-card.test.tsx src/stores/test/chat-store.test.ts`
Expected: all PASS.

- [ ] **Step 3: Run CDK infra tests**

Run: `cd packages/infra && bunx jest`
Expected: all PASS.

- [ ] **Step 4: Lint**

Run: `uv run ruff check scripts/graphrag/ packages/graphrag/lambdas/agentic_retrieval/ packages/messages/lambdas/resource_streaming/ packages/shared/lambda_layers/`
Run: `bunx eslint packages/messages/types/message-types.ts packages/webapp/src/components/documents/document-card/faq-card.tsx packages/webapp/src/stores/types.ts`
Expected: no errors. Fix any reported issues at the root cause (do not `--no-verify`).

- [ ] **Step 5: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes for FAQ + case-law external links"
```

---

### Task 16: Deploy to the us-east-1 GraphRAG test stack and seed

> Deployment only — runs against the us-east-1 test stack, never production (us-west-2). Confirm with the user before deploying.

- [ ] **Step 1: Bundle + diff**

Run:
```bash
bun run bundle
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 bunx cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
```
Expected: only additive changes — a new `FaqUrlTable`, a new IAM grant + env var on the agentic retrieval Lambda, and updated Lambda code. No deletions/replacements of Neptune, buckets, or other tables.

- [ ] **Step 2: Deploy**

Run:
```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 bunx cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```
Expected: success; note the `FaqUrlTableName` output.

- [ ] **Step 3: Seed the table**

Run (substitute the actual table name from stack outputs):
```bash
TABLE=$(aws cloudformation describe-stacks --stack-name WisconsinBotGraphRAG \
  --profile wisco --region us-east-1 \
  --query "Stacks[0].Outputs[?contains(OutputKey,'FaqUrlTable')].OutputValue" --output text)
cd scripts/graphrag && AWS_REGION=us-east-1 AWS_PROFILE=wisco uv run python seed_faq_url_table.py \
  --table "$TABLE" --faqs ../../documents/faqs.json
```
Expected: "Upserted ~633 items".

- [ ] **Step 4: Manual end-to-end smoke (frontend)**

In the deployed web app:
- Ask a known FAQ question (e.g. an agricultural-forest classification question) → the FAQ card shows a **"View on revenue.wi.gov"** link that opens the correct `*.aspx` page.
- Ask a question that surfaces case law → the case card's **View Case** opens Google Scholar for the citation (not an S3 text file).
- Ask a question answered by WPAM or a statute → the **View WPAM / View Statute** button still opens the S3 PDF at the correct page (`#page=N`). This confirms we did not regress the page-anchored path.

- [ ] **Step 5: Record the result**

Note in the PR description which three checks passed and paste the `cdk diff` summary confirming additive-only changes.

---

## Self-Review notes (already applied)

- **Spec coverage:** Part A (Tasks 1-3) = case-law → Scholar; Part B (Tasks 4-10) = FAQ URL table + schema thread + card; Part C (Tasks 11-14) = infra + seed + refresh; Part D (Tasks 15-16) = verify + deploy. Every spec section maps to a task.
- **Normalization consistency:** the same normalization rule appears in `faq_url_map.normalize_question` (Task 4) and `main._normalize_faq_question` (Task 9) — both must stay byte-identical; the plan states this explicitly.
- **Field naming:** `source_url` (Python) ↔ `sourceUrl` (wire/TS) consistently; PK is `normalized_question` in CDK (Task 11), seed (Task 13), refresh (Task 14), and lambda lookup (Task 9).
- **Graceful degradation:** orphan FAQs and lookup errors return `None` → no link button, matching today's behavior.
