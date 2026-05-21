# Citation URL Refresh — Design

**Date:** 2026-05-21
**Branch:** `feat/graphrag-migration`
**Status:** Approved for plan-writing

## Problem

Citation cards link to PDFs in the `RAW_BUCKET` via S3 presigned URLs
minted at retrieval time. The URLs expire after 1 hour
(`PRESIGNED_URL_EXPIRY=3600`). Now that the app supports persistent
sessions — users can reopen a chat from days ago via the sidebar — every
citation in those restored sessions points at a dead URL. Clicking
yields an opaque S3 XML error.

The presigned URL is also persisted into DynamoDB chat history at
end-of-turn (`packages/messages/lambdas/streaming/main.py::log_chat_history`),
so even if the agent ran today, the link rotted into history forever.

We want:

1. Citation clicks to work indefinitely from any session — live or
   restored.
2. Reasonable share-resistance: a copied citation URL pasted into Slack
   or email expires within a meeting (~15 min), reducing the blast
   radius of a leaked link.

## Scope

**In scope:**

- New `GET /citation` route on the Sessions HTTP API. Lambda mints a
  fresh 15-minute presigned URL on demand and 302-redirects to it.
- Stop persisting presigned URLs in chat history. Persist stable
  references (`s3Key`, `startPage`, `endPage`) instead.
- Stop minting presigned URLs in `agentic_retrieval`. Pass references
  through to the WebSocket / chat-history payload.
- Frontend `DocumentCard` click handler: resolve via the new endpoint
  when `s3Key` is present; fall back to direct `sourceUrl` for public
  gov-website citations.
- JWT authorization on the resolver via the existing Cognito user
  pool, accepting the token from a `?token=` query parameter (so
  `window.open()` can carry it without a custom header).

**Out of scope:**

- Legacy OpenSearch RAG path (`packages/messages/lambdas/{classifier,
  retrieval}/`) is no longer in use; not modified.
- Backfilling old chat-history rows that contain dead presigned URLs.
  Existing rows in the dev/test stack are left as-is; they will simply
  fall through to a no-op when clicked. (The frontend gracefully treats
  a missing `s3Key` and a non-S3 `sourceUrl` as a dead link.)
- Changes to `discoveryTag`, authority levels, the agent loop,
  case-opinion fetching, or citation-card visual layout. None of these
  move.
- Cookie-based session auth. The resolver uses `?token=` because that
  matches the existing token-in-storage SPA model with one fewer moving
  part.

## Architecture

### Data flow (live click)

```
[ChatMessage card] --click--> [DocumentCard.onSourceClick]
   |
   |  s3Key present?
   |    yes -> open synchronously a blank popup, await getIdToken(),
   |          set popup.location = `${API_BASE_URL}/citation?s3Key=...&page=N&token=<jwt>`
   |    no, sourceUrl present -> window.open(sourceUrl)  (gov website case)
   |    no, neither -> noop
   v
[Sessions HTTP API] --JWT authorizer (validates ?token=)--> [CitationResolver Lambda]
   |
   |  validate s3Key starts with "raw/"
   |  validate page is a positive int (if provided)
   |  s3:HeadObject (RAW_BUCKET, s3Key)
   |  s3:GetObject presigned URL, ExpiresIn=900
   |  append #page=N if page provided
   v
302 Found
Location: <presigned-url>#page=N
Cache-Control: no-store
   |
   v
[Browser tab follows 302] -> S3 -> PDF.js opens at the cited page
```

### Data flow (restored session)

Identical to live, except the `s3Key` came from a DynamoDB chat-history
row written days ago. The reference is stable; only the resolver-minted
URL is short-lived.

## Components

### 1. Shared types

**`packages/shared/lambda_layers/step_function_types/models.py::RAGDocument`**

Add three optional fields:

```python
class RAGDocument(BaseModel):
    document_id: str
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    discovery_tag: str = "unknown"
    authority_level: int | None = None
    s3_key: str | None = None        # NEW: stable reference to RAW_BUCKET
    start_page: int | None = None    # NEW: 1-indexed first page of the cited chunk
    end_page: int | None = None      # NEW: 1-indexed last page
```

**`packages/shared/lambda_layers/websocket_utils/models.py::SourceDocument`**

Same three fields. Camel-case aliasing already wired via `CamelCaseModel`,
so the WebSocket payload sees `s3Key`, `startPage`, `endPage`.

### 2. Backend — agentic_retrieval

**`packages/graphrag/lambdas/agentic_retrieval/main.py`**

`_generate_source_links` (today returns `(display_label, clickable_url)`)
becomes `_generate_source_label` (returns `display_label` only). Drops
the `s3_client.generate_presigned_url(...)` call and the `#page=` URL
construction.

`_build_opinion_card` likewise stops minting a URL. It populates
`s3_key=raw_key`, `start_page=None`, `end_page=None`. The Google
Scholar fallback (used today when presigning fails) is gone — if S3 is
down the resolver returns 404, which the frontend can surface.

`_build_rag_documents` populates `s3_key`/`start_page`/`end_page` on
every `RAGDocument` from the chunk metadata that's already plumbed
through (`chunk["s3_key"]`, `chunk["start_page"]`, `chunk["end_page"]`).

The `s3_client` boto3 client and `PRESIGNED_URL_EXPIRY` env var become
unused in `main.py` and are removed. (They remain available in the
Lambda's IAM role — that change is in the CDK section below.)

### 3. Backend — resource_streaming

**`packages/messages/lambdas/resource_streaming/main.py`**

The `SourceDocument` it builds at line 122 gains the three new fields,
mapped from `doc.s3_key`, `doc.start_page`, `doc.end_page`. Pure
forwarding — no logic.

### 4. Backend — chat history persistence

**`packages/messages/lambdas/streaming/main.py::log_chat_history`**

The `resources[].data` blob includes `s3Key`, `startPage`, `endPage`
when present. `sourceUrl` is still serialized when present, but
post-change only carries non-S3 public URLs (gov websites). No code
guard against this — the source is the agent's `RAGDocument.source_url`
field, which we ensure carries only public URLs.

### 5. Backend — Citation Resolver Lambda

**New: `packages/sessions/lambdas/citation_resolver/main.py`**

```python
import os, urllib.parse
import boto3

s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]
EXPIRES_IN = 900  # 15 minutes

def handler(event, _context):
    qs = event.get("queryStringParameters") or {}
    s3_key = qs.get("s3Key")
    page = qs.get("page")

    if not s3_key or not s3_key.startswith("raw/"):
        return {"statusCode": 400, "body": "invalid s3Key"}

    page_num = None
    if page is not None:
        try:
            page_num = int(page)
            if page_num < 1:
                return {"statusCode": 400, "body": "page must be >= 1"}
        except ValueError:
            return {"statusCode": 400, "body": "invalid page"}

    try:
        s3.head_object(Bucket=RAW_BUCKET, Key=s3_key)
    except s3.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return {"statusCode": 404, "body": "not found"}
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
        "headers": {"Location": url, "Cache-Control": "no-store"},
        "body": "",
    }
```

Single file. ~50 lines. No Pydantic, no powertools router, no layers.
Cold-start friendly.

### 6. Backend — CDK wiring

**`packages/sessions/infra/sessions-stack.ts`**

- Take `RAW_BUCKET` (or its ARN/name) via stack props from the GraphRAG
  stack, where the bucket is currently created.
- Add a new `lambda.Function` for `citation_resolver`: 128 MB, 5 s
  timeout, env var `RAW_BUCKET`.
- IAM: `s3:GetObject`, `s3:HeadObject` on `arn:aws:s3:::<bucket>/raw/*`
  only.
- New HTTP route:
  ```ts
  httpApi.addRoutes({
    path: '/citation',
    methods: [HttpMethod.GET],
    integration: new HttpLambdaIntegration('CitationIntegration', resolver),
    authorizer,
  });
  ```
- Authorizer: reuse `authorizer` (`HttpJwtAuthorizer`) but extend its
  `IdentitySource` to include `$request.querystring.token` so the JWT
  can come from `?token=`.

**`packages/graphrag/infra/graphrag-messages-stack.ts`**

- Drop `PRESIGNED_URL_EXPIRY` from the `agentic_retrieval` Lambda env.
- Keep `RAW_BUCKET` and the existing `s3:GetObject` grant — the agent
  loop still calls `fetch_case_opinion`, which performs an in-Lambda
  `s3:GetObject` on the case-law `.txt` to feed opinion text into the
  prompt. That code path is unrelated to URL minting.

### 7. Frontend — types

**`packages/webapp/src/components/documents/document-card/document-card.tsx::Document`**

```ts
export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;       // NEW
  startPage?: number;   // NEW
  endPage?: number;     // NEW
  authorityLevel?: number;
  discoveryTag?: 'vector-search' | 'graph-neighbor' | 'fetched'
              | 'framework-list' | 'opinion-fetched' | 'unknown';
}
```

**`packages/webapp/src/stores/types.ts`**: same three fields wherever
`sourceUrl` appears today.

**WebSocket Zod schema** (`packages/messages/types/message-types.ts`):
add `s3Key`, `startPage`, `endPage` to the `SourceDocument` schema in
the discriminated union. Tag: per `CLAUDE.md` "WebSocket Contract", any
new field requires both backend Pydantic and frontend Zod sides to
update.

### 8. Frontend — citation resolver client

**New: `packages/webapp/src/lib/citation-resolver.ts`**

```ts
import { getIdToken } from './auth';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL!;

export async function buildResolverUrl(
  s3Key: string,
  page?: number
): Promise<string | null> {
  const token = await getIdToken();
  if (!token) return null;
  const params = new URLSearchParams({ s3Key, token });
  if (page) params.set('page', String(page));
  return `${API_BASE_URL.replace(/\/+$/, '')}/citation?${params}`;
}
```

### 9. Frontend — click handler

**`packages/webapp/src/components/documents/document-card/document-card.tsx`**

`handleSourceClick` becomes:

```ts
const handleSourceClick = useCallback(
  (e: React.MouseEvent) => {
    e.stopPropagation();
    if (document.s3Key) {
      // Open synchronously to satisfy popup blockers, then redirect once
      // the token resolves. Browsers only allow window.open() inside the
      // synchronous tail of a click event.
      const popup = window.open('about:blank', '_blank', 'noopener,noreferrer');
      if (!popup) return;
      void buildResolverUrl(document.s3Key, document.startPage).then((url) => {
        if (url) popup.location.href = url;
        else popup.close();
      });
    } else if (document.sourceUrl) {
      window.open(document.sourceUrl, '_blank', 'noopener,noreferrer');
    }
    onSourceClick?.(document);
  },
  [document, onSourceClick]
);
```

The `document-badge.tsx` `disabled` rule gains an `s3Key` check:
`disabled={!sourceUrl && !s3Key}`.

## Error Handling

| Failure | Where | User-visible behavior |
|---|---|---|
| JWT missing or expired | API Gateway authorizer | Popup tab navigates to API Gateway 401 page. Acceptable for dev; we surface re-auth via the rest of the app's existing flow. |
| `s3Key` doesn't start with `raw/` | Resolver | 400 in popup. Indicates frontend bug; logged. |
| `s3Key` not in bucket | Resolver `head_object` | 404 in popup. Could happen if the bucket is rotated without re-ingesting. |
| `head_object` non-404 error (e.g. 500, throttle) | Resolver | Lambda raises; API Gateway returns 502. |
| Popup blocked | Frontend (popup is `null`) | Silent no-op. Documented behavior of `window.open` outside synchronous click handler — but our handler IS synchronous, so this should not happen in practice. |
| Token fetch rejects or returns null | Frontend | `buildResolverUrl` returns `null`; click handler closes the popup via `popup.close()`. |
| `start_page` is `None` (case-law `.txt`) | Resolver | No `#page=` fragment appended; tab opens at top of `.txt`. Same as today. |

No special UI for 404/expired bucket — these are operator errors in the
ingestion pipeline, not user errors. CloudWatch logs surface them.

## Testing

**Python unit tests** (`packages/sessions/lambdas/test/test_citation_resolver.py`):

- valid s3Key+page → 302 with `Location` containing `#page=N`
- valid s3Key, no page → 302, no fragment
- s3Key not starting with `raw/` → 400
- non-integer or negative page → 400
- `head_object` raises 404 → 404
- `head_object` raises throttle → propagates
- `Cache-Control: no-store` always present in 302

`packages/sessions/lambdas/test/test_chat_api.py` is unchanged (route
lives in a different Lambda).

**Existing graphrag tests** (`test_agentic_retrieval.py`,
`test_tools.py`): assertions checking `source_url` containing
`s3.amazonaws.com` or a presigned signature need to be updated to
assert `s3_key` / `start_page` instead.

**Frontend Jest tests**: skip explicit unit tests on `buildResolverUrl`
(it's a URL builder); the integration test is "open a restored session,
click a citation, verify the popup tab loads the PDF at the right
page" — manual smoke per the project's `CLAUDE.md` directive.

**Manual verification checklist:**

1. Send a question that returns a PDF citation. Verify card click opens
   the PDF at the cited page.
2. Wait > 15 minutes. Click the same citation again from the same chat.
   Verify it still works (resolver re-mints).
3. Reload the app, open the session from the sidebar, click a citation.
   Verify it works.
4. Copy a resolver URL into a new incognito tab → it should fail (no
   token cookie/session); when navigated within the app, succeed.
5. Wait 15 minutes after a single resolution; the resolved S3 URL
   itself should 403.
6. Send a question that returns a `case-law-*` opinion. Verify the
   `.txt` opens.
7. Send a question whose top citation is a public gov URL (no
   `s3Key`). Verify card click opens the gov URL directly, no resolver
   involvement.

## Open questions / deferred decisions

1. **Resolver telemetry.** Should the resolver emit a CloudWatch metric
   on each click (per-user, per-doc)? Useful for "which sources do
   users actually open?" analytics. Deferred to a follow-up.
2. **Rate limit.** The resolver has no per-user throttling. API
   Gateway's account-level throttle covers DoS. Per-user limits can be
   added later if abuse appears.
3. **Restoring URLs in copied messages.** If a user copies the chat
   markdown to a doc, inline `[doc:...]` markers won't auto-rewrite to
   anything portable. Out of scope; needs a separate "exportable
   citation" feature.
