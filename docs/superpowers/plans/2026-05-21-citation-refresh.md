# Citation URL Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace eagerly-minted, hour-long S3 presigned URLs with on-demand resolution via a Cognito-authed `GET /citation` endpoint, so citation cards in restored chat sessions remain clickable indefinitely while individual resolved URLs expire in 15 minutes.

**Architecture:** `RAGDocument` and the WebSocket `SourceDocument` carry stable references (`s3_key`, `start_page`, `end_page`) instead of presigned URLs. The agentic-retrieval Lambda stops minting URLs at retrieval time. A new lightweight `citation_resolver` Lambda lives in the sessions package, sharing the existing HTTP API and Cognito JWT authorizer (extended to also accept the token from `?token=`). Frontend `DocumentCard.handleSourceClick` opens a popup synchronously (to satisfy popup blockers), then redirects it to the resolver URL once the JWT resolves; the resolver `head_object`s the key, mints a 15-minute presigned URL, and returns a `302` with `Cache-Control: no-store`.

**Tech Stack:** Python 3.12 (Lambdas), Pydantic v2 (shared types layer), boto3 (S3 presigning), AWS CDK v2 (TypeScript), API Gateway HTTP API v2 with `HttpJwtAuthorizer`, Next.js 14 / React (frontend), Zod (frontend WebSocket schema), pytest + Jest.

**Reference spec:** `docs/superpowers/specs/2026-05-21-citation-refresh-design.md`

---

## Task 1: Extend shared Pydantic types with citation references

**Files:**
- Modify: `packages/shared/lambda_layers/step_function_types/models.py`
- Modify: `packages/shared/lambda_layers/websocket_utils/models.py`

The shared types layer is the contract between every lambda in the pipeline. Adding the three optional fields here is the foundation for every later task. Order matters: this task must merge first because tasks 2, 4, 5, and 6 all consume these fields.

- [ ] **Step 1: Add `s3_key`, `start_page`, `end_page` to `RAGDocument`**

Edit `packages/shared/lambda_layers/step_function_types/models.py`. The existing class body (around lines 59-71) becomes:

```python
class RAGDocument(BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = Field(default=None)
    source_url: str | None = Field(default=None)
    discovery_tag: str = Field(default="unknown")
    # Optional: 1=Constitution, 2=Statute, 3=CaseLaw, 4=AdminRule, 5=WPAM,
    # 6=FAQ, 7=GovPub, 8=IAAO, 9=USPAP. Drives the AuthorityBadge color in
    # the frontend. Stored on every Document node in Neptune; populated by
    # _build_rag_documents / _build_opinion_card so non-FAQ cards render
    # with their authority pill (FAQs hard-code level 6 client-side).
    authority_level: int | None = Field(default=None)
    # Stable S3 reference resolved at click time by the citation_resolver
    # Lambda. Replaces eager presigned URLs so restored sessions stay
    # clickable indefinitely while a copied URL still expires in 15 min.
    s3_key: str | None = Field(default=None)
    start_page: int | None = Field(default=None)
    end_page: int | None = Field(default=None)
```

- [ ] **Step 2: Add the same fields to `SourceDocument`**

Edit `packages/shared/lambda_layers/websocket_utils/models.py`. The class body (around lines 36-43) becomes:

```python
class SourceDocument(WebSocketMessage):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"
    authority_level: int | None = None
    # Mirror of RAGDocument: stable S3 reference + page range for click-time
    # resolution by the citation_resolver Lambda. Camel-case aliasing makes
    # these s3Key / startPage / endPage on the wire.
    s3_key: str | None = None
    start_page: int | None = None
    end_page: int | None = None
```

- [ ] **Step 3: Verify no syntax errors with a quick import**

Run from repo root:

```bash
uv run python -c "from packages.shared.lambda_layers.step_function_types.models import RAGDocument; print(RAGDocument(document_id='x', title='t', content='c').model_dump(by_alias=True))"
```

Expected: prints a dict containing `s3Key: None, startPage: None, endPage: None`.

- [ ] **Step 4: Commit**

```bash
git add packages/shared/lambda_layers/step_function_types/models.py packages/shared/lambda_layers/websocket_utils/models.py
git commit -m "feat(shared): add s3_key/start_page/end_page to RAGDocument and SourceDocument"
```

---

## Task 2: Stop minting presigned URLs in agentic_retrieval

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:1240-1271` (`_generate_source_links`)
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:1447-1487` (`_build_opinion_card`)
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:1490-1581` (`_build_rag_documents`)
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:531-586` (`save_chat_history`)
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py:67-72` (module-level S3 client + env)

The agent loop stops generating URLs entirely. It now passes `s3_key`/`start_page`/`end_page` on `RAGDocument`. The `s3_client` boto3 import and `PRESIGNED_URL_EXPIRY` env var are no longer used by `main.py` and are removed. (S3 read access is still needed by `case_opinion.py` for `fetch_case_opinion`, which `GET`s the case-law `.txt` to feed text into the prompt — that's a different code path and stays.)

- [ ] **Step 1: Look at the existing tests for these functions**

Run:

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v 2>&1 | head -60
```

Expected: tests pass (the goal is to know which assertions exist before we change behavior).

- [ ] **Step 2: Update tests to assert the new contract**

Edit `packages/graphrag/lambdas/test/test_agentic_retrieval.py`. Find any assertion checking `source_url` against an S3 presigned URL pattern (look for `s3.amazonaws.com`, `X-Amz-Signature`, `#page=`, or hardcoded presigned-URL fixtures). Replace those assertions with:

- For PDF citations: assert `rag_doc.s3_key == "raw/<expected-key>.pdf"` and `rag_doc.start_page == <expected-page>`.
- For case-law `.txt` citations from `_build_opinion_card`: assert `rag_doc.s3_key == "raw/case-law-<slug>/<slug>.txt"` (or whatever the existing fixture's `raw_key` is) and `rag_doc.start_page is None`.
- For public gov-website citations: assert `rag_doc.source_url == "<gov-url>"` and `rag_doc.s3_key is None`.

If you can't find an existing test that exercises `_generate_source_links` directly, add this minimal one near the top of the file (after the imports, before `test_collapse_case_law_by_title_merges_parallel_citations`):

```python
def test_generate_source_label_returns_label_only():
    """_generate_source_label returns the display label; no URL minting."""
    from agentic_retrieval.main import _generate_source_label

    chunk = {
        "s3_key": "raw/wpam/wpam.pdf",
        "start_page": 12,
        "end_page": 14,
        "source_url": "https://www.revenue.wi.gov/dor-publications/wpam.pdf",
    }
    doc_info = {"title": "Wisconsin Property Assessment Manual"}

    label = _generate_source_label(chunk, doc_info)
    assert label == "https://www.revenue.wi.gov/dor-publications/wpam.pdf"


def test_generate_source_label_falls_back_to_doc_title():
    from agentic_retrieval.main import _generate_source_label

    label = _generate_source_label({"s3_key": "raw/wpam/wpam.pdf"}, {"title": "WPAM"})
    assert label == "WPAM"
```

- [ ] **Step 3: Run the tests, see them fail**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v 2>&1 | tail -30
```

Expected: failures referencing `_generate_source_label` (does not exist yet) and any updated `source_url` assertions.

- [ ] **Step 4: Replace `_generate_source_links` with `_generate_source_label`**

Edit `packages/graphrag/lambdas/agentic_retrieval/main.py`. Replace the entire body of `_generate_source_links` (lines 1240-1271) with:

```python
def _generate_source_label(chunk: dict, doc_info: dict | None) -> str:
    """Return the display label shown on the citation badge.

    Replaces _generate_source_links: URL construction now happens at click
    time in the citation_resolver Lambda. Cards carry stable s3_key /
    start_page on the RAGDocument; the badge label still uses the gov
    source_url (when present) so users see something semantically
    meaningful, not the doc title.
    """
    gov_source_url = chunk.get("source_url") or (doc_info or {}).get("source_url") or ""
    return gov_source_url or (doc_info or {}).get("title", "")
```

- [ ] **Step 5: Update `_build_rag_documents` to set s3_key/start_page/end_page and use the new label**

Same file. The two call sites that today destructure `(source, source_url) = _generate_source_links(...)` (lines 1516, 1529, 1553) need to change. Replace the inner block of `_build_rag_documents` from `for chunk in chunks:` (line 1507) through the end of the for-loop body that closes at the `else` branch (around line 1538) with:

```python
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        chunk_text = chunk.get("text") or ""
        tag = discovery.get(doc_id, "unknown")

        if doc_id not in docs_by_id:
            doc_info = neptune.get_document(doc_id)
            title = (doc_info.get("title") if doc_info else None) or doc_id
            content_hash = hashlib.sha256(doc_id.encode()).hexdigest()[:7]
            label = _generate_source_label(chunk, doc_info)
            gov_url = chunk.get("source_url") or (doc_info or {}).get("source_url")
            s3_key = chunk.get("s3_key") or (doc_info or {}).get("s3_key")

            docs_by_id[doc_id] = RAGDocument(
                document_id=f"{doc_id}-{content_hash}",
                title=title,
                content=chunk_text,
                source=label,
                # source_url now only carries public gov URLs. S3 references
                # ride on s3_key / start_page / end_page; the resolver mints
                # the presigned URL at click time.
                source_url=gov_url,
                s3_key=s3_key,
                start_page=chunk.get("start_page"),
                end_page=chunk.get("end_page"),
                discovery_tag=tag,
                authority_level=(doc_info or {}).get("authority_level"),
            )
        else:
            existing = docs_by_id[doc_id]
            docs_by_id[doc_id] = RAGDocument(
                document_id=existing.document_id,
                title=existing.title,
                content=existing.content + "\n\n" + chunk_text,
                source=existing.source,
                source_url=existing.source_url,
                # First chunk wins for the s3 reference (chunks of the same
                # doc all point to the same PDF; pick the lowest start_page
                # only if the existing one is None).
                s3_key=existing.s3_key or chunk.get("s3_key"),
                start_page=existing.start_page or chunk.get("start_page"),
                end_page=existing.end_page or chunk.get("end_page"),
                discovery_tag=existing.discovery_tag,
                authority_level=existing.authority_level,
            )
```

Then update the "no chunks" branch (around line 1553) — the block under `for doc_id in doc_ids - docs_by_id.keys():`:

```python
        label = _generate_source_label({}, doc_info)
        docs_by_id[doc_id] = RAGDocument(
            document_id=f"{doc_id}-{content_hash}",
            title=doc_info.get("title") or doc_id,
            content=doc_info.get("summary") or "",
            source=label,
            source_url=doc_info.get("source_url"),
            s3_key=doc_info.get("s3_key"),
            start_page=None,
            end_page=None,
            discovery_tag=tag,
            authority_level=doc_info.get("authority_level"),
        )
```

- [ ] **Step 6: Update `_build_opinion_card` to drop URL minting**

Same file, replace the body of `_build_opinion_card` (lines 1447-1487) with:

```python
def _build_opinion_card(stub_doc_id: str, payload: dict) -> RAGDocument:
    """Build a RAGDocument for a fetched full court opinion.

    Supersedes the one-chunk case-law stub card for this citation. The
    resolver mints the presigned URL to the .txt at click time; this
    function only carries the stable s3 reference. scholar_url remains
    available on chunk metadata as a public fallback when the bot
    surfaces the case but the .txt isn't in S3.
    """
    citation = payload.get("citation", "")
    raw_key = payload.get("raw_key", "")
    opinion_text = payload.get("text", "")
    scholar_url = payload.get("scholar_url", "")

    doc_info = neptune.get_document(stub_doc_id) or {}
    title = doc_info.get("title") or citation or stub_doc_id
    content_hash = hashlib.sha256(stub_doc_id.encode()).hexdigest()[:7]

    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        # When raw_key is empty (S3 lookup miss in fetch_case_opinion),
        # fall back to the Google Scholar URL so the card is still
        # clickable. The resolver isn't involved when s3_key is None.
        source_url=None if raw_key else scholar_url,
        s3_key=raw_key or None,
        start_page=None,
        end_page=None,
        discovery_tag="opinion-fetched",
        authority_level=doc_info.get("authority_level") if doc_info else 3,
    )
```

- [ ] **Step 7: Update `save_chat_history` to persist s3Key/startPage/endPage**

Same file, the resource-building block in `save_chat_history` (lines 553-565) becomes:

```python
        resources: list[dict] = []
        if rag_documents:
            for doc in rag_documents:
                data: dict = {
                    "documentId": doc.document_id,
                    "title": doc.title,
                    "source": doc.source,
                    "discoveryTag": doc.discovery_tag,
                }
                if doc.source_url is not None:
                    data["sourceUrl"] = doc.source_url
                if doc.s3_key is not None:
                    data["s3Key"] = doc.s3_key
                if doc.start_page is not None:
                    data["startPage"] = doc.start_page
                if doc.end_page is not None:
                    data["endPage"] = doc.end_page
                resources.append({"type": "document", "data": data})
```

- [ ] **Step 8: Remove unused module-level S3 client and env var**

Same file. Inspect lines 67-72:

```python
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3_client = boto3.client("s3", region_name=REGION)
neptune = NeptuneClient()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
PRESIGNED_URL_EXPIRY = int(os.environ.get("PRESIGNED_URL_EXPIRY", "3600"))
```

`s3_client` is no longer referenced from `main.py`. Search the file to confirm:

```bash
grep -n "s3_client" packages/graphrag/lambdas/agentic_retrieval/main.py
```

Expected: zero matches after step 4-6 are applied (only the module-level definition).

`RAW_BUCKET` is still used: `case_opinion.fetch_case_opinion` is called with `raw_bucket=RAW_BUCKET` from `tools.py`. Keep `RAW_BUCKET`. Delete the `s3_client = ...` line and the `PRESIGNED_URL_EXPIRY = ...` line. Keep `RAW_BUCKET`.

- [ ] **Step 9: Run the agentic-retrieval tests**

```bash
uv run pytest packages/graphrag/lambdas/test/test_agentic_retrieval.py -v 2>&1 | tail -30
```

Expected: PASS. If a test fails because it asserted on the old `source_url` containing a presigned URL, fix the assertion to match the new contract (assert on `s3_key`/`start_page`).

- [ ] **Step 10: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat(graphrag): pass s3_key/start_page on RAGDocument instead of presigned URLs"
```

---

## Task 3: Forward citation refs through resource_streaming

**Files:**
- Modify: `packages/messages/lambdas/resource_streaming/main.py:114-127`
- Modify: `packages/messages/lambdas/test/test_resource_streaming.py`

The `SourceDocument` constructor inside `_stream_resources_async` builds the WebSocket payload. It currently passes through `source_url` but not the new fields. Pure forwarding change.

- [ ] **Step 1: Add a failing test for s3Key forwarding**

Open `packages/messages/lambdas/test/test_resource_streaming.py` and find the test that constructs a `RAGDocument` with `source_url` (around line 267). Add an assertion that the resulting `SourceDocument` also carries `s3_key`. Or add a fresh test:

```python
def test_source_document_carries_s3_key_and_pages(self):
    """resource_streaming forwards s3_key/start_page/end_page to the WebSocket payload."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from resource_streaming.main import _stream_resources_async
    from step_function_types.models import (
        DocumentResource,
        RAGDocument,
        StreamResourcesJob,
    )

    rag_doc = RAGDocument(
        document_id="doc-001",
        title="WPAM",
        content="some content",
        source="https://www.revenue.wi.gov/wpam.pdf",
        source_url="https://www.revenue.wi.gov/wpam.pdf",
        s3_key="raw/wpam/wpam.pdf",
        start_page=12,
        end_page=14,
    )
    job = StreamResourcesJob(
        query_id="q-1",
        session_id="s-1",
        documents=DocumentResource(documents=[rag_doc]),
    )

    sent_messages = []
    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

    asyncio.run(_stream_resources_async(job, ws))

    assert len(sent_messages) == 1
    sent_doc = sent_messages[0].content.documents[0]
    assert sent_doc.s3_key == "raw/wpam/wpam.pdf"
    assert sent_doc.start_page == 12
    assert sent_doc.end_page == 14
```

- [ ] **Step 2: Run the test, see it fail**

```bash
uv run pytest packages/messages/lambdas/test/test_resource_streaming.py::TestResourceStreaming::test_source_document_carries_s3_key_and_pages -v
```

Expected: FAIL because `SourceDocument` constructed at line 117 doesn't pass `s3_key` etc.

- [ ] **Step 3: Pass the new fields through**

Edit `packages/messages/lambdas/resource_streaming/main.py` around line 116-127:

```python
        source_documents = [
            SourceDocument(
                document_id=doc.document_id,
                title=doc.title,
                content=doc.content,
                source=doc.source,
                source_url=doc.source_url,
                discovery_tag=doc.discovery_tag,
                authority_level=doc.authority_level,
                s3_key=doc.s3_key,
                start_page=doc.start_page,
                end_page=doc.end_page,
            )
            for doc in documents_resource.documents
        ]
```

- [ ] **Step 4: Run the test, see it pass; run the full file to catch regressions**

```bash
uv run pytest packages/messages/lambdas/test/test_resource_streaming.py -v
```

Expected: PASS for all tests.

- [ ] **Step 5: Commit**

```bash
git add packages/messages/lambdas/resource_streaming/main.py packages/messages/lambdas/test/test_resource_streaming.py
git commit -m "feat(messages): forward s3_key/start_page/end_page through resource_streaming"
```

---

## Task 4: Persist citation refs in legacy chat history writer

**Files:**
- Modify: `packages/messages/lambdas/streaming/main.py:78-100` (`log_chat_history`)

`packages/messages/lambdas/streaming/main.py::log_chat_history` runs at end-of-turn for both retrieval paths. The legacy/non-graphrag streaming Lambda also writes to the same chat history table. Mirror the same field set we wrote in agentic_retrieval `save_chat_history` (Task 2 step 7) so restored sessions get a consistent shape regardless of which Lambda authored them.

- [ ] **Step 1: Update the resource-building block**

Edit `packages/messages/lambdas/streaming/main.py` around lines 78-100:

```python
    resources = []
    if documents:
        for doc in documents.documents:
            data: dict = {"documentId": doc.document_id, "title": doc.title}
            if doc.content:
                data["content"] = doc.content[:CONTENT_PREVIEW_LIMIT]
            if doc.source is not None:
                data["source"] = doc.source
            if doc.source_url is not None:
                data["sourceUrl"] = doc.source_url
            if doc.discovery_tag:
                data["discoveryTag"] = doc.discovery_tag
            if doc.s3_key is not None:
                data["s3Key"] = doc.s3_key
            if doc.start_page is not None:
                data["startPage"] = doc.start_page
            if doc.end_page is not None:
                data["endPage"] = doc.end_page
            resources.append({"type": "document", "data": data})
```

- [ ] **Step 2: Run streaming tests**

```bash
uv run pytest packages/messages/lambdas/test/test_streaming.py -v
```

Expected: PASS. (No assertion changes needed — these tests don't fixture `s3_key` today, and the new fields are additive optional.)

- [ ] **Step 3: Commit**

```bash
git add packages/messages/lambdas/streaming/main.py
git commit -m "feat(messages): persist s3_key/start_page/end_page in chat history rows"
```

---

## Task 5: Drop `PRESIGNED_URL_EXPIRY` from CDK env

**Files:**
- Modify: `packages/graphrag/infra/graphrag-messages-stack.ts:57-74`

Now that no code path reads `PRESIGNED_URL_EXPIRY`, drop it from the env so a future reader doesn't think it controls anything. Keep `RAW_BUCKET` and the `s3:GetObject` grant — `case_opinion.fetch_case_opinion` still GETs the case-law `.txt` from S3 to feed text into the prompt.

- [ ] **Step 1: Inspect the existing block (already in your context, lines 57-74). The current `environment` map does not contain a `PRESIGNED_URL_EXPIRY` key.**

Verify:

```bash
grep -n "PRESIGNED_URL_EXPIRY" packages/graphrag/infra/graphrag-messages-stack.ts
```

Expected: zero matches. The variable was only ever set inside `agentic_retrieval/main.py` via `os.environ.get("PRESIGNED_URL_EXPIRY", "3600")` with a default. **No CDK change is needed** — this task is a no-op verification.

- [ ] **Step 2: Verify the s3:GetObject grant is intact**

```bash
grep -n "s3:GetObject" packages/graphrag/infra/graphrag-messages-stack.ts
```

Expected: matches around line 115 (the existing grant). Leave it. Update the inline comment from "S3 read permissions for presigned URL generation on raw documents" to "S3 read permissions for fetch_case_opinion to load case-law .txt files":

Edit `packages/graphrag/infra/graphrag-messages-stack.ts`, line 111:

```typescript
    // S3 read permissions: fetch_case_opinion GETs case-law .txt files
    // to feed full opinion text into the agent prompt. Citation URLs are
    // minted by the citation_resolver Lambda, not here.
```

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/infra/graphrag-messages-stack.ts
git commit -m "docs(graphrag): clarify s3:GetObject grant is for fetch_case_opinion"
```

---

## Task 6: Create the citation_resolver Lambda

**Files:**
- Create: `packages/sessions/lambdas/citation_resolver/__init__.py`
- Create: `packages/sessions/lambdas/citation_resolver/main.py`
- Create: `packages/sessions/lambdas/citation_resolver/requirements.txt`
- Create: `packages/sessions/lambdas/test/test_citation_resolver.py`

The resolver is a tiny self-contained Lambda. No Pydantic, no aws-lambda-powertools router, no shared layers — just boto3 and a `head_object` + `generate_presigned_url` call. Validates `s3Key` starts with `raw/`, validates `page` is a positive int, returns 302 with `Cache-Control: no-store`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sessions/lambdas/test/test_citation_resolver.py`:

```python
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "citation_resolver"))


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_redirects_with_page_fragment():
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.return_value = {}
        mock_s3.generate_presigned_url.return_value = "https://test-bucket.s3.amazonaws.com/raw/wpam/wpam.pdf?signature=abc"

        event = {"queryStringParameters": {"s3Key": "raw/wpam/wpam.pdf", "page": "12"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 302
    assert response["headers"]["Location"].endswith("#page=12")
    assert response["headers"]["Cache-Control"] == "no-store"
    mock_s3.head_object.assert_called_once_with(Bucket="test-bucket", Key="raw/wpam/wpam.pdf")


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_redirects_without_page_fragment():
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.return_value = {}
        mock_s3.generate_presigned_url.return_value = "https://test-bucket.s3.amazonaws.com/raw/case/x.txt?sig=z"

        event = {"queryStringParameters": {"s3Key": "raw/case/x.txt"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 302
    assert "#page" not in response["headers"]["Location"]


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_s3_key_outside_raw_prefix():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "work/something.pdf"}}
    response = handler(event, MagicMock())

    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_missing_s3_key():
    from citation_resolver.main import handler

    response = handler({"queryStringParameters": {}}, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_non_integer_page():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "raw/x.pdf", "page": "twelve"}}
    response = handler(event, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_zero_page():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "raw/x.pdf", "page": "0"}}
    response = handler(event, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_returns_404_when_object_missing():
    from botocore.exceptions import ClientError
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        event = {"queryStringParameters": {"s3Key": "raw/wpam/missing.pdf"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 404


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_returns_404_for_no_such_key():
    from botocore.exceptions import ClientError
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "HeadObject"
        )

        event = {"queryStringParameters": {"s3Key": "raw/wpam/missing.pdf"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 404
```

- [ ] **Step 2: Run tests, see them fail**

```bash
uv run pytest packages/sessions/lambdas/test/test_citation_resolver.py -v
```

Expected: collection error (`citation_resolver.main` does not exist).

- [ ] **Step 3: Create the package files**

Create `packages/sessions/lambdas/citation_resolver/__init__.py` as an empty file:

```bash
mkdir -p packages/sessions/lambdas/citation_resolver
touch packages/sessions/lambdas/citation_resolver/__init__.py
```

Create `packages/sessions/lambdas/citation_resolver/main.py`:

```python
"""Citation Resolver Lambda.

Mints short-lived (15 min) presigned URLs to PDFs in the GraphRAG raw
bucket on demand. Replaces eager URL minting in the agent so citation
cards in restored chat sessions stay clickable indefinitely while a
copied URL still expires within a meeting.

Allow-listed to keys under raw/ to keep accidental access to other
bucket prefixes (work/, embeddings/) impossible.
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]
EXPIRES_IN = 900  # 15 minutes
ALLOWED_PREFIX = "raw/"


def _bad_request(reason: str) -> dict:
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "text/plain", "Cache-Control": "no-store"},
        "body": reason,
    }


def handler(event: dict, _context) -> dict:
    qs = event.get("queryStringParameters") or {}
    s3_key = qs.get("s3Key")
    page = qs.get("page")

    if not s3_key or not s3_key.startswith(ALLOWED_PREFIX):
        return _bad_request("invalid s3Key")

    page_num: int | None = None
    if page is not None:
        try:
            page_num = int(page)
        except ValueError:
            return _bad_request("page must be an integer")
        if page_num < 1:
            return _bad_request("page must be >= 1")

    try:
        s3.head_object(Bucket=RAW_BUCKET, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            logger.info(f"citation key not found: {s3_key}")
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "text/plain", "Cache-Control": "no-store"},
                "body": "not found",
            }
        # Any other ClientError (throttle, perms, 5xx) is a real failure.
        logger.error(f"head_object failed for {s3_key}: {code}", exc_info=True)
        raise

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": RAW_BUCKET, "Key": s3_key},
        ExpiresIn=EXPIRES_IN,
    )
    if page_num:
        url = f"{url}#page={page_num}"

    return {
        "statusCode": 302,
        "headers": {
            "Location": url,
            # Browsers and CDNs MUST NOT cache the redirect. Otherwise a
            # second click after expiry would still get the dead URL.
            "Cache-Control": "no-store",
        },
        "body": "",
    }
```

Create `packages/sessions/lambdas/citation_resolver/requirements.txt`:

```text
boto3==1.40.7
botocore==1.40.7
```

- [ ] **Step 4: Run tests, see them pass**

```bash
uv run pytest packages/sessions/lambdas/test/test_citation_resolver.py -v
```

Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sessions/lambdas/citation_resolver/ packages/sessions/lambdas/test/test_citation_resolver.py
git commit -m "feat(sessions): add citation_resolver Lambda for on-demand presigned URLs"
```

---

## Task 7: Wire citation_resolver into the Sessions stack

**Files:**
- Modify: `packages/sessions/infra/sessions-stack.ts`
- Modify: `packages/infra/lib/stack.ts` (pass `rawBucketName` to `SessionsStack`)
- Modify: `bundles.toml` (add `citation_resolver` to bundling list — confirm by reading)

The resolver lives in the sessions package because it shares the existing HTTP API surface and Cognito user pool. The HTTP API authorizer is reused but its `IdentitySource` is extended to also accept the JWT from `?request.querystring.token` — required because `window.open()` cannot attach an `Authorization` header.

- [ ] **Step 1: Add citation_resolver to bundles.toml**

`bundles.toml` is explicit (not glob-based). Append a new `[[bundles]]` block at the end of the file:

```toml
[[bundles]]
dest = "citation_resolver"
sources = ["./packages/sessions/lambdas/citation_resolver"]
```

- [ ] **Step 2: Add the new Lambda + route to `sessions-stack.ts`**

Edit `packages/sessions/infra/sessions-stack.ts`. First, extend the props at line 12:

```typescript
export interface SessionsStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  // The raw GraphRAG bucket. citation_resolver mints presigned URLs
  // against keys under raw/ on demand.
  rawBucketName: string;
}
```

Then, after the existing `apiHandler` definition (after line 156), add:

```typescript
    const citationResolverHandler = new lambda.Function(this, 'CitationResolverHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/citation_resolver', {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash',
            '-c',
            [
              'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output',
              'cp -r . /asset-output',
            ].join(' && '),
          ],
        },
      }),
      description: 'Mints short-lived presigned URLs for citation clicks',
      timeout: cdk.Duration.seconds(5),
      memorySize: 128,
      environment: {
        RAW_BUCKET: props.rawBucketName,
        LOG_LEVEL: 'INFO',
      },
    });

    citationResolverHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:HeadObject'],
        // Allow-list to raw/ — work/, embeddings/, and other bucket
        // prefixes are NOT user-accessible. Defense in depth on top of
        // the in-Lambda startswith("raw/") check.
        resources: [`arn:aws:s3:::${props.rawBucketName}/raw/*`],
      })
    );
```

- [ ] **Step 3: Extend the JWT authorizer to read from `?token=`**

Same file, around line 356-364. Replace the `HttpJwtAuthorizer` instantiation with:

```typescript
    const authorizer = new apigatewayv2Authorizers.HttpJwtAuthorizer(
      'CognitoAuthorizer',
      `https://cognito-idp.${cdk.Stack.of(this).region}.amazonaws.com/${
        this.userPool.userPoolId
      }`,
      {
        jwtAudience: [this.userPoolClient.userPoolClientId],
        // Default identity source is "$request.header.Authorization".
        // The citation resolver is invoked by window.open() which cannot
        // set custom headers, so we accept the token from a query param
        // as well. The header form is preferred when present.
        identitySource: [
          '$request.header.Authorization',
          '$request.querystring.token',
        ],
      }
    );
```

- [ ] **Step 4: Add the `/citation` route**

Same file, after the existing `httpApi.addRoutes(...)` blocks (after line 427), add:

```typescript
    httpApi.addRoutes({
      path: '/citation',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: new apigatewayv2Integrations.HttpLambdaIntegration(
        'CitationResolverIntegration',
        citationResolverHandler
      ),
      authorizer: authorizer,
    });
```

- [ ] **Step 5: Pass `rawBucketName` from the root stack**

Edit `packages/infra/lib/stack.ts`. The challenge: `SessionsStack` is currently instantiated at line 22, *before* `GraphRAGStack` (line 55) which owns `rawBucketName`. Reorder so GraphRAG is constructed first, then pass the bucket name.

Replace lines 22-27 (the current `SessionsStack` instantiation) with — after the GraphRAGStack instantiation — a deferred initialization:

The cleanest fix is to move the `GraphRAGStack` construction to before `SessionsStack`. Edit `packages/infra/lib/stack.ts`. Move the `const graphRAGStack = new GraphRAGStack(...)` block (currently lines 55-58) to immediately after `lambdaLayersStack` (after line 20). The new ordering:

```typescript
    const lambdaLayersStack = new LambdaLayersStack(this, 'LambdaLayersStack', {
      description: 'Shared lambda layers for the Wisconsin bot.',
    });

    // GraphRAG infra (Neptune + S3 buckets) must come before SessionsStack
    // so the citation_resolver can wire its env var to the raw bucket.
    const graphRAGStack = new GraphRAGStack(this, 'WisconsinGraphRAGStack', {
      description:
        'Stack providing GraphRAG services (Neptune Analytics + S3).',
    });

    const sessionsStack = new SessionsStack(this, 'WisconsinSessionsStack', {
      description:
        'Stack providing API and WebSocket session services for the Wisconsin bot.',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
      websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
      rawBucketName: graphRAGStack.rawBucketName,
    });
```

Then delete the now-duplicate `GraphRAGStack` instantiation (the original block at lines 55-58 in the un-edited file).

- [ ] **Step 6: cdk synth to catch wiring errors**

```bash
bun run bundle && cd packages/infra && AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk synth -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG > /tmp/synth.out 2>&1
echo "exit=$?"
```

Expected: exit=0. If non-zero, read `/tmp/synth.out` and fix.

- [ ] **Step 7: Commit**

```bash
git add packages/sessions/infra/sessions-stack.ts packages/infra/lib/stack.ts bundles.toml
git commit -m "feat(infra): wire citation_resolver Lambda + GET /citation route"
```

---

## Task 8: Extend the WebSocket Zod schema

**Files:**
- Modify: `packages/messages/types/message-types.ts:3-11` (`SourceDocumentSchema`)

Per `CLAUDE.md` "WebSocket Contract": any new field on a backend Pydantic model must also appear in the frontend Zod schema, or every message gets rejected at parse-time.

- [ ] **Step 1: Add the three fields to the schema**

Edit `packages/messages/types/message-types.ts` lines 3-11:

```typescript
export const SourceDocumentSchema = z.object({
  documentId: z.string(),
  title: z.string(),
  content: z.string(),
  source: z.string().optional(),
  sourceUrl: z.string().optional(),
  discoveryTag: z.string().optional(),
  authorityLevel: z.number().optional(),
  // Stable references to the raw S3 object. The frontend sends these to
  // GET /citation at click time; the resolver mints a 15-minute presigned
  // URL and 302-redirects.
  s3Key: z.string().optional(),
  startPage: z.number().int().optional(),
  endPage: z.number().int().optional(),
});
```

- [ ] **Step 2: Run frontend tests + typecheck**

```bash
cd packages/webapp && bun run test 2>&1 | tail -30
```

Expected: PASS. If a test fixture asserts on the exact shape of `SourceDocument`, update it to include the new optional fields (or rely on optional defaults).

- [ ] **Step 3: Commit**

```bash
git add packages/messages/types/message-types.ts
git commit -m "feat(types): extend Zod SourceDocument schema with s3Key/startPage/endPage"
```

---

## Task 9: Update frontend types and chat-api response shape

**Files:**
- Modify: `packages/webapp/src/stores/types.ts:54-62` (`Document` interface)
- Modify: `packages/webapp/src/components/documents/document-card/document-card.tsx:18-32` (`Document` interface)

Two places define a `Document` interface today; keep them aligned.

- [ ] **Step 1: Add fields to `Document` in stores/types.ts**

Edit `packages/webapp/src/stores/types.ts` lines 54-62:

```typescript
export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;
  startPage?: number;
  endPage?: number;
  authorityLevel?: number;
  discoveryTag?: string;
}
```

- [ ] **Step 2: Add fields to `Document` in document-card.tsx**

Edit `packages/webapp/src/components/documents/document-card/document-card.tsx` lines 18-32:

```typescript
export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;
  startPage?: number;
  endPage?: number;
  authorityLevel?: number;
  discoveryTag?:
    | 'vector-search'
    | 'graph-neighbor'
    | 'fetched'
    | 'framework-list'
    | 'opinion-fetched'
    | 'unknown';
}
```

- [ ] **Step 3: Type-check**

```bash
cd packages/webapp && bun run typecheck 2>&1 | tail -20
```

If `typecheck` is not a script, use `bunx tsc --noEmit -p tsconfig.json 2>&1 | tail -20`.

Expected: no new errors. The fields are optional, so existing callsites compile.

- [ ] **Step 4: Commit**

```bash
git add packages/webapp/src/stores/types.ts packages/webapp/src/components/documents/document-card/document-card.tsx
git commit -m "feat(webapp): add s3Key/startPage/endPage to Document type"
```

---

## Task 10: Add the citation-resolver client helper

**Files:**
- Create: `packages/webapp/src/lib/citation-resolver.ts`

Tiny helper that builds the resolver URL with the JWT. Single responsibility: take `(s3Key, page?)`, return a fully-formed URL ready for `popup.location.href`.

- [ ] **Step 1: Create the file**

Create `packages/webapp/src/lib/citation-resolver.ts`:

```typescript
import { getIdToken } from './auth';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL!;
if (!API_BASE_URL) throw new Error('NEXT_PUBLIC_API_BASE_URL is not set');

/**
 * Build a one-shot URL that the user navigates to in a new tab. The
 * resolver Lambda validates the Cognito JWT (carried in ?token= because
 * window.open cannot attach a custom Authorization header), HEAD-checks
 * the s3 key, and 302-redirects to a 15-minute presigned URL with an
 * optional #page=N fragment.
 *
 * Returns null when no JWT is available (signed out). Caller should
 * close the popup it opened synchronously.
 */
export async function buildResolverUrl(
  s3Key: string,
  page?: number
): Promise<string | null> {
  const token = await getIdToken();
  if (!token) return null;

  const params = new URLSearchParams({ s3Key, token });
  if (page && page > 0) {
    params.set('page', String(page));
  }
  const base = API_BASE_URL.replace(/\/+$/, '');
  return `${base}/citation?${params.toString()}`;
}
```

- [ ] **Step 2: Type-check**

```bash
cd packages/webapp && bunx tsc --noEmit -p tsconfig.json 2>&1 | grep citation-resolver
```

Expected: no output (no errors).

- [ ] **Step 3: Commit**

```bash
git add packages/webapp/src/lib/citation-resolver.ts
git commit -m "feat(webapp): add citation-resolver client helper"
```

---

## Task 11: Wire the click handler to use the resolver

**Files:**
- Modify: `packages/webapp/src/components/documents/document-card/document-card.tsx:374-383` (`handleSourceClick`)
- Modify: `packages/webapp/src/components/documents/document-card/document-badge.tsx:6-35` (`disabled` and `onClick` rules)

Click flow when `s3Key` is present:

1. Open a blank popup *synchronously* — required because Chrome/Safari refuse `window.open` after any `await`.
2. Fetch the JWT (async).
3. Set `popup.location.href = <resolver-url>` once the URL is built.
4. If the URL build fails (no token), `popup.close()`.

When `s3Key` is absent and `sourceUrl` is present, open `sourceUrl` directly (the gov-website case).

- [ ] **Step 1: Update the click handler in document-card.tsx**

Edit `packages/webapp/src/components/documents/document-card/document-card.tsx`. Add an import at the top of the file (after the `lucide-react` import on line 13):

```typescript
import { buildResolverUrl } from '@/lib/citation-resolver';
```

Then replace `handleSourceClick` (lines 374-383):

```typescript
  const handleSourceClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();

      if (document.s3Key) {
        // Open the popup synchronously to satisfy popup blockers (Chrome
        // and Safari deny window.open after any await), then redirect it
        // once the JWT is fetched.
        const popup = window.open(
          'about:blank',
          '_blank',
          'noopener,noreferrer'
        );
        if (!popup) return;
        void buildResolverUrl(document.s3Key, document.startPage)
          .then((url) => {
            if (url) {
              popup.location.href = url;
            } else {
              popup.close();
            }
          })
          .catch(() => popup.close());
      } else if (document.sourceUrl) {
        window.open(document.sourceUrl, '_blank', 'noopener,noreferrer');
      }

      onSourceClick?.(document);
    },
    [document, onSourceClick]
  );
```

- [ ] **Step 2: Update the badge to enable for s3Key cards**

Edit `packages/webapp/src/components/documents/document-card/document-badge.tsx`. The component receives `sourceUrl` today; extend it to take both. Replace the `Props` interface and component body to gate on either field. Read the file first to understand the current shape:

```bash
cat packages/webapp/src/components/documents/document-card/document-badge.tsx
```

Then edit so that:
- The interface adds `s3Key?: string` alongside `sourceUrl?: string`.
- The `cursor-pointer` and `disabled` checks become `(sourceUrl || s3Key)` instead of just `sourceUrl`.
- The `aria-label` reads `sourceUrl ?? "Open source"`.

Concretely, replace lines 6-35 of `document-badge.tsx` with:

```tsx
interface DocumentBadgeProps {
  source: string;
  sourceUrl?: string;
  s3Key?: string;
  onSourceClick?: (e: React.MouseEvent) => void;
}

export const DocumentBadge: React.FC<DocumentBadgeProps> = ({
  source,
  sourceUrl,
  s3Key,
  onSourceClick,
}) => {
  const clickable = !!(sourceUrl || s3Key);
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted/80',
        clickable ? 'cursor-pointer' : 'cursor-default opacity-70'
      )}
      onClick={clickable ? onSourceClick : undefined}
      disabled={!clickable}
      aria-label={clickable ? `Open source ${source}` : undefined}
    >
      <span className="truncate max-w-[200px]">{source}</span>
      {clickable && <ExternalLink className="h-3 w-3 flex-shrink-0" />}
    </button>
  );
};
```

(If the existing component differs significantly — e.g. uses different class names — preserve the visual props and only change the `clickable` gating logic. Match the file you actually see.)

- [ ] **Step 3: Wire `s3Key` through to the badge**

In `document-card.tsx`, find the two `<DocumentBadge ... />` usages (lines ~240 and ~304 per the existing grep). Each currently passes `sourceUrl={document.sourceUrl}`; add `s3Key={document.s3Key}` next to it.

- [ ] **Step 4: Type-check + run frontend tests**

```bash
cd packages/webapp && bunx tsc --noEmit -p tsconfig.json && bun run test 2>&1 | tail -20
```

Expected: typecheck clean, tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/webapp/src/components/documents/document-card/document-card.tsx packages/webapp/src/components/documents/document-card/document-badge.tsx
git commit -m "feat(webapp): resolve citation URLs at click time via /citation endpoint"
```

---

## Task 12: Deploy to the GraphRAG test stack

**Files:**
- (no source changes — deployment + smoke test)

Per the user's `project_deployment_safety` memory, GraphRAG changes deploy to us-east-1 (`WisconsinBotGraphRAG`), not us-west-2 prod. Per `feedback_investigate_root_cause`: reproduce end-to-end before declaring success.

- [ ] **Step 1: cdk diff to confirm only the expected additions**

```bash
bun run bundle && cd packages/infra && AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG 2>&1 | tail -60
```

Expected diff:
- New `CitationResolverHandler` Lambda
- New `CitationResolverIntegration`
- New `/citation` route
- Updated `HttpJwtAuthorizer` `IdentitySource` (now includes `$request.querystring.token`)
- New IAM policy for the resolver scoped to `raw/*`

If you see destructive changes (table/queue removals, role recreations), STOP and audit before continuing.

- [ ] **Step 2: Deploy**

```bash
cd packages/infra && AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never 2>&1 | tail -30
```

Expected: deploy succeeds.

- [ ] **Step 3: Smoke test the live system from a browser**

In the deployed webapp:

1. Sign in. Send a question that has a known PDF citation (e.g., something that hits WPAM). Wait for the answer to stream.
2. Click the citation card. Verify the new tab opens the PDF at the correct page.
3. Open browser dev tools Network tab. Click the same citation again — verify the request is `GET /citation?s3Key=...&page=...&token=...`, status `302`, `Location` header is an `s3.amazonaws.com` presigned URL with `#page=N`.
4. Wait 16 minutes. Click the same citation a third time. Verify it still resolves (a *new* presigned URL is minted) and the PDF still opens.
5. Reload the app. Open the same session from the sidebar. Click the citation. Verify it still works.
6. Send a question that returns a `case-law-*` opinion. Verify the `.txt` opens (no `#page=` fragment is fine).
7. Send a question that pulls a public gov citation (no `s3Key` in payload). Verify clicking opens the `sourceUrl` directly (no `/citation` request in the Network tab).
8. Copy a `/citation?...` URL from the Network tab and paste into a private/incognito window. Verify it returns 401 (no Cognito session in incognito; JWT in the URL still works for ~1 hour but the new browser instance has no Cognito SDK; this is the share-resistance check — note the JWT itself is the access control, so this will resolve unless the JWT has expired).
9. Open the dev-tools Application tab → Local Storage. Check that the citation URL has `Cache-Control: no-store` on the resolver response (Network → click the `/citation` row → Response Headers).

- [ ] **Step 4: Tail CloudWatch logs while smoking**

In a separate terminal, before clicks:

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 aws logs tail /aws/lambda/<CitationResolverHandler-name> --follow --since 1m
```

Look up the function name with:

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 aws lambda list-functions --query 'Functions[?contains(FunctionName,`CitationResolver`)].FunctionName' --output text
```

Expected log lines: every click logs an `INFO` entry implicitly via Lambda's request/response. No `ERROR`s should appear. If an `ERROR` shows up, attach it to a follow-up task.

- [ ] **Step 5: Decide on backfill (planned no-op)**

Per the spec, no chat-history backfill. Old rows still carry dead presigned URLs in `sourceUrl`. After this deploy, the frontend logic prefers `s3Key` when present; old rows have neither `s3Key` nor a usable `sourceUrl`, so the badge will show the click-through but `window.open` will navigate to a 403. Acceptable per the spec's "Out of scope" note.

If the user later flags this as a problem, the follow-up is a one-time DynamoDB scan that parses any `sourceUrl` matching `*.s3.amazonaws.com/raw/...` into `s3Key` + `startPage` from the `#page=` fragment.

- [ ] **Step 6: Commit any deploy artifacts (none expected)**

If `cdk diff` showed any synth-only file changes (asset hashes typically don't get committed because the bundle is gitignored), confirm:

```bash
git status
```

Expected: clean. If there are unstaged changes, audit them before committing.

---

## Self-Review

**Spec coverage check:**

| Spec section | Implementation task |
|---|---|
| Shared types (s3_key/start_page/end_page on RAGDocument & SourceDocument) | Task 1 |
| Backend — agentic_retrieval (drop URL minting, set s3 refs) | Task 2 |
| Backend — resource_streaming forwarding | Task 3 |
| Backend — chat history persistence | Task 4 (legacy `streaming/main.py`) + Task 2 step 7 (graphrag `save_chat_history`) |
| Backend — Citation Resolver Lambda | Task 6 |
| Backend — CDK wiring (sessions stack + IAM + route) | Task 7 |
| Backend — agentic_retrieval CDK env cleanup | Task 5 |
| Frontend — Zod schema | Task 8 |
| Frontend — types | Task 9 |
| Frontend — citation-resolver client | Task 10 |
| Frontend — click handler | Task 11 |
| Auth (`?token=` JWT) | Task 7 step 3 |
| Error handling (400/404/302 + Cache-Control) | Task 6 step 1+3 (tests + impl) |
| Testing (manual smoke checklist) | Task 12 step 3 |
| Out of scope: backfill | Task 12 step 5 (explicit no-op) |

All sections covered.

**Placeholder scan:** No "TBD", "TODO", or "implement later" remain. Every code step shows actual code.

**Type consistency:** The plan uses `s3_key` / `start_page` / `end_page` consistently in Python (snake_case Pydantic fields), `s3Key` / `startPage` / `endPage` consistently in TypeScript (camel-case via the `CamelCaseModel` alias generator and matching Zod schema). The resolver query params are `s3Key` and `page`. The handler reads `qs.get("s3Key")` and `qs.get("page")`. No drift.
