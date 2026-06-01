# FAQ & Court Case External Links — Design

**Date:** 2026-05-31
**Branch:** `feat/graphrag-migration`
**Status:** Approved for planning

## Problem

When the GraphRAG chatbot cites a source, two source types currently link to
plain-text copies in our S3 buckets instead of the real website:

- **FAQs** have *no* clickable link at all. The `FAQ` model is just
  `{faq_id, question, answer}` and `FAQCard` renders only an authority badge.
- **Court cases** link to the S3 `.txt` opinion file. `_build_opinion_card`
  sets `source_url = None if raw_key else scholar_url`, so when the opinion
  `.txt` exists in S3 (the common case) the card opens the raw text file and
  the public URL is discarded. Case-law *stub* cards (no fetched opinion) get
  whatever `source_url` is on the Neptune node, which is not Google Scholar.

We want both to link back to the authoritative public website:

- FAQs → their `revenue.wi.gov/Pages/FAQS/*.aspx` source page.
- Court cases → a Google Scholar search for the citation.

## Scope

**In scope:** FAQs and court cases only.

**Explicitly out of scope:** statutes, admin rules, WPAM, and gov publications
keep linking to the S3 PDF via the `citation_resolver` Lambda. Those PDFs carry
`start_page`/`end_page` and the resolver appends `#page=N`, opening the exact
page the answer came from. Linking to the website (a monolithic PDF or an HTML
landing page) would drop the user at page 1 of a 600-page manual — a
regression. This page-anchor tradeoff does **not** apply to case law, because
the S3 case-law object is a flat `.txt` with no page concept, so switching it
to the web loses nothing.

## Background: how links flow today (verified)

- Agentic retrieval (`packages/graphrag/lambdas/agentic_retrieval/main.py`)
  builds `RAGDocument`s (for documents) and a `FAQResource` (for FAQs).
- `RAGDocument.source_url` carries a public URL; `RAGDocument.s3_key` carries a
  stable S3 reference. The frontend click handler in `document-card.tsx`
  **prefers `s3Key` over `sourceUrl`** — if `s3Key` is set it calls
  `buildResolverUrl(s3Key, startPage)` to mint a 15-minute presigned URL;
  otherwise it opens `sourceUrl` directly. So *nulling `s3_key`* is how we make
  a document card open its website.
- `FAQ` (in both `step_function_types/models.py` and
  `websocket_utils/models.py`) has no URL field; `resource_streaming/main.py`
  maps only `faq_id/question/answer`; `FAQSchema` in `message-types.ts` matches.
- The WebSocket contract is three-sided: Python websocket model → Zod schema →
  frontend handler. Any new field must be added in all three (per CLAUDE.md).
- Chat history is persisted in `main.py` (`_save_chat_history`, ~line 555/572)
  so restored sessions keep their resources; new fields must be added there too.

### Why FAQ URLs need a lookup

At query time the lambda only knows the FAQ's **question text** and a positional
`faq_N` id. The live `wis-faq-bucket` has 1244 `.txt` files (heavy duplication
from repeated re-syncs) but no URL metadata, and Bedrock KB results expose only
the chunk text + an S3 URI. The real URLs live only in `documents/faqs.json`
(638 records of `{Q, A, source_url}`, 57 distinct URLs), keyed implicitly by
question text. Therefore the lookup key must be **normalized question text**.

Coverage measured against the live bucket:
- 1141 / 1244 files (~92%) match a manifest URL on exact normalized question.
- +27 recoverable with fuzzy matching (6 by exact answer, 21 by 50-char question
  prefix) → ~94.5%.
- 76 files (~6%) are genuine orphans — questions not present in `faqs.json` at
  all (mostly older agricultural-classification worked examples from a page
  version the current 57-URL scrape no longer reproduces). These have no
  recoverable URL and will fall back to today's behavior (no link button).

## Design

### Part A — Court cases → Google Scholar

The opinion **text** still feeds the LLM (answer quality unchanged). Only the
card's link changes. Reuse the existing helper
`case_opinion._scholar_url(citation)`.

1. **`_build_opinion_card`** (`agentic_retrieval/main.py` ~line 1480): set
   `source_url = scholar_url` (always) and `s3_key = None`. The card then opens
   Google Scholar instead of the S3 `.txt`.
2. **Case-law stub cards** in `_build_rag_documents`: for any doc where
   `_is_case_law_stub(doc_id)` is true, derive the Scholar URL from the
   `citation` returned by `neptune.get_document` (already selected at
   `neptune_client.py:209`), set `source_url = scholar_url`, `s3_key = None`.
   If `citation` is missing, leave existing behavior.

No schema/frontend/infra changes — `sourceUrl` already flows end-to-end for
documents and the frontend already opens it when `s3Key` is null.

### Part B — FAQs → revenue.wi.gov via a DynamoDB lookup table

**New table `FaqUrlTable`** (GraphRAG infra, us-east-1 test stack):
- PK: `normalized_question` (S) — lowercased, whitespace-collapsed,
  trailing `?`/`.` stripped (mirror the extract script's `_clean`).
- Attributes: `source_url` (S), `question` (S, raw, for debugging).
- On-demand billing; ~640 small items.
- Passed into `GraphRAGMessagesStack` like `chatHistoryTable`;
  `grantReadData(agenticRetrievalHandler)`; new env var `FAQ_URL_TABLE_NAME`.

**Population:**
- `scripts/graphrag/seed_faq_url_table.py` — one-time seed from
  `documents/faqs.json`, including the fuzzy recovery (answer + 50-char prefix)
  to reach ~94.5%. Idempotent `PutItem` upserts. Run against the test stack as
  part of verification.
- Extend `scripts/graphrag/extract_faq_qa_pairs.py`: during the normal FAQ
  refresh (which already holds `source_url` at line 227), upsert each record
  into `FaqUrlTable`. Keeps the table current on every future scrape with no
  lambda redeploy. Gated behind a `--faq-url-table` arg so existing runs are
  unaffected.

**Query-time lookup** (`agentic_retrieval/main.py`):
- In `_build_faq_resource` (which also backs `_build_cited_faq_resource`),
  collect the normalized questions of the parsed FAQs (≤ `MAX_FAQS` = 3),
  issue one `BatchGetItem` against `FaqUrlTable`, and attach `source_url` to
  each `FAQ`. Misses → `source_url = None`. Table not configured / lookup error
  → log and proceed with no URLs (never fail the query).

**Schema thread (WebSocket contract, all sides):**
- `step_function_types/models.py` `FAQ`: add `source_url: str | None = None`.
- `websocket_utils/models.py` `FAQ`: add `source_url` (→ `sourceUrl` on wire).
- `resource_streaming/main.py`: map `source_url=faq.source_url` in the
  `FAQMessage` construction.
- `message-types.ts` `FAQSchema`: add `sourceUrl: optStr`. **Refactor:** the
  `FAQ` shape is currently hand-declared three times (Zod schema, store
  `types.ts`, and inside `faq-card.tsx`). Make the Zod-inferred type the single
  source of truth and have the store + card *import* it, so the new field is
  added in one place and the type cannot drift (the exact failure mode the
  three-sided WebSocket contract guards against). The duplicated `Document`
  type is left as a follow-up to keep this change FAQ-scoped.
- `_save_chat_history` (`main.py` ~line 572): include `sourceUrl` in the FAQ
  resource dict so restored sessions keep the link.
- `faq-card.tsx`: add a **"View on revenue.wi.gov ↗"** button (compact + modal)
  rendered only when `sourceUrl` is present; opens with
  `noopener,noreferrer`. No resolver — it is a public URL. The `FAQ` interface
  in this file gains `sourceUrl?: string`.

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `_scholar_url` (existing) | citation → Scholar URL | none |
| `_build_opinion_card` / case-law branch of `_build_rag_documents` | set Scholar URL, null s3_key for case law | `_scholar_url`, `neptune.get_document` |
| `FaqUrlTable` | normalized question → source_url | — |
| `seed_faq_url_table.py` | one-time seed from faqs.json | `documents/faqs.json` |
| `extract_faq_qa_pairs.py` (extended) | keep table current on refresh | `FaqUrlTable` |
| FAQ lookup in `_build_faq_resource` | attach URL to FAQs at query time | `FaqUrlTable` |
| WebSocket/schema thread | carry `sourceUrl` to the client | shared models, Zod |
| `faq-card.tsx` | render the FAQ link | `sourceUrl` field |

## Error handling

- FAQ orphans / lookup misses → no link button (matches today; no regression).
- `FaqUrlTable` unset or `BatchGetItem` error → log warning, return FAQs with no
  URL. Retrieval must never fail because of a link lookup.
- Case-law node missing `citation` → leave existing link behavior for that card.
- Scholar URLs are search links; they always resolve even when no opinion text
  exists in S3.

## Testing

- **Python (pytest):**
  - `test_case_opinion.py` / `test_agentic_retrieval.py`: opinion card and
    case-law stub card now carry `source_url == _scholar_url(citation)` and
    `s3_key is None`.
  - New: FAQ lookup attaches `source_url` on hit, `None` on miss, and tolerates
    a missing/erroring table (mock DynamoDB).
  - New: `seed_faq_url_table.py` normalization + fuzzy recovery unit tests
    (exact, answer-match, prefix-match, orphan).
- **TS (jest):** `FAQSchema` accepts `sourceUrl` present/absent/null.
- **Round-trip:** Pydantic `FAQ.model_dump(by_alias=True)` → Zod
  `WebSocketMessageSchema.parse()` for a FAQ with a `sourceUrl`.
- **Manual smoke (us-east-1 test stack):** deploy, seed the table, ask a
  known-FAQ question and a case-law question; confirm the FAQ card links to
  `revenue.wi.gov` and the case card links to Google Scholar; confirm a
  WPAM/statute answer still opens the S3 PDF at the right page.

## Deployment notes

- All changes are additive (new table, new optional field, new link button) and
  land in the us-east-1 GraphRAG test stack only. No production (us-west-2)
  deploy from this branch.
- `cdk diff` before deploy to confirm only additive changes (new table + grant +
  env var).
- Seed `FaqUrlTable` after deploy; re-running the seed is idempotent.
- No graph re-ingestion needed: case-law `citation` is already on Neptune
  nodes, and the FAQ table is independent of the KB.
