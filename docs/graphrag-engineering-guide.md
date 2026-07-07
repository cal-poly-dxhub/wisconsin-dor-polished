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
    Phase B: converse_stream() → answer tokens over WebSocket
    │
    ▼ WebSocket (API Gateway)
Frontend (Next.js)
```

Key facts:
- **Region:** us-east-1 (`WisconsinBotGraphRAG` stack). Neptune graph: `g-ndvl4j73v4`.
- **Model:** `us.anthropic.claude-sonnet-4-6` (configurable via `AGENTIC_MODEL_ID` env var).
- **Embedding:** Titan Embed Text V2, 1024 dimensions.
- **Legacy path** (`useGraphRAG=false`): Step Function + separate streaming Lambdas in `MessagesStack`. Not deployed, not under active development. CDK context flag gates it.

---

## 2. The Request Lifecycle

1. **Frontend → API.** User sends a message. `POST /session/{id}/message` emits an EventBridge event `wisconsin-dor.chat-api:ChatMessageReceived` carrying `UserQuery {query, query_id, session_id}`.
2. **EventBridge → Lambda.** Rule forwards `$.detail` to the AgenticRetrieval Lambda (`retryAttempts: 0`, `maxEventAge: 3 min`).
3. **Phase A (Research Loop).** Claude calls tools against Neptune until it calls `prepare_answer(cited_doc_ids, answer_plan)`. Returns cited documents, chunks, and an answer plan — no answer text yet.
4. **Phase B (Answer Stream).** `converse_stream()` with NO tools generates the answer token-by-token, streamed over WebSocket. Research context from Phase A is injected as a formatted prompt.
5. **Resource cards.** Before the answer stream, document cards and FAQ cards are sent over WebSocket.
6. **Persistence.** `save_chat_history` writes query, answer, documents, FAQs, and trace log to DynamoDB after streaming completes.
7. **Frontend.** Each WebSocket frame is Zod-validated. Fragments drive streaming markdown; `documents`/`faq` frames populate source cards; `agent-event` frames drive the live trace.

---

## 3. Phase A: The Research Loop

`run_agentic_loop` in `backend/lambdas/agentic_retrieval/main.py`.

### Pre-loop steps

- **Turn 0a — `refine_query`** (only when `chat_history` exists): rewrites context-dependent follow-ups and extracts an optional `target_wpam_year`.
- **Turn 0b — deterministic `faq_search`**: bypasses Claude entirely, runs the verbatim (or refined) query against Bedrock FAQ KB. Result is **seeded** into the message list as a synthetic `toolUse`/`toolResult` pair so Claude enters the loop already seeing FAQ context.
- **High-confidence steering**: if top FAQ score ≥ 0.70, a user message tells Claude to treat the FAQ as primary truth and use graph results only to supplement.

### The tool loop (turns 1–10)

`bedrock.converse(maxTokens=4096, temperature=0.0)` with the full tool set. Each turn: append assistant message → execute all `toolUse` blocks → append `toolResult`s. Loop terminates when Claude calls `prepare_answer`.

**Turn budget:** at turn 8, a "running low on turns" warning is injected. If the loop exhausts without `prepare_answer`, a degraded fallback extracts the last assistant text with `_(Response incomplete: turn budget reached)_`.

### Tool set

| Tool | Purpose |
| --- | --- |
| `faq_search` | Bedrock FAQ KB retrieve (SEMANTIC). Seeded at turn 0; Claude should not call it again. |
| `refine_query` | LLM query rewrite + `target_wpam_year` extraction. |
| `vector_search` | Titan-embed query → Neptune vector index → 6× over-fetch → diversity cap (5 chunks/doc) → WPAM dedup → auto-enrich top-3 doc_ids with `get_neighbors` → direct citation resolution → neighbor-doc citation discovery. |
| `get_neighbors` | Graph traversal from a node; accepts `edge_types` filter. |
| `get_document` | Node lookup by ID; falls back to vector search on miss. |
| `get_authority_chain` | Walk `DERIVED_FROM`/`PART_OF` up and down from a node. |
| `list_framework_docs` | Enumerate all documents in a framework. |
| `list_sections` | Table of contents for a document: distinct headings with chunk counts and page ranges. Deterministic graph traversal — no vector search. Preferred for multi-chapter docs (WPAM, guides). |
| `get_section` | Chunks from a specific section by exact heading match. Use after `list_sections`. Without `query`: returns all chunks in document order (deterministic). With `query`: fetches stored embeddings via `neptune.algo.vectors.get`, ranks by cosine similarity + z-score filtering (threshold 0.5), returns up to `top_k` (default 5, max 10) relevant chunks. |
| `search_document` | Semantic search within one document's chunks. Global vector search (fetch_k=800) filtered to target `doc_id`. Fallback when headings alone can't identify the right section. |
| `fetch_case_opinion` | Fetch full opinion `.txt` from S3 for a case-law stub. |
| `prepare_answer` | Terminal tool. Claude declares `cited_doc_ids` + `answer_plan`. The loop exits. |
| `clarify` | Ask the user a disambiguation question (disabled by default). |

### Key behaviors

- **`list_sections` + `get_section` (structural browsing):** `list_sections` is a deterministic graph traversal. `get_section` supports two modes: without a `query` it returns all chunks in order (deterministic); with a `query` it fetches stored embeddings from Neptune (`neptune.algo.vectors.get`), computes cosine similarity against the query embedding, applies z-score filtering (threshold=0.5, always includes at least 1 chunk), and returns up to `top_k` ranked results. This hybrid approach avoids the query-formulation problem of `search_document` while still surfacing the most relevant chunks in large sections. The system prompt directs Claude to prefer this path for WPAM and similar.
- **Auto-enrichment:** after `vector_search` returns chunks, `get_neighbors` is called on the top-3 distinct parent doc_ids. Agent gets graph context for free without spending a turn.
- **6× over-fetch + diversity cap:** WPAM editions dominate the vector space, so `vector_search` always requests `top_k * 6` chunks (90 for default top_k=15) regardless of whether a `target_wpam_year` is set. After WPAM dedup, a per-document cap (default 5) prevents any source from crowding out others.
- **Tool exceptions → error result:** any tool exception becomes an `{"error": ...}` tool-result fed back to Claude so it self-corrects, never crashes the Lambda.
- **`cited_doc_ids` is the authoritative sidebar:** only documents Claude explicitly cites via `prepare_answer` become source cards, not all discovered docs.

---

## 4. Phase B: Answer Streaming

After `prepare_answer` returns, the handler:

1. **Builds resource cards** from `cited_doc_ids` — including opinion backfill (up to 3 case-law stubs fetched from S3).
2. **Sends resource cards** over WebSocket (documents batched to stay under 128 KB frame limit, then FAQ card).
3. **Calls `converse_stream()`** with NO tools — just the research context + `ANSWER_STREAM_SYSTEM_PROMPT`. Streams answer token-by-token.
4. **Fragment buffering:** text deltas are batched (minimum 30 chars) before sending as WebSocket fragments to reduce frame overhead.
5. **Heartbeat:** a background thread sends keepalive pings every 15 seconds to prevent API Gateway's idle timeout from killing the connection.

The answer context is a structured prompt built from: prior conversation (last 3 turns), the user question, the answer plan, and all retrieved chunks grouped by document with page references and opinion text.

**Fallback:** if Phase B streaming fails (WebSocket dead, Bedrock error), a non-streaming `converse()` call generates the answer for DB persistence.

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
| `CITES` | Doc/Chunk → Statute/AdminRule; Statute → CaseLaw (mirror) | Citation reference |
| `IMPLEMENTS` | Doc → Statute | Rule implements statute |
| `PART_OF` | Section → Chapter | Statute hierarchy |
| `BELONGS_TO` | Doc → Framework | Framework membership |
| `HAS_SUBSECTION` | Doc → Doc | Multi-part documents |
| `EXTRACTED_FROM` | Chunk → Doc | Chunk provenance |
| `DERIVED_FROM` | Framework → Framework | Authority precedence chain |
| `COVERS_TOPIC` | Doc → Topic | Semantic grouping |

Phase 11 additionally creates semantic edges: `RELATED_TO`, `SUPPLEMENTS`, `SUPERSEDES`, `CONFLICTS_WITH`.

---

## 6. Neptune Analytics Constraints

These constraints recur across the codebase and explain otherwise-baffling patterns.

### StreamingBody response

`neptune-graph`'s boto3 `execute_query` returns results inside a streaming body under `response["payload"]`, not a pre-parsed dict. All query helpers decode via `json.loads(payload.read())`. Reading `response["results"]` silently returns `[]`.

### No parameterized CALL args

Neptune Analytics rejects `$parameters` inside `CALL` procedure arguments and in variable-length path bounds. So:
- `vector_search` inlines the 1024-float embedding and `topK` as string literals into Cypher.
- `get_authority_chain` inlines `max_depth`.
- Phase 10 (vector upserts) inlines each embedding and upserts one vector per query, parallelized over 8 threads.

### No WHERE on topKByEmbedding

Neptune's `topKByEmbedding` procedure has no pre-filter capability. You cannot filter by properties (e.g., `edition_year`) before the vector search. This drives the over-fetch + post-filter dedup pattern for WPAM recency.

### Throttling signals

Neptune signals overload via both `ThrottlingException` and `UnprocessableException` with message about suppressed retries. Retry loops must catch both (8 attempts, backoff capped at 60s).

### UNWIND byte cap

Batch writes (`UNWIND $rows`) must cap by cumulative text bytes (`PHASE_8_MAX_BYTES_PER_FLUSH = 50_000`) in addition to row count. Count-only caps don't prevent per-query OOM. Neptune at 32 mCU OOMs on large batches during full re-ingestion — scale to 128 mCU for loads.

---

## 7. PDF Extraction and Chunking

`tools/ingestion/chunking/` turns PDFs into page-tracked chunks. Entry point: `process_pdf_from_s3()` in `pdfChunker.py`, invoked from `extract.py`.

### Extraction

**PyMuPDF-first, Textract-fallback.** `process_pdf_from_s3` tries `extract_with_pymupdf()` (font-metric title/header/body classification, table detection), gated by `extraction_looks_good()` (≥5 lines, ≥1 non-empty, avg stripped length ≥3). On failure, falls through to async Textract (LAYOUT+TABLES via `TextLinearizationConfig`). The corpus is digital-native — OCR adds nothing; PyMuPDF is faster and free.

**PyMuPDF extraction (`pymupdf_extractor.py`):**
- Determines body font size (`_get_body_font_size`) by character-count-weighted mode across all pages.
- Classifies each text line as title (≥1.4× body size, or ≥1.15× + bold), header (≥1.1× body size), or body based on span font metrics.
- Detects tables via `page.find_tables()`, validates with `looks_like_real_table()` (rejects sparse grids >60% empty cells, multi-column prose with long cells + few rows). Genuine tables are rendered as `" | "`-joined rows.
- Tags lines with XML markers (`<titles>`, `<headers>`, `<tables>`) for downstream splitting.

Both extractors emit: `header_split` (text split on `<titles>` markers) and `line_page_mapping` (`list[tuple[str, int]]` — every line + 1-based page number).

### Boilerplate stripping

Applied between extraction and chunking (`boilerplate.py`). Strategy-aware regex patterns remove:
- **General (all docs):** bare page numbers, "Wisconsin Department of Revenue" headers, "Back to table of contents" links, date-only lines.
- **Statute:** "Updated 20XX Wisconsin Statutes" running headers, bare "Chapter N" lines, all-caps section-title running headers.
- **Admin Rule:** "WISCONSIN ADMINISTRATIVE CODE" headers, register lines, "Published under s. X.X" lines.
- **WPAM:** "Wisconsin Property Assessment Manual" headers, volume/page references. Additionally strips repeated "Chapter N Title" running headers (keeps only the first non-TOC occurrence per unique heading text).

### Chunking strategies

Routed by `get_chunking_strategy()` which checks `source_id` prefixes (`wpam-`, `admin_rules-`, `statutes-`) then falls back to `CHUNKER_BY_SOURCE` dict:

| Strategy | Cap | Docs | Key behavior |
| --- | --- | --- | --- |
| **statute** | 3500 | State law PDFs (`statutes-*`) | Section-boundary splitting: regex `{chapter}.\d+` detects section headers (e.g., `70.32 Real estate, how valued.`). Merges multi-page fragments of the same section. Oversized sections split at subsection markers `(1)`, `(a)`, `(4m)`, then sentence boundaries, then line breaks (`_split_statute_section`). Greedy-merges small adjacent subsections back together. |
| **admin_rule** | 3500 | Admin code PDFs (`admin_rules-*`) | Rule-boundary splitting: regex `Tax\s\d+\.\d+` detects rule IDs. Groups all fragments by normalized rule ID (merges TOC entries + body occurrences). Drops stubs (<80 chars body). Oversized rules split via same `_split_statute_section` logic. |
| **wpam** | 2500 | Assessment manual (`wpam-*`) | Chapter/section hierarchy: `_is_chapter_heading()` detects real chapter titles (rejects mid-prose references via suffix/remainder heuristics, 80-char/8-word caps). Section headers detected by 4 patterns (ALL-CAPS, `A. Title`, `1. Title`, `IV. Title`). Merges adjacent small chunks (<80 words) within the same chapter if combined <500 words and <2500 chars. |
| **general** | 2500 | Everything else | Heading-based splitting at roman-numeral (`^[IVXLCDM]+\s*[.\-–:]`) and capital-letter (`^[A-Z]\s*[.\-–:]`) section markers. Word limit (1200) and char cap both trigger flushes. |

### Post-chunking pipeline

After strategy-specific chunking, `process_pdf_from_s3` applies these steps in order:

1. **TOC chunk removal** (`toc_detector.py`): `is_toc_chunk()` flags chunks with high leader-dot coverage (≥20% of text is `......` sequences, ≥2 matches, ≤1500 chars) or chunks with pure-roman-numeral headings (`V.`, `XIV.`) containing any leader dots.

2. **WPAM quality filters** (`wpam_chunk_filter.py`, WPAM only):
   - `filter_wpam_chunks`: drops chunks with body <60 chars, garbled column-interleaved text (high pipe density + fragmented lines), or single-character table cells (>30% of lines ≤2 chars).
   - `repair_wpam_subheadings`: clears subheadings appearing on >5 chunks (leaked numbered-list items the chunker carried forward without reset).

3. **Clean plaintext** (`extract_clean_plaintext`): strips residual XML tags, drops chunks with <50 words and only 1 sentence (unless they match a heading pattern), drops statute index-page stubs (<15 words).

4. **Final cap enforcement** (`_enforce_chunk_cap`): splits any chunk exceeding the strategy cap at paragraph (`\n\n`) or line (`\n`) boundaries within the last 20% of the cap window. Tiny tail fragments (<200 chars) are merged back into the predecessor.

5. **Short-chunk merge** (WPAM only, `merge_short_chunks`): merges chunks <200 chars into their predecessor when they share the same heading and combined length stays ≤3000 chars. Prevents concentrated short-chunk embeddings from artificially outscoring substantive chunks.

### Key parameters

| Parameter | Value | Location |
| --- | --- | --- |
| `CHUNK_MAX_CHARS` | 2500 (general/wpam) | `pdfChunker.py` |
| `_CHUNK_CAP_BY_STRATEGY` | 3500 (statute, admin_rule) | `pdfChunker.py` |
| `max_words` | 1200 (general/wpam in-loop) | `chunk_document` / `chunk_document_wpam` |
| `min_merge_words` | 80 (wpam) | `chunk_document_wpam` |
| `_MIN_BODY_CHARS` | 80 (admin_rule stub filter) | `chunk_document_admin_rule` |
| `min_chars` (short merge) | 200 | `merge_short_chunks` |
| `max_merged_chars` | 3000 | `merge_short_chunks` |

### Design invariants

- **Per-line page tracking:** each buffered line is a `(text, page)` tuple. A chunk's `start_page`/`end_page` is min/max of its buffer pages. No substring-matching reconstruction (prior approach inflated page ranges when chunks contained repeated boilerplate).
- **TOC-immune headings:** dot-leader lines (`≥5` consecutive dots) are always treated as body text, never allowed to become the current heading — TOC entries match heading regexes textually but reference page numbers rather than starting sections.
- **Table validation:** `looks_like_real_table()` rejects false-positive tables using row count, max cell length, and cell sparsity (>60% empty). Multi-column prose misdetected by `find_tables()` falls through to normal text extraction.
- **Embedding alignment:** 2500-char general cap stays well below Titan Embed v2's 8000-char silent-truncation threshold, ensuring the full chunk text is vector-represented.

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

1. **`current_wpam_year`** — dynamically resolved from Neptune (`max(edition_year)` across FW-WPAM docs) at Lambda cold start.
2. **6× over-fetch** — `vector_search` always requests `top_k * 6` chunks regardless of whether a `target_wpam_year` is set.
3. **Two-pass dedup** (`wpam_dedup.py`):
   - Pass 1 (heading collapse): groups WPAM chunks by normalized heading, keeps one per group (newest).
   - Pass 2 (edition filter): only `current_wpam_year` (+ `target_year` if set) survives. All other WPAM editions are dropped.
4. **Prompt reinforcement** — instructs Claude to cite only the current edition.

### Historical queries

When the user asks about a specific year, `refine_query` extracts `target_wpam_year`. The 6× over-fetch still applies, and `allowed_years = {current, target}`.

### `edition_year` stamping

Extracted from the doc_id (last 4-digit group, plausibility-gated). Denormalized onto every Chunk so dedup needs no Neptune join.

---

## 10. Case Law

Case law is **thin citation stubs only** — no embeddings, no chunk text. They never appear in `vector_search`. The only way to reach a case is by traversal or text-based citation extraction.

### Three discovery paths

1. **Mirror edge traversal:** `(Statute)-[:CITES]->(CaseLaw)` mirror edges. The prompt mandates `get_neighbors` on the controlling statute with `edge_types=["CITES"]`.
2. **Direct citation resolution:** after `vector_search`, retrieved chunk text is regex-scanned for citation patterns and resolved against CaseLaw node `citation` properties.
3. **Neighbor-doc citation discovery:** topically-ranked neighbor docs have their chunk text scanned for citations. Catches cases mentioned only in related docs (not in directly-retrieved chunks).

### Citation extraction

Three regex patterns cover Wisconsin formats: `\d+ Wis. 2d \d+`, `\d+ N.W.2d/3d \d+`, `\d{4} WI(App)? \d+`. Resolution: parameterized `MATCH (n:CaseLaw) WHERE n.citation IN $citations`.

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

| streamId | Body type | Purpose |
| --- | --- | --- |
| `resources` | `DocumentsMessage` / `FAQMessage` | Source cards (batched for 128KB frame limit) |
| `answer-event` | `AnswerEventType` | `start` / `stop` bookends |
| `answer` | `FragmentMessage` | Answer text fragments (30-char min buffer) |
| `agent-trace` | `AgentEventMessage` | Live agent trace events |
| `heartbeat` | `{}` | Keepalive during long Bedrock calls |
| `choices` | `ChoicesMessage` | Disambiguation options (when enabled) |

### The shared-contract discipline

Every WebSocket message is validated by `WebSocketMessageSchema.parse()` (Zod discriminated union on the frontend). A shape the union doesn't know **drops the entire frame** and shows an error.

**Adding a field requires updating:**
1. `RAGDocument` in `backend/layers/step_function_types/models.py`
2. `SourceDocument` in `backend/layers/websocket_utils/models.py`
3. Streaming construction in `agentic_retrieval/main.py` + `save_chat_history`
4. `SourceDocumentSchema` in `frontend/types/message-types.ts`

Use `.nullish()` not `.optional()` for new Zod optional fields — Pydantic serializes unset `Optional` as `null`, but `z.optional()` accepts only `undefined`.

### Batching

`batch_documents_for_ws` (in `websocket_utils.batching`) splits documents into multiple frames to stay under the 128 KB API Gateway limit. Each doc's content is capped at 60 KB.

---

## 13. Agent Trace UI

The agent's per-turn reasoning and tool calls stream as a live, collapsible chain-of-thought.

### Backend

`emit_trace` is the single emission helper. Hard-gated: returns immediately if `EMIT_AGENT_TRACE` is false or `ws_server` is None, swallows all send errors. Emission points: phase events, tool_call/tool_result pairs, reasoning text, loop_complete.

The backend pre-formats all human-readable strings and camelCase metadata. The UI just picks a verb and renders.

### Frontend

`appendAgentTraceEvent` dedupes by `seq`. For a `tool_result`, finds the most recent same-key event and replaces it in place (one slot transitions "Searching" → "Found N"). Dot states: error (red), miss (hollow + muted), done (solid), pending (hollow).

### Metadata allow-list

`ALLOWED_METADATA_KEYS` in `main.py` mirrors a Set in `trace-metadata.ts`. Defense-in-depth to prevent raw query/chunk text from leaking to the UI. A new key must be added to both sides.

---

## 14. Prompt Management

All LLM prompts are externalized to `config/model_configs.toml` and loaded from DynamoDB at Lambda cold-start.

### Entries

- `agenticRetrieval` — system prompt for Phase A (the research loop tool instructions).
- `answerStream` — system prompt for Phase B (answer generation with citation formatting rules).
- `ragResponse` — legacy RAG generation (unused in GraphRAG path).
- `faqResponse` — legacy FAQ synthesis (unused in GraphRAG path).

### Iteration workflow

```bash
# Edit the prompt, then push to DynamoDB:
AWS_PROFILE=widor AWS_REGION=us-east-1 uv run tools/upload_model_configs.py --only agenticRetrieval
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
embed.py → work: embedded/{doc_id}.json (Titan v2 1024-dim; case_law SKIPPED)
       ▼
load.py → Neptune graph (11 CLI phases of batched Cypher)
```

### Load phases

| CLI step | Name | What it does |
| ---: | --- | --- |
| 1 | Scaffold | Framework nodes + DERIVED_FROM edges |
| 2 | Document Nodes | MERGE doc nodes with all properties |
| 3 | Cross-References | CITES + IMPLEMENTS edges from statute_refs |
| 4 | Statute Hierarchy | PART_OF edges (section → chapter) |
| 5 | Topic Merging | LLM-driven synonym clustering |
| 6 | Hierarchy Links | BELONGS_TO, HAS_SUBSECTION, COVERS_TOPIC |
| 7 | Chunk Nodes | MERGE chunks + EXTRACTED_FROM edges |
| 8 | Stub Resolution | Create stub nodes for unresolved citations |
| 9 | Vector Upserts | Upsert embeddings (one per query, 8 threads) |
| 10 | Semantic Edges | LLM-classified RELATED_TO/SUPPLEMENTS/etc. |
| 11 | Orphan Cleanup | Delete chunks/edges with no parent |

**CLI step ≠ function name.** `--start-phase 7` runs the function `phase_8_chunks`. Log references to "phase 8" mean the function name, not the CLI step.

### Cache-aware resume

`extract.py`/`embed.py` skip already-processed docs unless `--force`. All three accept `--source-filter <prefix>` for scoped runs. `load.py` has `--start-phase`/`--stop-after-phase`.

### When to re-ingest

- **Edge logic changes (phases 3/4/10):** full re-ingest required. MERGE doesn't retroactively remove stale edges.
- **Chunking or embedding changes:** full re-ingest. Run `purge_orphan_chunks.py` after (MERGE never deletes high-index orphans).
- **Property-only mutation (e.g., edition_year):** scoped `--source-filter` run is sufficient.

### Running on Fargate

```bash
./tools/ingestion/scripts/run_full_ingest.sh          # full pipeline
./tools/ingestion/scripts/run_fargate.sh extract      # single phase
./tools/ingestion/scripts/run_fargate.sh load --start-phase 5 --stop-after-phase 8
```

Docker image must be `--platform linux/amd64` (Fargate requirement on Apple Silicon builds).

### Cleanup scripts (dry-run by default)

| Script | Purpose |
| --- | --- |
| `purge_orphan_chunks.py` | Delete chunks with no slot in current embedded JSON |
| `clean_stale_extracts.py` | Delete artifacts for missing/drifted raw docs |
| `patch_metadata_authority.py` | Fix `authority_level` in raw `.metadata.json` |
| `patch_work_authority.py` | Fix cached `authority_level` in extracted/embedded JSONs |

---

## 16. Sessions, Auth, and Chat History

Cognito-authenticated users get a sidebar of past chats. Two DynamoDB tables:

- **SessionTable** — PK `sessionId`; `userIdIndex` GSI (PK `userId`, SK `lastMessageAt`); `connectionId` GSI for WebSocket.
- **ChatHistoryTable** — PK `queryId`; `sessionIdKey` GSI (PK `sessionId`, SK `timestamp`).

All routes live in one Powertools Lambda (`backend/lambdas/chat_api/main.py`): `POST /session`, `GET /sessions`, `PATCH /session/{id}`, `DELETE /session/{id}`, `POST /session/{id}/message`, `GET /session/{id}/history`, `POST /session/{id}/feedback`.

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

Always use `--profile widor`. Always run `cdk diff` before deploy.

### Deploy checklist

```bash
bun install
bun run bundle                          # copy Python lambdas to infra/bundle/
cd infra
AWS_PROFILE=widor AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=widor AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```

### First-time setup

1. `cdk bootstrap` us-east-1.
2. `bun install` → `bun run bundle` → `cdk deploy`.
3. Seed `ModelConfigTable`: `uv run tools/upload_model_configs.py`.
4. Sync FAQs: `sync_faq_bucket.sh` → `seed_faq_url_table.py`.
5. Run full ingestion pipeline.

### Environment gotchas

- **SSL on macOS:** `export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")` then set `AWS_CA_BUNDLE=$CERT`.
- **Bedrock model IDs:** require full inference-profile format (`us.anthropic.claude-sonnet-4-6`).
- **Neptune scaling:** 32 mCU for runtime, 128 mCU during full re-ingestion. Scale back after — 128 mCU is 4× cost.
- **`AWS_REGION` in shell:** scripts default to us-east-1 but `AWS_REGION` overrides. Always set explicitly.

### Observability

A single `query_id` threads through handler → tools → WebSocket → DynamoDB. To debug a failed query:
1. DynamoDB `get-item` by `queryId` for the stored question/answer/feedback.
2. CloudWatch `filter-log-events` on the query_id in the Lambda log group.
3. Look for: structured log events (`agent_loop_start`, `agent_tool_call`, `agent_tool_result`, `agent_loop_complete`).

---

_Last updated: 2026-06-26. When you change a subsystem, update its section here._
