# GraphRAG Engineering Guide

This document describes the GraphRAG system powering the Wisconsin DOR property tax chatbot. It covers the runtime architecture, data model, ingestion pipeline, and operational procedures. Audience: developers inheriting or extending this codebase.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [The Request Lifecycle](#2-the-request-lifecycle)
3. [Phase A: The Research Loop](#3-phase-a-the-research-loop)
4. [Phase B: Answer Streaming](#4-phase-b-answer-streaming)
5. [The Neptune Graph Data Model](#5-the-neptune-graph-data-model)
6. [Neptune Analytics Constraints](#6-neptune-analytics-constraints)
7. [PDF Extraction and Chunking](#7-pdf-extraction-and-chunking)
8. [The FAQ Layer](#8-the-faq-layer)
9. [WPAM Edition Recency](#9-wpam-edition-recency)
10. [Case Law](#10-case-law)
11. [Citations and Source Cards](#11-citations-and-source-cards)
12. [WebSocket Streaming and Contracts](#12-websocket-streaming-and-contracts)
13. [Agent Trace UI](#13-agent-trace-ui)
14. [Prompt Management](#14-prompt-management)
15. [The Ingestion Pipeline](#15-the-ingestion-pipeline)
16. [Sessions, Auth, and Chat History](#16-sessions-auth-and-chat-history)
17. [Deployment and Operations](#17-deployment-and-operations)

---

## 1. System Overview

The chatbot answers Wisconsin property-tax questions using a single **agentic retrieval Lambda** invoked directly by EventBridge. There is no Step Function and no separate streaming Lambda for the GraphRAG path.

```
Chat API (POST /message)
    │ emit EventBridge: ChatMessageReceived
    ▼
AgenticRetrieval Lambda
    Phase A: Claude tool loop over Neptune Analytics
    Phase B: streamed answer tokens over WebSocket
    │
    ▼ WebSocket (API Gateway)
Frontend (Next.js)
```

Key facts:
- **Region:** us-east-1 (`WisconsinBotGraphRAG` stack). Neptune graph: `g-ndvl4j73v4`.
- **Model:** `us.anthropic.claude-sonnet-4-6` (default; configurable via `AGENTIC_MODEL_ID` env var).
- **Embedding:** Titan Embed Text V2, 1024 dimensions.
- **Package layout:** the agentic retrieval Lambda has no `main.py`. Entry point is `handler.py`; logic lives in the `loop/`, `agent_tools/` (+ `agent_tools/stages/`), `graph/`, `streaming/`, and `tracing/` subpackages, plus flat helpers (`config.py`, `prompt.py`, `faq.py`, `case_law.py`, `wpam_dedup.py`, `rag_documents.py`, `chat_history.py`).
- The old Step Function path (`useGraphRAG=false`, separate classifier/retrieval/streaming Lambdas) was fully removed — GraphRAG is the only path.

---

## 2. The Request Lifecycle

1. **Frontend → API.** User sends a message. `POST /session/{id}/message` emits an EventBridge event `wisconsin-dor.chat-api:ChatMessageReceived` carrying a `MessageEvent {query, query_id, session_id, persona}`. The AgenticRetrieval Lambda validates the incoming `$.detail` against `UserQuery` (same fields).
2. **EventBridge → Lambda.** Rule `TriggerGraphRAGProcessing` forwards `$.detail` to the AgenticRetrieval Lambda (`retryAttempts: 0`, `maxEventAge: 3 min`).
3. **Phase A (Research Loop).** Claude calls tools against Neptune until it calls `prepare_answer(cited_doc_ids, answer_plan)`. Returns cited documents, chunks, and an answer plan — no answer text yet.
4. **Phase B (Answer Stream).** `converse_stream_with_cache()` (`invoke_model_with_response_stream`) with NO tools generates the answer token-by-token, streamed over WebSocket. Research context from Phase A is injected as a formatted prompt.
5. **Resource cards.** Before the answer stream, document cards and FAQ cards are sent over WebSocket (`streaming/delivery.py`).
6. **Persistence.** `save_chat_history` (`chat_history.py`) writes query, answer, documents, FAQs, and trace log to DynamoDB after streaming completes.
7. **Frontend.** Each WebSocket frame is Zod-validated. Fragments drive streaming markdown; `documents`/`faq` frames populate source cards; `agent-event` frames drive the live trace.

---

## 3. Phase A: The Research Loop

`run_agentic_loop` in `backend/lambdas/agentic_retrieval/loop/phase_a.py`. (The Lambda entry point is `handler.py`; there is no `main.py` — the package is organized into `loop/`, `agent_tools/`, `graph/`, `streaming/`, and `tracing/` subpackages plus flat helpers.)

### Pre-loop steps

- **Turn 0 — `_auto_refine`** (only when `chat_history` exists): rewrites context-dependent follow-ups and extracts an optional `target_wpam_year`. This is a deterministic call to `executor._auto_refine`, **not** a tool the model can invoke (there is no `refine_query` tool — see below).
- **Turn 0 — deterministic `faq_search` seed**: bypasses Claude entirely, runs the verbatim (or refined) query against the Bedrock FAQ KB. Result is **seeded** into the message list as a synthetic `toolUse`/`toolResult` pair (`toolUseId="faq_search_turn0"`) so Claude enters the loop already seeing FAQ context.
- **Turn 0 — deterministic `vector_search` seed**: likewise, an initial `vector_search` is run and seeded (`toolUseId="vector_search_turn0"`) in the same synthetic message pair, so the first real turn already has graph context and broad discovery has fired once. Saves a round-trip.
- **High-confidence steering**: if top FAQ score ≥ `FAQ_SCORE_THRESHOLD` (0.70), a user message tells Claude to treat the FAQ as the primary source of truth and use graph results only to supplement. This only steers — it never short-circuits the loop.

### The tool loop

`MAX_TURNS = 10` (hardcoded in `config.py`; the `MAX_TURNS` env var documented in `retrieval.toml` is not read). Each turn: append assistant message → execute all `toolUse` blocks → append `toolResult`s. The loop terminates when Claude calls `prepare_answer`.

The loop drives Bedrock via `converse_with_cache` (`streaming/bedrock.py`), which wraps `invoke_model` with the native Anthropic Messages API and `cache_control` prompt-caching breakpoints — **not** the Bedrock Converse API. Inference config: `maxTokens=4096`, `temperature=0.0`.

**Turn budget:** at `turn == 7` (the 8th turn) a "running low on turns — call prepare_answer NOW" warning is injected. If the loop exhausts without `prepare_answer`, a degraded fallback returns the last assistant text with `_(Response incomplete: turn budget reached)_` and Phase B is skipped.

### Tool set

`TOOL_DEFINITIONS` in `agent_tools/definitions.py` exposes **12 tools**:

| Tool | Purpose |
| --- | --- |
| `faq_search` | Bedrock FAQ KB retrieve. Already seeded at turn 0; the description tells the model not to call it again. |
| `vector_search` | Titan-embed query → Neptune vector index → 6× over-fetch → dedup/diversity/authority stages → backfill arms. See [auto-backfill](auto-backfill.md) for the full 11-stage pipeline. `top_k` default 10, max 25. |
| `search_document` | Semantic search within one document's chunks. Global vector search (over-fetched, `fetch_k` ~800) filtered to the target `doc_id`. Fallback when headings alone can't identify the right section. |
| `list_sections` | Table of contents for a document: distinct headings with chunk counts and page ranges. Deterministic graph traversal — no vector search. Preferred for multi-chapter docs (WPAM, guides). |
| `get_section` | Chunks from a specific section by exact heading match. Two modes (see below). |
| `get_document` | Node lookup by ID; falls back to vector search on miss. |
| `get_neighbors` | Graph traversal from a node; accepts an `edge_types` filter; optionally semantically ranked. |
| `get_authority_chain` | Walk the governance hierarchy up to the root framework. |
| `list_framework_docs` | Enumerate all documents in a framework. |
| `find_case_law` | Search CaseLaw nodes by name/citation, optionally scoped to a statute. |
| `fetch_case_opinion` | Fetch full opinion `.txt` from S3 for a case-law stub. |
| `prepare_answer` | **Terminal tool.** Claude declares `cited_doc_ids` + `answer_plan`. The loop exits. |

> **Ghost tools.** `refine_query` and `clarify` appear in prompt text and trace-summary handlers, and `clarify` even has executor + loop handling, but neither is in `TOOL_DEFINITIONS` — the model cannot call them. Query refinement happens deterministically pre-loop (`_auto_refine`); disambiguation is a separate pre-loop path in `handler.py`, off by default (`ENABLE_DISAMBIGUATION`).

### Key behaviors

- **`list_sections` + `get_section` (structural browsing):** `list_sections` is a deterministic graph traversal. `get_section` has two modes: **without** a `query` it returns all chunks in document order (deterministic); **with** a `query` it fetches stored embeddings from Neptune (`neptune.algo.vectors.get`), computes cosine similarity against the query embedding, applies z-score filtering (`_Z_THRESHOLD = 0.5`, always keeps at least the top result; falls back to plain cosine sort on a flat distribution), and returns up to `top_k` (default 5, max 10) ranked chunks. The system prompt directs Claude to prefer this path for WPAM and similar large docs.
- **6× over-fetch + diversity cap:** WPAM editions dominate the vector space, so `vector_search` always requests `top_k * 6` chunks (60 for the default `top_k=10`) regardless of whether a `target_wpam_year` is set. After WPAM dedup, a per-document diversity cap (`DIVERSITY_CAP_PER_DOC`, default 3) prevents any source from crowding out others, and an authority quota reserves top_k slots for primary sources.
- **Tool exceptions → error result:** any tool exception becomes an `{"error": ...}` tool-result fed back to Claude so it self-corrects, never crashes the Lambda.
- **`cited_doc_ids` is the authoritative sidebar:** only documents Claude explicitly cites via `prepare_answer` become source cards, not all discovered docs.

---

## 4. Phase B: Answer Streaming

Phase B is orchestrated by `handler.py` and implemented in `loop/phase_b.py`. After `prepare_answer` returns:

1. **Opinion backfill** (`handler.py`): for up to `_OPINION_BACKFILL_CAP` (3) cited case-law stubs not already fetched, pull the full opinion `.txt` from S3 — **before** building RAG documents so the stream has substantive content. See [auto-backfill](auto-backfill.md).
2. **Builds resource cards** from `cited_doc_ids` (`build_rag_documents` in `rag_documents.py`; wire mapping in `streaming/delivery.py`).
3. **Sends resource cards** over WebSocket (documents batched to stay under the frame budget, then the FAQ card).
4. **Calls `stream_answer`** (`loop/phase_b.py`) with NO tools — just the research context + `ANSWER_STREAM_SYSTEM_PROMPT`. Streams the answer token-by-token.
5. **Fragment buffering:** text deltas are batched (`_FRAGMENT_MIN_SIZE = 30` chars) before sending as WebSocket fragments to reduce frame overhead.
6. **Heartbeat:** a background thread (`loop/heartbeat.py`) sends keepalive pings every `WS_HEARTBEAT_INTERVAL` (15) seconds to prevent API Gateway's idle timeout from killing the connection.

The answer context is a structured prompt built from: prior conversation, the user question, the answer plan, and all retrieved chunks grouped by document with page references and opinion text. Phase B also rebuilds statute section→page indexes from cited chunk text (with OCR-space normalization).

**Fallbacks (`handler.py`):** if Phase B streaming throws, a non-streaming `bedrock.converse()` regenerates the answer for DB persistence; if there's no WebSocket connection at all, the answer is generated non-streaming purely to persist it.

---

## 5. The Neptune Graph Data Model

**Engine:** Neptune Analytics (`neptune-graph`), graph `g-ndvl4j73v4`, us-east-1, 1024-dim vector index, 32 m-NCU (scale to 128 for full re-ingestion), IAM auth, public connectivity (no VPC — IAM is the only protection).

### Node types

- **Framework** — authority-level grouping (e.g., `FW-STATUTES`, `FW-WPAM`).
- **Document** — carries `title, source_key, summary, source_url, doc_type, authority_level, citation, effective_date, edition_year`. Labels from `ingest_config.yaml` (e.g., `Statute`, `CaseLaw`, `AssessmentManual`).
- **Chunk** — carries `text, doc_id, source_url, chunk_index, s3_key, start_page, end_page, heading, subheading, edition_year` + 1024-dim `embedding`.
- **Topic** — semantic grouping nodes.
- **Stub** — identity-only `Statute`/`AdminRule`/`CaseLaw` nodes created from citation regex matches.

### Authority hierarchy (9 levels)

| Level | Framework | Parent |
| ---: | --- | --- |
| 1 | Constitution | — |
| 2 | Statutes | Constitution |
| 3 | Case Law | Statutes |
| 4 | Admin Rules | Statutes |
| 5 | WPAM | Admin Rules |
| 6 | FAQ | WPAM |
| 7 | Gov Pubs | WPAM |
| 8 | IAAO | Gov Pubs |
| 9 | USPAP | Gov Pubs |

`authority_level` defaults to `None` for unresolved docs (never a number — a prior default of `6` mislabeled 607 nodes as FAQs).

### Edge types

| Edge | Direction | Meaning |
| --- | --- | --- |
| `CITES` | Doc/Chunk → Statute/AdminRule; Statute → CaseLaw | Citation reference |
| `IMPLEMENTS` | Doc → Statute | Rule implements statute |
| `PART_OF` | Section → Chapter | Statute hierarchy |
| `BELONGS_TO` | Doc → Framework | Framework membership |
| `HAS_SUBSECTION` | Doc → Doc | Multi-part documents |
| `EXTRACTED_FROM` | Chunk → Doc | Chunk provenance |
| `DEFINED_BY` | Statute/AdminRule stub → Chunk | Resolves a citation stub to the chunk that defines it |
| `DERIVED_FROM` | Framework → Framework | Authority precedence chain |
| `COVERS_TOPIC` | Doc → Topic | Semantic grouping |

> **Written vs. queried.** The load pipeline writes all of these. At *retrieval*
> time the Neptune client mainly queries `CITES`, `BELONGS_TO`, `PART_OF`,
> `DERIVED_FROM`, `EXTRACTED_FROM`, and `DEFINED_BY`. `IMPLEMENTS`, `HAS_SUBSECTION`,
> and `COVERS_TOPIC` are written and available to `get_neighbors`/`get_authority_chain`
> but aren't hard-wired into any retrieval query. Document nodes carry doc-type
> labels (`Statute`, `CaseLaw`, `AssessmentManual`, …) but are matched generically
> by `id` in most queries; `Topic` is used only as a filter set, and "stub" is a
> node **property** (`n.stub = true`), not a label.

---

## 6. Neptune Analytics Constraints

These constraints recur across the codebase and explain otherwise-baffling patterns.

### StreamingBody response

`neptune-graph`'s boto3 `execute_query` returns results inside a streaming body under `response["payload"]`, not a pre-parsed dict. All query helpers decode via `json.loads(payload.read())`. Reading `response["results"]` silently returns `[]`.

### No parameterized CALL args

Neptune Analytics rejects `$parameters` inside `CALL` procedure arguments and in variable-length path bounds. So:
- `vector_search` inlines the 1024-float embedding and `topK` as string literals into Cypher.
- `get_authority_chain` inlines `max_depth`.
- Load Phase 8 (vector upserts) inlines each embedding and upserts one vector per query, parallelized over 8 threads.

### No WHERE on topKByEmbedding

Neptune's `topKByEmbedding` procedure has no pre-filter capability. You cannot filter by properties (e.g., `edition_year`) before the vector search. This drives the over-fetch + post-filter dedup pattern for WPAM recency.

### Throttling signals

Neptune signals overload via both `ThrottlingException` and `UnprocessableException` with message about suppressed retries. Retry loops must catch both (8 attempts, backoff capped at 60s).

### UNWIND byte cap

Batch writes (`UNWIND $rows`) must cap by cumulative text bytes (`PHASE_5_MAX_BYTES_PER_FLUSH = 50_000`, in `load.py`) in addition to row count. Count-only caps don't prevent per-query OOM. Neptune at 32 m-NCU OOMs on large batches during full re-ingestion — scale to 128 m-NCU for loads.

---

## 7. PDF Extraction and Chunking

`tools/ingestion/chunking/` turns PDFs into page-tracked chunks. Entry point: `process_pdf_from_s3()` in `pdfChunker.py`, invoked from `extract.py`.

**Extraction** is PyMuPDF-first with Textract fallback: `extract_with_pymupdf()` does font-metric title/header/body classification and table detection, gated by `extraction_looks_good()` (≥5 lines, ≥1 non-empty, avg stripped length ≥3); on failure it falls through to async Textract. The corpus is digital-native, so OCR is rarely needed. The extractor emits `header_split` (text split on `<titles>` markers) and `line_page_mapping` (`list[(line, 1-based page)]`), the basis for per-line page tracking.

**Chunking** routes each PDF by doc_id prefix to one of four strategies — `statute` (cap 3500), `admin_rule` (3500), `wpam` (2500), `general` (2500) — then runs a shared quality pass (boilerplate stripping, TOC removal, clean-plaintext, cap enforcement) plus WPAM-only filters. Case law and non-PDF text take separate paths.

> **The full story lives in [chunk-quality-controls](chunk-quality-controls.md)** — per-strategy boundary
> detection, the boilerplate/TOC/WPAM filters, the case-law and non-PDF paths, the
> complete parameter table, and the design rationale. This section is just the map.

Two invariants worth stating up front:
- **Per-line page tracking:** each buffered line is a `(text, page)` tuple; a chunk's `start_page`/`end_page` is the min/max of its buffer pages. No substring-matching reconstruction.
- **Embedding alignment:** the 2500-char default cap stays well below Titan Embed v2's ~8000-char silent-truncation threshold, so the full chunk text is vector-represented.

---

## 8. The FAQ Layer

A Bedrock FAQ Knowledge Base (Titan v2, `ChunkingStrategy.NONE` — one file = one Q&A pair).

### Turn 0 is deterministic

The FAQ search runs as hardcoded Python (`faq_search_direct`) on the verbatim query, bypassing Claude. Claude paraphrasing the query hurt KB recall.

### Scoring

`FAQ_SCORE_THRESHOLD = 0.70`. At or above this threshold, the FAQ is treated as the primary source of truth. The agentic loop **always continues** into the graph — there is no short-circuit. The FAQ anchors the answer while the graph supplements it with citable authority (statutes, rules, WPAM).

### FAQ URL resolution

Each FAQ's public source page is resolved via `_lookup_faq_url(question)` — a DynamoDB `get_item` against `FaqUrlTable`. The normalizer in the Lambda and the seed script must stay byte-identical (tested by `test_faq_question_normalizer_matches_seed_script`).

### Refreshing FAQs

Multi-region operation: master FAQ files in `wis-faq-bucket` (us-west-2), KB + `FaqUrlTable` in us-east-1. Run `sync_faq_bucket.sh` to copy + trigger KB ingestion, then seed `FaqUrlTable` with `seed_faq_url_table.py`.

---

## 9. WPAM Edition Recency

The graph contains ~15 WPAM editions (2011–2026). Only the current edition should be cited unless the user asks about a specific year.

### The problem

Neptune's `topKByEmbedding` has no pre-filtering. With ~15 editions of semantically identical content, a naive top_k returns mostly old-edition chunks.

### The solution: over-fetch + two-pass dedup

1. **`current_wpam_year`** — dynamically resolved from Neptune (`max(edition_year)` across FW-WPAM docs) as a lazy cached property, queried on first access and cached for the invocation.
2. **6× over-fetch** — `vector_search` always requests `top_k * 6` chunks regardless of whether a `target_wpam_year` is set.
3. **Two-pass dedup** (`wpam_dedup.py`):
   - Pass 1 (heading collapse): groups WPAM chunks by normalized heading, keeps one per group (newest).
   - Pass 2 (edition filter): only `current_wpam_year` (+ `target_year` if set) survives. All other WPAM editions are dropped.
4. **Prompt reinforcement** — instructs Claude to cite only the current edition.

### Historical queries

When the user asks about a specific year, the pre-loop `_auto_refine` step extracts `target_wpam_year`. The 6× over-fetch still applies, and `allowed_years = {current, target}`.

### `edition_year` stamping

Extracted from the doc_id (last 4-digit group, plausibility-gated). Denormalized onto every Chunk so dedup needs no Neptune join.

---

## 10. Case Law

Case law is **thin citation stubs only** — no embeddings, no chunk text. They never appear in `vector_search`. The only way to reach a case is by traversal or text-based citation extraction.

### Discovery paths

Case law reaches the agent through the `vector_search` backfill arms and via
explicit tools — see [auto-backfill](auto-backfill.md) for the full mechanics. In brief:

1. **Direct citation resolution** (`citation_extraction` stage): retrieved chunk text is regex-scanned for citation patterns and resolved against CaseLaw node `citation` properties.
2. **Statute → case-law backfill** (`caselaw_backfill` stage): statute stubs discovered in the results are traversed via `(:Statute)<-[:CITES]-(:Chunk)-[:EXTRACTED_FROM]->(:CaseLaw)` to surface chunks of cases that cite them.
3. **Explicit tools:** `find_case_law` (search by name/citation, optionally scoped to a statute) and `get_neighbors` on a statute with `edge_types=["CITES"]`.

> A "neighbor-doc citation discovery" path (rank neighbor docs by shared statutes,
> scan their chunk text) was designed and the Neptune client still carries its
> helper methods, but it is **not wired into the pipeline** — only tests call those
> methods. See the "Not wired" note in [auto-backfill](auto-backfill.md).

### Citation extraction

Three regex patterns (`extract_citations` in `agent_tools/executor.py`) cover Wisconsin formats: `\d+ Wis. 2d \d+`, `\d+ N.W.2d/3d \d+`, `\d{4} WI [App] \d+`. Resolution: `MATCH (n:CaseLaw) WHERE n.citation IN $citations`.

### Opinion access

Full opinion text lives in S3 (`raw/case-law-{slug}/...txt`). `fetch_case_opinion` fetches on demand. Cards link to CourtListener/Google Scholar (not S3 — `.txt` has no page anchors).

### Post-answer opinion backfill

After `prepare_answer`, up to 3 cited-but-unfetched case-law stubs have their opinions fetched from S3. This ensures the streamed answer has substantive case-law content.

---

## 11. Citations and Source Cards

Citation cards link to each document's **public `source_url`** (docs.legis.wisconsin.gov, revenue.wi.gov, iaao.org, Google Scholar, ...) with a `#page=N` fragment from the chunk's `start_page`. The `s3_key`/page-range fields still flow through the pipeline for provenance/debugging, but no presigned URLs are generated — the old `citation_resolver` Lambda and its `/citation` route were removed after the graph was cleaned of URL-less legacy documents (every non-stub doc now carries a public URL).

### Click-time flow

1. Card click → `chooseSourceTarget(document)` returns the public `sourceUrl` (or null — the card then renders without a link).
2. `appendPageFragment(url, page)` adds `#page=N` and the link opens in a new tab with `noopener,noreferrer`.

### Inline prose citations

The answer uses `[Title](doc:documentId#page=N)`. The frontend's `animated-markdown.tsx::resolveHref` rewrites `doc:<id>#page=N` to a real URL from a per-message `docUrls` map. The `#page=N` fragment carries the chunk's `start_page` for page-specific linking.

---

## 12. WebSocket Streaming and Contracts

### Message types

| streamId | Body type (responseType) | Purpose |
| --- | --- | --- |
| `resources` | `DocumentsMessage` (`documents`) / `FAQMessage` (`faq`) | Source cards (batched for the frame budget) |
| `answer-event` | `AnswerEventType` (`answer-event`) | `start` / `stop` bookends |
| `answer` | `FragmentMessage` (`fragment`) | Answer text fragments (30-char min buffer) |
| `agent-trace` | `AgentEventMessage` (`agent-event`) | Live agent trace events |
| `error` | `ErrorMessage` (`error`) | Error surfaced to the user |
| `choices` | `ChoicesMessage` (`choices`) | Disambiguation options (when enabled) |

Note that `streamId` and `responseType` differ for `agent-trace`→`agent-event` and `resources`→`documents`/`faq`. The frontend enum keys off `streamId`; the Zod discriminated union keys off `responseType`.

**Heartbeat is out-of-band.** Keepalive pings are posted as a raw `{"streamId": "heartbeat", "body": {}}` frame from the heartbeat thread — they are **not** a `WebSocketMessage` subclass, don't go through the `send_json` router, and are dropped by the frontend *before* Zod validation. So heartbeat is intentionally outside the typed contract on both ends.

### The shared-contract discipline

Every WebSocket message is validated by `WebSocketMessageSchema.parse()` (Zod discriminated union on the frontend). A shape the union doesn't know **drops the entire frame** and shows an error.

**Adding a `SourceDocument`/`RAGDocument` field requires updating:**
1. `RAGDocument` in `backend/layers/step_function_types/models.py` (the internal model)
2. `SourceDocument` in `backend/layers/websocket_utils/models.py` (the wire model)
3. Population in `agentic_retrieval/rag_documents.py` (`build_rag_documents`), wire mapping in `streaming/delivery.py`, and persistence in `chat_history.py` (`save_chat_history`)
4. `SourceDocumentSchema` in `frontend/types/message-types.ts`

Use `.nullish()` not `.optional()` for new Zod optional fields — Pydantic serializes unset `Optional` as `null`, but `z.optional()` accepts only `undefined`. (The frontend helpers `optStr`/`optInt`/`optNum` already do `.nullish().transform(v => v ?? undefined)`.)

### Batching

`batch_documents_for_ws` (in `websocket_utils.batching`) splits documents into multiple frames. The enforced budget is `WS_FRAME_BUDGET_BYTES = 100_000` — deliberate headroom below API Gateway's 128 KB hard limit for the JSON envelope and UTF-8 escape expansion. Each doc's content is capped at `MAX_DOC_CONTENT_BYTES = 60_000`.

---

## 13. Agent Trace UI

The agent's per-turn reasoning and tool calls stream as a live, collapsible chain-of-thought.

### Backend

`emit_trace` (in `tracing/emitter.py`) is the single emission helper. Hard-gated: returns immediately if emission is disabled (`config.EMIT_AGENT_TRACE`, wired via `tracing/runtime.py`) or `ws_server` is None, and swallows all send errors — it never raises. Emission points: phase events, tool_call/tool_result pairs, reasoning text, loop_complete.

The backend pre-formats all human-readable strings and camelCase metadata. The UI just picks a verb and renders.

### Frontend

`appendAgentTraceEvent` (`frontend/src/stores/chat-store.ts`) dedupes by `seq`. For a `tool_result`, it finds the most recent same-key event (`traceStepKey`) and, if that event is still `pending`, replaces it in place (one slot transitions "Searching" → "Found N"). Dot states: error (red), miss (hollow + muted), done (solid), pending (hollow).

### Metadata allow-list

`ALLOWED_METADATA_KEYS` in `tracing/emitter.py` (a ~70-key frozenset) mirrors a Set in `frontend/src/components/messages/trace-metadata.ts`. Defense-in-depth to prevent raw query/chunk text from leaking to the UI. A new key must be added to both sides.

> **Known drift:** the frontend mirror is currently a subset (~24 keys) and its
> comment points at the obsolete `packages/graphrag/lambdas/agentic_retrieval/main.py`
> path. The frontend Set only governs the compact trace *summary* line, so missing
> keys are dropped from that summary but still present in the raw payload — worth
> reconciling when you touch trace metadata.

---

## 14. Prompt Management

All LLM prompts are externalized to `config/model_configs.toml` and loaded from DynamoDB at Lambda cold-start.

### Entries

- `agenticRetrieval` — system prompt for Phase A (the research loop tool instructions).
- `answerStream` — system prompt for Phase B (answer generation with citation formatting rules).
- `personaGovernment` / `personaCitizen` — persona suffixes appended to the Phase B prompt.

### Iteration workflow

```bash
# Edit the prompt, then push to DynamoDB:
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 uv run python tools/upload_model_configs.py --only agenticRetrieval
```

Hot-reload without redeploy. `cdk deploy` does NOT write prompt content — only the upload script does. `cdk deploy` is only needed when infra changes (new env vars, permissions, etc.).

---

## 15. The Ingestion Pipeline

Four scripts run in sequence against two S3 buckets (raw + work) and the Neptune graph. Available as Fargate tasks or local execution.

```
scrape/upload → raw/{doc_id}/{doc_id}.{pdf|txt} + .metadata.json
       ▼
extract.py → work: extracted/{doc_id}.json (PDF→chunks, classify, statute_refs)
       ▼
embed.py → work: embedded/{doc_id}.json (Titan v2 1024-dim; empty case_law stubs SKIPPED)
       ▼
load.py → Neptune graph (9 CLI phases of batched Cypher)
```

### Load phases

`load.py` runs **9 sub-phases**, numbered 1–9 with a 1:1 mapping to their `phase_N_*` functions (no CLI-step vs. function offset). `--start-phase`/`--stop-after-phase` take integers in `[1, 9]`.

| # | Name | What it does |
| ---: | --- | --- |
| 1 | Scaffold | Framework nodes, `DERIVED_FROM` edges, statute-family Statute nodes + `BELONGS_TO` |
| 2 | Document Nodes | MERGE per-doc-type nodes with all properties (incl. WPAM `edition_year`) |
| 3 | Statute Hierarchy | `PART_OF` edges (section → chapter, subsection → parent); MERGE stub parents |
| 4 | Hierarchy Links | `HAS_SUBSECTION` sub-document links; wire orphan stubs to their framework |
| 5 | Chunk Nodes | Purge stale chunks, MERGE Chunk nodes + `EXTRACTED_FROM` + chunk-level `CITES` |
| 6 | Case Law CITES | `(Statute)-[:CITES]->(CaseLaw)` reverse edges |
| 7 | Stub Resolution | `DEFINED_BY` edges from Statute/AdminRule stubs to matching chunks |
| 8 | Vector Upserts | Upsert embeddings (one per query, 8 threads) |
| 9 | Orphan Cleanup | GC orphan Statute stubs, orphan Topics, stale CaseLaw nodes |

> An earlier semantic-edge / topic-clustering phase (LLM-classified
> `RELATED_TO`/`SUPPLEMENTS`/`SUPERSEDES`/`CONFLICTS_WITH`) was removed — this is why
> older notes mention "10 steps" or "phases 1–11". The current pipeline stops at
> Phase 9 (orphan cleanup). One internal batch-writer helper is still named
> `_flush_phase_8_batch` but is used by Phase 5 — a naming vestige, not an offset.

### Cache-aware resume

`extract.py`/`embed.py` skip already-processed docs unless `--force`. All three accept `--source-filter <prefix>` for scoped runs. `load.py` has `--start-phase`/`--stop-after-phase`.

### When to re-ingest

- **Edge logic changes (phases 3/4/6/7):** full re-ingest required. MERGE doesn't retroactively remove stale edges.
- **Chunking or embedding changes:** full re-ingest. Run `purge_orphan_chunks.py` after (MERGE never deletes high-index orphans; Phase 5 also purges stale chunks per-doc on each load).
- **Property-only mutation (e.g., edition_year):** scoped `--source-filter` run is sufficient.

### Running on Fargate

```bash
./tools/ingestion/scripts/run_full_ingest.sh          # full pipeline
./tools/ingestion/scripts/run_fargate.sh extract      # single phase
./tools/ingestion/scripts/run_fargate.sh load --start-phase 5 --stop-after-phase 8
```

Docker image must be `--platform linux/amd64` (Fargate requirement on Apple Silicon builds).

### Cleanup / ops scripts (`tools/ingestion/ops/`, dry-run by default)

| Script | Purpose |
| --- | --- |
| `purge_orphan_chunks.py` | Delete Chunk nodes whose index ≥ current chunk count (orphans from a prior, larger load) |
| `cleanup_orphan_chunks.py` | Sibling of the above — delete Neptune chunks beyond the expected per-doc range |
| `clean_stale_extracts.py` | Delete `extracted/`+`embedded/` artifacts for missing/drifted raw docs |
| `cleanup_legacy_docs.py` | Audit/backfill legacy doc nodes lacking a public `source_url` |
| `delete_semantic_edges.py` | Delete the removed semantic-edge layer (`RELATED_TO`/`SUPPLEMENTS`/…) from the live graph |
| `seed_faq_url_table.py` | Seed `FaqUrlTable` from `documents/faqs.json` |
| `extract_faq_qa_pairs.py` | Scrape FAQ pages into single Q&A files for the Bedrock FAQ KB |

(There are no `patch_metadata_authority.py` / `patch_work_authority.py` scripts —
authority resolution lives inline in `extract.py`/`load.py::resolve_authority_level`.)

---

## 16. Sessions, Auth, and Chat History

Cognito-authenticated users get a sidebar of past chats. Two DynamoDB tables:

- **SessionTable** — PK `sessionId`; `userIdIndex` GSI (PK `userId`, SK `lastMessageAt`); `connectionId` GSI for WebSocket.
- **ChatHistoryTable** — PK `queryId`; `sessionIdKey` GSI (PK `sessionId`, SK `timestamp`); plus `timestampIndex` and `activityIndexV2` GSIs backing the admin activity endpoint. The table's env var is `MESSAGES_TABLE_NAME` in chat_api, `CHAT_HISTORY_TABLE_NAME` in agentic_retrieval.

All routes live in one Powertools Lambda (`backend/lambdas/chat_api/main.py`). Session routes: `POST /session`, `GET /sessions`, `PATCH /session/{id}`, `DELETE /session/{id}`, `POST /session/{id}/message`, `GET /session/{id}/history`, `POST /session/{id}/feedback`. Admin routes (Cognito `Admins`-group gated via `require_admin()`): `GET /admin/activity`, `GET /admin/activity/{queryId}`, `POST /admin/ingest`, `GET /admin/chunks/documents`, `GET /admin/chunks/{docId}`.

### Key invariants

- Chat history is written **after** streaming completes. DynamoDB is the source of truth for resumed sessions.
- `save_chat_history` rebuilds fields explicitly — adding a `RAGDocument` field requires adding it here or resumed cards lose it.
- JWT accessor: `request_context.authorizer.jwt_claim` (not `.jwt.claims`).
- `$connect` uses `update_item` with `ConditionExpression attribute_exists(sessionId)` — `put_item` would clobber `userId`/`title`.
- DynamoDB numbers deserialize as `Decimal`; all responses run through `_json_default` (whole→int, fractional→float).

---

## 17. Deployment and Operations

### Regions

| Region | Stack | Rule |
| --- | --- | --- |
| us-east-1 | `WisconsinBotGraphRAG` (Neptune) | All GraphRAG development |
| us-west-2 | Production (legacy OpenSearch) | Do not deploy from feature branches |

Commands below use `<your-profile>` as a placeholder — substitute your own AWS CLI/SSO profile, or export `AWS_PROFILE`. Always run `cdk diff` before deploy.

### Deploy checklist

```bash
bun install
bun run bundle                          # copy Python lambdas to infra/bundle/
cd infra
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```

### First-time setup

1. `cdk bootstrap` us-east-1.
2. `bun install` → `bun run bundle` → `cdk deploy`.
3. Seed `ModelConfigTable`: `uv run python tools/upload_model_configs.py`.
4. Sync FAQs: `sync_faq_bucket.sh` → `seed_faq_url_table.py`.
5. Run full ingestion pipeline.

### Environment gotchas

- **SSL on macOS:** `export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")` then set `AWS_CA_BUNDLE=$CERT`.
- **Bedrock model IDs:** require full inference-profile format (`us.anthropic.claude-sonnet-4-6`).
- **Neptune scaling:** 32 m-NCU for runtime, 128 m-NCU during full re-ingestion. Scale back after — 128 m-NCU is 4× cost.
- **`AWS_REGION` in shell:** scripts default to us-east-1 but `AWS_REGION` overrides. Always set explicitly.

### Observability

A single `query_id` threads through handler → tools → WebSocket → DynamoDB. To debug a failed query:
1. DynamoDB `get-item` by `queryId` for the stored question/answer/feedback.
2. CloudWatch: fetch the recent log stream and grep the `query_id` (the filter tokenizer splits UUIDs poorly — see CLAUDE.md "Investigating a Query").
3. Look for structured log events keyed by `query_id`: `agentic_retrieval_request_received`, `agent_tool_call`, `agent_tool_result`, `agent_loop_complete`, `answer_stream_complete`.

---

_When you change a subsystem, update its section here. Companion docs:
[auto-backfill](auto-backfill.md) (vector_search backfill arms), [chunk-quality-controls](chunk-quality-controls.md)
(ingestion chunking), [fargate-ingestion](fargate-ingestion.md) (running the pipeline),
[testing](testing.md) (what to run and update)._
