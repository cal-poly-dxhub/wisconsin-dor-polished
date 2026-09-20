# GraphRAG Engineering Guide

The single architecture reference for the GraphRAG system powering the Wisconsin DOR
property-tax chatbot: runtime architecture, data model, ingestion pipeline, and
operational procedures. Audience: developers inheriting or extending this codebase.

Everything below describes the system **as of 2026-09-19**. Companion docs cover
subsystems in depth and are linked from the relevant section; this file is the map.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [The Request Lifecycle](#2-the-request-lifecycle)
3. [Phase A: The Research Loop](#3-phase-a-the-research-loop)
4. [The Adequacy Judge](#4-the-adequacy-judge)
5. [Phase B: Answer Streaming](#5-phase-b-answer-streaming)
6. [The Neptune Graph Data Model](#6-the-neptune-graph-data-model)
7. [Neptune Analytics Constraints](#7-neptune-analytics-constraints)
8. [PDF Extraction and Chunking](#8-pdf-extraction-and-chunking)
9. [The FAQ Layer](#9-the-faq-layer)
10. [WPAM Edition Recency](#10-wpam-edition-recency)
11. [Case Law](#11-case-law)
12. [Worksheets and Flowcharts](#12-worksheets-and-flowcharts)
13. [Citations and Source Cards](#13-citations-and-source-cards)
14. [WebSocket Streaming and Contracts](#14-websocket-streaming-and-contracts)
15. [Agent Trace UI](#15-agent-trace-ui)
16. [Prompt Management](#16-prompt-management)
17. [The Ingestion Pipeline](#17-the-ingestion-pipeline)
18. [Sessions, Auth, and Chat History](#18-sessions-auth-and-chat-history)
19. [Deployment and Operations](#19-deployment-and-operations)

---

## 1. System Overview

The chatbot answers Wisconsin property-tax questions using a single **agentic retrieval
Lambda** invoked directly by EventBridge. There is no Step Function and no separate
streaming Lambda.

```
Chat API (POST /session/{id}/message)
    │ emit EventBridge: ChatMessageReceived
    ▼
AgenticRetrieval Lambda
    Phase A: Claude tool loop over Neptune Analytics
    Judge:   post-retrieval adequacy check (Haiku)
    Phase B: streamed answer tokens over WebSocket
    │
    ▼ WebSocket (API Gateway)
Frontend (Next.js)
```

Key facts:

- **Region / stack:** us-east-1, `WisconsinBotGraphRAG`.
- **Neptune graph:** `g-svphgiu4k6`. It is **not a CDK resource** — it is pinned by the
  `neptuneGraphId` context in `infra/cdk.json` and synth fails without it. See
  [`infra/README.md`](../infra/README.md) for the blue/green and rollback procedure. The
  previously CDK-owned graph `g-ndvl4j73v4` was deleted on 2026-09-19.
- **Agent model:** `us.anthropic.claude-sonnet-4-6` (default; `AGENTIC_MODEL_ID` env var).
- **Judge model:** Claude Haiku 4.5 (`ADEQUACY_JUDGE_MODEL_ID`).
- **Embedding:** Titan Embed Text V2, 1024 dimensions.
- **Package layout:** the Lambda's entry point is `handler.py` (there is no `main.py`).
  Logic lives in `loop/`, `agent_tools/` (+ `agent_tools/stages/`), `graph/`, `streaming/`
  and `tracing/`, plus flat helpers (`config.py`, `prompt.py`, `faq.py`, `case_law.py`,
  `wpam_dedup.py`, `rag_documents.py`, `chat_history.py`, `adequacy_judge.py`,
  `flowchart_router.py`, `flowcharts.py`, `worksheets.py`, `disambiguation.py`).
- **There is no `useGraphRAG` context flag.** The old Step Function path (separate
  classifier / retrieval / streaming Lambdas) was removed along with the flag that
  selected it; deploy commands pass only `-c stackName=WisconsinBotGraphRAG`.

---

## 2. The Request Lifecycle

1. **Frontend → API.** `POST /session/{id}/message` emits an EventBridge event
   `wisconsin-dor.chat-api:ChatMessageReceived` carrying a
   `MessageEvent {query, query_id, session_id, persona}`. The AgenticRetrieval Lambda
   validates the incoming `$.detail` against `UserQuery`.
2. **EventBridge → Lambda.** Rule `TriggerGraphRAGMessageProcessing` forwards `$.detail`
   to the AgenticRetrieval Lambda (`retryAttempts: 0`, `maxEventAge: 3 min`).
3. **Clarification resolution.** `resolve_pending_clarification` (`adequacy_judge.py`)
   checks whether the previous turn ended with a judge clarification and whether this
   message answers it (a chip label or a short typed value). If so it folds the answer
   back into the original question deterministically, so classification, retrieval and
   the flowchart router all see the real question. The raw user message is still what
   gets persisted.
4. **Phase A (research loop).** Claude calls tools against Neptune until it calls
   `prepare_answer(cited_doc_ids, answer_plan)`. Returns cited documents, chunks and an
   answer plan — no answer text yet.
5. **Adequacy judge.** With `ADEQUACY_JUDGE_ENABLED=true`, Haiku reads the question,
   history, the answer plan and all retrieved chunks and returns a `Finding`
   (ANSWER / CLARIFY / DECLINE). See §4.
6. **Phase B (answer stream).** `converse_stream_with_cache()` with NO tools generates
   the answer token by token over WebSocket. The Finding is injected into the context as
   a `## RETRIEVAL FINDING` block; Phase B always streams.
7. **Resource cards.** Before the answer stream, document cards, FAQ cards and (when a
   chart is in play) the flowchart are sent over WebSocket (`streaming/delivery.py`).
   A CLARIFY finding also sends a `choices` message.
8. **Persistence.** `save_chat_history` (`chat_history.py`) writes query, answer,
   documents, FAQs, any pending clarification and the trace log to DynamoDB after
   streaming completes.
9. **Frontend.** Each WebSocket frame is Zod-validated. Fragments drive the streaming
   markdown; `documents`/`faq` frames populate source cards; `agent-event` frames drive
   the live trace; `choices` renders the clarification block.

---

## 3. Phase A: The Research Loop

`run_agentic_loop` in `backend/lambdas/agentic_retrieval/loop/phase_a.py`.

### Pre-loop steps

- **`_auto_refine`** (only when `chat_history` exists): rewrites context-dependent
  follow-ups and extracts an optional `target_wpam_year`. A deterministic call to
  `executor._auto_refine`, **not** a tool the model can invoke.
- **Flowchart router** (`flowchart_router.py`): some questions are decision-procedure
  questions for which the WPAM publishes a step-by-step chart. Those charts are
  flattened raster images the text pipeline cannot retrieve, so the router embeds the
  query (Titan v2) and scores cosine similarity against each chart's
  `router_description`. On a clear match it seeds a `get_flowchart` tool result into
  turn-0 context. Candidates are scored cheapest-signal-first — the verbatim user
  message, then the question underneath a chip / one-liner follow-up, then the
  auto-refined query — and evaluation short-circuits on the first seed. The gate is
  `top >= SEED_ABS` **and** `(top - second) >= SEED_GAP` **and**
  `top >= SEED_MARGIN * second`; defaults live in code (`SEED_ABS_DEFAULT = 0.45`,
  `SEED_GAP_DEFAULT = 0.20`, margin 1.6) and are overridable via `FLOWCHART_SEED_ABS` /
  `FLOWCHART_SEED_GAP` / `FLOWCHART_SEED_MARGIN`. A missing sidecar simply disables that
  chart's seed.
- **Deterministic `faq_search` seed:** bypasses Claude entirely, running the verbatim
  (or refined) query against the Bedrock FAQ KB. Seeded into the message list as a
  synthetic `toolUse`/`toolResult` pair (`toolUseId="faq_search_turn0"`).
- **Deterministic `vector_search` seed:** likewise seeded (`toolUseId="vector_search_turn0"`)
  so the first real turn already has graph context.
- **High-confidence steering:** if the top FAQ score ≥ `FAQ_SCORE_THRESHOLD` (0.70), a
  user message tells Claude to treat the FAQ as the primary source of truth and use
  graph results only to supplement. This steers; it never short-circuits the loop.

> **The pre-loop query classifier is legacy and OFF.** `disambiguation.py` still contains
> the Haiku scope / property-type / topic-shift classifier, but `ENABLE_DISAMBIGUATION`
> and `ENABLE_TOPIC_SHIFT` both default to `false`, and `SCOPE_GATE_ENABLED=false` in
> production. Scope and clarification are owned by the post-retrieval adequacy judge
> (§4). Keep the module until the judge has a longer run of production traffic; flipping
> the flags back restores the old gate byte for byte.

### The tool loop

`MAX_TURNS = 10` (hardcoded in `config.py`). Each turn: append assistant message →
execute all `toolUse` blocks → append `toolResult`s. The loop terminates when Claude
calls `prepare_answer`.

The loop drives Bedrock via `converse_with_cache` (`streaming/bedrock.py`), which wraps
`invoke_model` with the native Anthropic Messages API and `cache_control` prompt-caching
breakpoints — **not** the Bedrock Converse API. Inference config: `maxTokens=4096`,
`temperature=0.0` (model-aware: Sonnet 5 / Opus 5 reject `temperature` and take effort
via `output_config` instead).

**Turn budget:** at `turn == 7` (the 8th turn) a "running low on turns — call
prepare_answer NOW" warning is injected. If the loop exhausts without `prepare_answer`,
a degraded fallback returns the last assistant text; with the judge on, that fallback
becomes the plan the judge rules on.

### Tool set — 14 tools

`TOOL_DEFINITIONS` in `agent_tools/definitions.py` exposes exactly these:

| Tool | Purpose |
| --- | --- |
| `faq_search` | Bedrock FAQ KB retrieve. Already seeded at turn 0; the description tells the model not to call it again. |
| `vector_search` | Titan-embed query → Neptune vector index → 6× over-fetch → dedup/diversity/authority stages → backfill arms. See [auto-backfill](auto-backfill.md) for the full 10-stage pipeline. `top_k` default 10, max 25. |
| `search_document` | Semantic search within one document's chunks. Global vector search (over-fetched, `fetch_k` ~800) filtered to the target `doc_id`. |
| `list_sections` | Table of contents for a document: distinct headings with chunk counts and page ranges. Deterministic graph traversal — no vector search. |
| `get_section` | Chunks from a specific section by exact heading match; optional `subsection` param fetches a numbered `(N)` chunk verbatim. Two modes (see below). |
| `get_document` | Node lookup by ID; falls back to vector search on miss. |
| `get_neighbors` | Graph traversal from a node; accepts an `edge_types` filter; optionally semantically ranked. |
| `find_case_law` | Search CaseLaw nodes by name/citation, optionally scoped to a statute. |
| `fetch_case_opinion` | Fetch full opinion `.txt` from S3 for a case-law stub. |
| `list_worksheets` | Registry of the TID base-value worksheet sidecars. |
| `get_worksheet` | One worksheet's structured JSON (labels / formulas described, never evaluated). |
| `list_flowcharts` | Registry of the hand-transcribed WPAM decision flowcharts. |
| `get_flowchart` | One flowchart sidecar (nodes, edges, authorities). Often already seeded by the router. |
| `prepare_answer` | **Terminal tool.** Claude declares `cited_doc_ids` + `answer_plan`. The loop exits. |

> **Retired tools.** `refine_query`, `clarify`, `cite_documents`, `get_authority_chain`
> and `list_framework_docs` no longer exist anywhere in the codebase. Query refinement is
> the deterministic pre-loop `_auto_refine`; clarification is the judge's CLARIFY verdict;
> citation is `prepare_answer`'s `cited_doc_ids`. Older notes and the frontend trace
> metadata mirror still carry vestiges of these names — see §15.

### Key behaviors

- **`list_sections` + `get_section` (structural browsing):** `list_sections` is a
  deterministic graph traversal. `get_section` has two modes: **without** a `query` it
  returns all chunks in document order; **with** a `query` it fetches stored embeddings
  from Neptune (`neptune.algo.vectors.get`), computes cosine similarity against the query
  embedding, applies z-score filtering (`_Z_THRESHOLD = 0.5`, always keeps at least the
  top result; falls back to plain cosine sort on a flat distribution), and returns up to
  `top_k` (default 5, max 10) ranked chunks.
- **6× over-fetch + diversity cap:** WPAM editions dominate the vector space, so
  `vector_search` always requests `top_k * 6` chunks. After WPAM dedup a per-document
  diversity cap (`DIVERSITY_CAP_PER_DOC`, default 3) prevents any source from crowding
  out others, and an authority quota reserves top_k slots for primary sources.
- **Tool exceptions → error result:** any tool exception becomes an `{"error": ...}`
  tool result fed back to Claude so it self-corrects; it never crashes the Lambda.
- **`cited_doc_ids` is the authoritative sidebar:** only documents Claude explicitly
  cites via `prepare_answer` become source cards, not all discovered docs.

### The `vector_search` pipeline

`VECTOR_SEARCH_STAGES` in `agent_tools/pipeline.py` is exactly ten stages, in order:

`auto_refine` → `neptune_search` → `wpam_dedup` → `diversity_cap` → `authority_quota` →
`authority_tiebreak` → `citation_extraction` → `statute_backfill` → `caselaw_backfill` →
`broad_discovery`

Stages 1–6 shape the primary result set; 7–10 are the backfill arms. The `auto_enrichment`
stage that once sat between 6 and 7 was removed on 2026-09-19 (it fetched neighbours into
a context field nothing read). Full mechanics: [auto-backfill](auto-backfill.md).

---

## 4. The Adequacy Judge

`backend/lambdas/agentic_retrieval/adequacy_judge.py`. Flag: `ADEQUACY_JUDGE_ENABLED`
(true in production since 2026-09-18); `SCOPE_GATE_ENABLED=false` alongside it.

**Why it exists.** The pre-loop classifier refused questions that name a court case
("What is the Markarian case?") because a case name carries no topic words and the
classifier never sees the corpus. Now every query runs the research loop and a judge
decides afterwards, with the evidence in front of it. Time is lost only on genuinely
out-of-scope questions.

**Mechanism.** `judge_answer_plan` (Haiku 4.5, ~5 s) reads the question, chat history,
the agent's answer plan, and all retrieved chunks (cited ones labelled and first) and
returns a `Finding`:

| Verdict | Meaning | Effect |
| --- | --- | --- |
| `ANSWER` | The evidence supports the plan | Phase B streams the answer |
| `CLARIFY` | The sources fork on a fact the question does not state | Phase B streams what it can; the clarification is sent as `choices` chips |
| `DECLINE` | Zero relevant evidence **and** the subject is outside property tax | Phase B streams a short honest no |

The Finding also carries what the evidence supports, what the plan claims without
support, and for CLARIFY a clarification derived from the sources (an `axis`, one
question, 2–5 options plus an escape). It is rendered into the Phase B context as a
`## RETRIEVAL FINDING` block (`render_finding_block`), and the `answerStream` prompt tells
the model the finding wins wherever the two disagree. **The judge never writes user-facing
text** and Phase B always streams — one prose path.

**Guards in code, not just prompt.** A DECLINE is downgraded to ANSWER when the judge
itself reports `relevant_material_found: true`; a CLARIFY is downgraded when
`question_omits_the_fact` is explicitly `false`; an unrecognized verdict degrades to
ANSWER. The whole path is **fail-open**: `judge_answer_plan` never raises, and any error
yields `Finding(verdict="ANSWER", ...)`.

**Clarification round trip.** CLARIFY options travel on the existing `choices` WebSocket
message, which carries `question`, `axis` and `kind` (`clarification` | `disambiguation`)
so the frontend can render one lifted clarification block between the answer and the
sources. The pending clarification is stored on the chat-history item;
`resolve_pending_clarification` folds a chip click or a short reply back into the original
question on the next turn — deterministic, and a no-op when nothing is pending.

**Rollback.** Flip `ADEQUACY_JUDGE_ENABLED=false` / `SCOPE_GATE_ENABLED=true` in
`graphrag-messages-stack.ts` and redeploy; the classifier gate returns byte for byte.

---

## 5. Phase B: Answer Streaming

Orchestrated by `handler.py`, implemented in `loop/phase_b.py`. After `prepare_answer`
returns (and the judge has ruled):

1. **Opinion backfill** (`handler.py`): for up to `_OPINION_BACKFILL_CAP` (3) cited
   case-law stubs not already fetched, pull the full opinion `.txt` from S3 — **before**
   building RAG documents so the stream has substantive content. See
   [auto-backfill](auto-backfill.md).
2. **Builds resource cards** from `cited_doc_ids` (`build_rag_documents` in
   `rag_documents.py`; wire mapping in `streaming/delivery.py`).
3. **Sends resource cards** over WebSocket (documents batched to stay under the frame
   budget, then the FAQ card, then the flowchart if one is in play).
4. **Calls `stream_answer`** with NO tools — research context + `ANSWER_STREAM_SYSTEM_PROMPT`
   + the persona suffix + the `## RETRIEVAL FINDING` block.
5. **Fragment buffering:** text deltas are batched (`_FRAGMENT_MIN_SIZE = 30` chars)
   before sending as WebSocket fragments.
6. **Heartbeat:** a background thread (`loop/heartbeat.py`) sends keepalive pings every
   `WS_HEARTBEAT_INTERVAL` (15) seconds so API Gateway's idle timeout does not kill the
   connection.

### Statute link pages are filled deterministically

Two mechanisms keep `[§ 74.37](doc:statutes-74)` from opening page 1 of a 200-page
chapter PDF:

- **`statute_section_pages`** (`loop/phase_b.py`) rebuilds a section → page index from
  the cited chunk text (with OCR-space normalization), cached per container. It covers
  cited chapters plus any known corpus chapter referenced in the chunks or the plan —
  there is no `>= 70` chapter filter (that filter used to drop chapters 19/60/61). The
  index is also shown to the model in the answer context.
- **`repair_citation_links`** (`loop/link_repair.py`) is a deterministic post-generation
  pass over the finished Markdown. It repairs conflated statute / admin-rule links and
  fills `#page=N` from the section index (`kind="paged"`). It **never invents** a page:
  a section it cannot resolve is left page-less, and the frontend then routes a
  page-less statute link that names a section to the legislature's per-section page
  (`document/statutes/74.37`).

**Fallbacks (`handler.py`):** if Phase B streaming throws, a non-streaming
`bedrock.converse()` regenerates the answer for DB persistence; with no WebSocket
connection at all the answer is generated non-streaming purely to persist it.

---

## 6. The Neptune Graph Data Model

**Engine:** Neptune Analytics (`neptune-graph`), graph `g-svphgiu4k6`, us-east-1,
1024-dim vector index, 32 m-NCU at rest (128 for a full load), IAM auth, public
connectivity (no VPC — IAM is the only protection).

### Node labels the loader actually creates

- **Framework** — authority-level grouping (`FW-STATUTES`, `FW-WPAM`, …).
- **Document, under a doc-type label** — `Constitution`, `Statute`, `CaseLaw`,
  `AdminRule`, `AssessmentManual`, `FAQ`, `Guide`, `Advisory`, `Template`,
  `FormInstruction`, `IAOStandard`, `USPAPStandard` (the `doc_types` map in
  `ingest_config.yaml`). Properties: `title, source_key, summary, source_url, doc_type,
  authority_level, citation, effective_date, edition_year`.
- **Chunk** — `text, doc_id, source_url, chunk_index, s3_key, start_page, end_page,
  heading, subheading, edition_year` + a 1024-dim vector in the index.
- **Stub** — identity-only `Statute` / `AdminRule` / `CaseLaw` nodes created from
  citation regex matches. "Stub" is a node **property** (`n.stub = true`), not a label.

> **There are no `Topic` nodes.** No load phase has created one for a long time; the
> orphan-Topic garbage collection in Phase 9 was removed on 2026-09-19 because it only
> ever deleted zero rows. `extract.py` still writes `topics` and `implements_refs` into
> the extracted JSON and `load.py` still carries them in `DOC_METADATA_KEYS`, but nothing
> reads them into the graph — dead data, tracked in [tasks](tasks.md).

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

`authority_level` defaults to `None` for unresolved docs (never a number — a prior
default of `6` mislabeled 607 nodes as FAQs).

### Edge types — exactly what `load.py` writes

| Edge | Direction | Written by | Meaning |
| --- | --- | --- | --- |
| `DERIVED_FROM` | Framework → Framework | Phase 1 | Authority precedence chain (e.g. IAAO → WPAM) |
| `BELONGS_TO` | Doc/stub → Framework | Phases 1, 2, 4 | Framework membership |
| `PART_OF` | Section → Chapter, Subsection → Section | Phase 3 | Statute hierarchy |
| `EXTRACTED_FROM` | Chunk → Doc | Phase 5 | Chunk provenance |
| `CITES` | Chunk → Statute/AdminRule; Statute → CaseLaw | Phases 5, 6 | Citation reference |
| `DEFINED_BY` | Statute/AdminRule stub → Chunk | Phase 7 | Resolves a citation stub to the chunk that defines it |

That is the complete list. **`IMPLEMENTS`, `HAS_SUBSECTION` and `COVERS_TOPIC` do not
exist.** `HAS_SUBSECTION` was written from a `_parent_id` key nothing ever set and was
removed from Phase 4 on 2026-09-19; the other two were never written by any current
phase. `get_neighbors` does not offer them as `edge_types` options. Documents carry
doc-type labels but are matched generically by `id` in most retrieval queries.

---

## 7. Neptune Analytics Constraints

These constraints recur across the codebase and explain otherwise-baffling patterns.

### StreamingBody response

`neptune-graph`'s boto3 `execute_query` returns results inside a streaming body under
`response["payload"]`, not a pre-parsed dict. All query helpers decode via
`json.loads(payload.read())`. Reading `response["results"]` silently returns `[]`.

### No parameterized CALL args

Neptune Analytics rejects `$parameters` inside `CALL` procedure arguments and in
variable-length path bounds. So:

- `vector_search` inlines the 1024-float embedding and `topK` as string literals into
  Cypher.
- Load Phase 8 (vector upserts) inlines each embedding and upserts one vector per query,
  parallelized over 8 threads.

### No WHERE on topKByEmbedding

`topKByEmbedding` has no pre-filter capability — you cannot filter by properties (e.g.
`edition_year`) before the vector search. This drives the over-fetch + post-filter dedup
pattern for WPAM recency.

### Throttling signals and retries

Neptune signals overload via both `ThrottlingException` and `UnprocessableException` with
a message about suppressed retries. The two clients retry differently, and both numbers
matter when you are reading a log:

| Client | Attempts | Backoff cap | Catches |
| --- | ---: | ---: | --- |
| Retrieval Lambda — `NeptuneClient.query` (`graph/neptune_client.py`) | 3 (`max_retries` default) | 30 s (`min(30, 2**attempt)`) | `"Throttling" in str(e)` only |
| Loader — `execute_query` (`tools/ingestion/load.py`) | 8 | 60 s (`min(60, 2**attempt)`) | `ThrottlingException` **or** `UnprocessableException` |

The Lambda is deliberately the impatient one: a 120 s Lambda timeout means a long retry
chain is worse than failing the tool call and letting the agent self-correct.

### UNWIND byte cap

Batch writes (`UNWIND $rows`) must cap by cumulative text bytes
(`PHASE_5_MAX_BYTES_PER_FLUSH = 50_000`, in `load.py`) in addition to row count.
Count-only caps don't prevent per-query OOM. Neptune at 32 m-NCU OOMs on large batches
during full re-ingestion — scale to 128 m-NCU for loads.

### Sizing floor

16 m-NCU is rejected by the service ("storage memory constraints") — confirmed
2026-09-18 on a **fresh** graph too, so it is the corpus size, not reload bloat. **32 is
the floor**, 128 is required for a full load. The lever if 16 ever needs to fit: drop the
15 non-current WPAM editions.

---

## 8. PDF Extraction and Chunking

`tools/ingestion/chunking/` turns PDFs into page-tracked chunks. Entry point:
`process_pdf_from_s3()` in `pdfChunker.py`, invoked from `extract.py`.

**Extraction** is PyMuPDF-first: `extract_with_pymupdf()` does font-metric
title/header/body classification and table detection, gated by `extraction_looks_good()`
(≥5 lines, ≥1 non-empty, avg stripped length ≥3). The extractor emits `header_split`
(text split on `<titles>` markers) and `line_page_mapping` (`list[(line, 1-based page)]`),
the basis for per-line page tracking.

> **Textract fallback is skipped unless `TEXTRACT_STAGING_BUCKET` is set.** The async
> Textract path needs a staging bucket for its output, and the Fargate task definition
> deliberately leaves that env var unset. When PyMuPDF fails the quality gate and no
> staging bucket is configured, `pdfChunker` logs the skip and keeps the PyMuPDF result
> rather than failing the document. The corpus is digital-native, so this is rare — but
> it means "Textract fallback" is opt-in infrastructure, not an automatic safety net.

**Chunking** routes each PDF by doc_id prefix to one of four strategies — `statute`
(cap 3500), `admin_rule` (3500), `wpam` (2500), `general` (2500) — then runs a shared
quality pass (boilerplate stripping, TOC removal, clean-plaintext, cap enforcement) plus
WPAM-only filters. Case law and non-PDF text take separate paths.

> **The full story lives in [chunk-quality-controls](chunk-quality-controls.md)** —
> per-strategy boundary detection, the boilerplate/TOC/WPAM filters, the case-law and
> non-PDF paths, the complete parameter table, and the design rationale.

Three invariants worth stating up front:

- **Per-line page tracking:** each buffered line is a `(text, page)` tuple; a chunk's
  `start_page`/`end_page` is the min/max of its buffer pages. No substring-matching
  reconstruction.
- **Embedding alignment:** the 2500-char default cap stays well below Titan Embed v2's
  ~8000-char silent-truncation threshold, so the full chunk text is vector-represented.
- **TOC detection is structural.** The WPAM chunker prepends "Chapter N …" to every
  chunk, and an old heuristic treated "starts with Chapter N *and* contains a
  digit-dash-digit token" as a table of contents. Nearly every Manual page carries a
  footer like "7-40", so 89% of Volume 1 was silently discarded (Task 72). A chunk is now
  a TOC only on structural evidence (≥3 leader-dot lines, or a high share of short bare
  page-number lines), tested on body lines only.

---

## 9. The FAQ Layer

A Bedrock FAQ Knowledge Base (Titan v2, `ChunkingStrategy.NONE` — one file = one Q&A pair).

### Turn 0 is deterministic

The FAQ search runs as hardcoded Python (`faq_search_direct`) on the verbatim query,
bypassing Claude. Claude paraphrasing the query hurt KB recall.

### Scoring

`FAQ_SCORE_THRESHOLD = 0.70`. At or above this threshold the FAQ is treated as the
primary source of truth. The agentic loop **always continues** into the graph — there is
no short-circuit. The FAQ anchors the answer while the graph supplements it with citable
authority.

### FAQ URL resolution

Each FAQ's public source page is resolved via `_lookup_faq_url(question)` — a DynamoDB
`get_item` against `FaqUrlTable`. The normalizer in the Lambda and the seed script must
stay byte-identical (tested by `test_faq_question_normalizer_matches_seed_script`).

### Refreshing FAQs

Multi-region operation: master FAQ files in `wis-faq-bucket` (us-west-2), KB +
`FaqUrlTable` in us-east-1. Run `sync_faq_bucket.sh` to copy + trigger KB ingestion, then
seed `FaqUrlTable` with `ops/seed_faq_url_table.py`.

---

## 10. WPAM Edition Recency

The graph contains ~15 WPAM editions (2011–2026). Only the current edition should be
cited unless the user asks about a specific year.

### The problem

`topKByEmbedding` has no pre-filtering. With ~15 editions of semantically identical
content, a naive top_k returns mostly old-edition chunks.

### The solution: over-fetch + two-pass dedup

1. **`current_wpam_year`** — dynamically resolved from Neptune (`max(edition_year)`
   across FW-WPAM docs) as a lazy cached property.
2. **6× over-fetch** — `vector_search` always requests `top_k * 6` chunks regardless of
   whether a `target_wpam_year` is set.
3. **Two-pass dedup** (`wpam_dedup.py`):
   - Pass 1 (heading collapse): groups WPAM chunks by normalized heading, keeps one per
     group (newest).
   - Pass 2 (edition filter): only `current_wpam_year` (+ `target_year` if set) survives.
4. **Prompt reinforcement** — instructs Claude to cite only the current edition.

### Volume 2 is not an edition

WPAM Volume 2 (Residential, Apartments and Agricultural) is registered with a year-less
doc_id and a null `edition_year`, so the per-heading edition dedup and `wpam_latest_only`
never treat it as a competing edition of Volume 1.

### Historical queries

When the user asks about a specific year, the pre-loop `_auto_refine` step extracts
`target_wpam_year`; `allowed_years = {current, target}`.

### `edition_year` stamping

Extracted from the doc_id (last 4-digit group, plausibility-gated). Denormalized onto
every Chunk so dedup needs no Neptune join.

---

## 11. Case Law

Case law is **thin citation stubs** in the vector space — the opinion text lives in S3,
not in embedded chunks, so cases rarely surface from `vector_search` directly. They are
reached by traversal, by text-based citation extraction, or by the explicit tools.

### Discovery paths

See [auto-backfill](auto-backfill.md) for the full mechanics. In brief:

1. **Direct citation resolution** (`citation_extraction` stage): retrieved chunk text is
   regex-scanned for citation patterns and resolved against CaseLaw node `citation`
   properties.
2. **Statute → case-law backfill** (`caselaw_backfill` stage): statute stubs discovered
   in the results are traversed via
   `(:Statute)<-[:CITES]-(:Chunk)-[:EXTRACTED_FROM]->(:CaseLaw)` to surface chunks of
   cases that cite them.
3. **Explicit tools:** `find_case_law` (search by name/citation, optionally scoped to a
   statute) and `get_neighbors` on a statute with `edge_types=["CITES"]`.

> A "neighbor-doc citation discovery" path was designed and mostly deleted on 2026-09-19;
> only `get_cases_for_subsections` survives, unwired. See the "Not wired" note in
> [auto-backfill](auto-backfill.md).

### Citation extraction

Three regex patterns (`extract_citations` in `agent_tools/executor.py`) cover Wisconsin
formats: `\d+ Wis. 2d \d+`, `\d+ N.W.2d/3d \d+`, `\d{4} WI [App] \d+`. Resolution:
`MATCH (n:CaseLaw) WHERE n.citation IN $citations`.

### Opinion access

Full opinion text lives in S3 (`raw/case-law-{slug}/...txt`). `fetch_case_opinion`
fetches on demand. Cards link to CourtListener / Google Scholar (not S3 — `.txt` has no
page anchors).

### Post-answer opinion backfill

After `prepare_answer`, up to 3 cited-but-unfetched case-law stubs have their opinions
fetched from S3, so the streamed answer has substantive case-law content.

### Dedup and integrity

Case law is ingested from statute-PDF hyperlinks plus a Google Scholar fallback, which
historically produced parallel-citation duplicates and two citation→text mis-assignments.
Load Phase 2 has a doubled-title guard, Phase 10 asserts integrity, and
`ops/purge_dedup_losers.py` + `dedup/losers.json` keep a full load from resurrecting
nodes a dedup pass deleted.

---

## 12. Worksheets and Flowcharts

Two families of content that do **not** live in the graph, served to the agent through
dedicated tools reading S3 sidecars.

| | TID worksheets | Decision flowcharts |
| --- | --- | --- |
| Source | 4 TID base-value `.xlsx` forms | 6 WPAM raster decision charts |
| Sidecar | `s3://{raw-bucket}/worksheets/{id}.json` | `s3://{raw-bucket}/flowcharts/{id}.json` |
| Produced by | `tools/ingestion/worksheets/extract_worksheets.py` (openpyxl, automated) | **hand-transcribed** JSON in `tools/ingestion/flowcharts/data/`, uploaded by `tools/ingestion/flowcharts/upload_flowcharts.py` |
| Registry | `WORKSHEET_REGISTRY` (`worksheets.py`) | `flowcharts.py` (titles + router descriptions) |
| Tools | `list_worksheets`, `get_worksheet` | `list_flowcharts`, `get_flowchart` + the pre-loop router seed |
| Why not the graph | annual re-issue, flattened grids embed badly, exact line/column lookup needed | flattened raster images the text pipeline cannot read |

Both steps are lightweight **local** runs, not Fargate phases, and both are re-run at the
annual refresh. Formulas in worksheets are **described, never evaluated** — the agent
presents the method and lets the user apply their own numbers.

Flowcharts have no automated extractor, so at every annual WPAM refresh you must manually
re-verify each chart's branch logic *and* its page anchors (`pdf_page` / `wpam_page` /
`source.source_url#page=N`) — page numbers shift yearly. `backend/.../tests/test_flowcharts.py`
guards structure but cannot catch a stale page number or an outdated branch. A missing
sidecar degrades gracefully: that chart's seed is simply disabled.

---

## 13. Citations and Source Cards

Citation cards link to each document's **public `source_url`** (docs.legis.wisconsin.gov,
revenue.wi.gov, iaao.org, Google Scholar, …) with a `#page=N` fragment from the chunk's
`start_page`. The `s3_key`/page-range fields still flow through the pipeline for
provenance/debugging, but no presigned URLs are generated — the old `citation_resolver`
Lambda and its `/citation` route were removed after the graph was cleaned of URL-less
legacy documents.

### Click-time flow

1. Card click → `chooseSourceTarget(document)` returns the public `sourceUrl` (or null —
   the card then renders without a link).
2. `appendPageFragment(url, page)` adds `#page=N`; the link opens in a new tab with
   `noopener,noreferrer`.

### Inline prose citations

The answer uses `[Title](doc:documentId#page=N)`. The frontend's
`animated-markdown.tsx::resolveHref` rewrites `doc:<id>#page=N` to a real URL from a
per-message `docUrls` map. Page fragments are filled server-side by `link_repair` (§5); a
page-less statute link that names a section routes to the legislature's per-section page.

### Card presentation

Cards are tinted by document kind with a left accent and a small-caps header label
(statute blue, case law violet, admin rule teal, WPAM green, gov pub / forms amber,
IAAO/USPAP rose, FAQ/news neutral) and grouped by authority (Statutes & Constitution →
Case Law → Admin Rules → WPAM → Gov Pubs & Forms → FAQs & News → Other) with collapsible
headers. Provenance pills ("Semantic match", "Source", "Court opinion") show only in
dev-trace mode. Files: `frontend/src/components/documents/document-card/source-taxonomy.ts`,
`source-group-grid.tsx`.

---

## 14. WebSocket Streaming and Contracts

### Message types

Every shape is defined in `backend/layers/websocket_utils/models.py` and routed by
`send_json` in `backend/layers/websocket_utils/utils.py`:

| streamId | Body type (responseType) | Purpose |
| --- | --- | --- |
| `resources` | `DocumentsMessage` (`documents`) / `FAQMessage` (`faq`) / `FlowchartMessage` (`flowchart`) | Source cards (batched for the frame budget), the FAQ card, and the seeded decision flowchart |
| `answer-event` | `AnswerEventType` (`answer-event`) | `start` / `stop` bookends |
| `answer` | `FragmentMessage` (`fragment`) | Answer text fragments (30-char min buffer) |
| `agent-trace` | `AgentEventMessage` (`agent-event`) | Live agent trace events |
| `error` | `ErrorMessage` (`error`) | Error surfaced to the user |
| `choices` | `ChoicesMessage` (`choices`) | Clarification / disambiguation chips |
| `suggestion` | `SuggestionMessage` (`suggestion`) | Soft nudge; currently only `kind: "topic-shift"` |

`streamId` and `responseType` differ for `agent-trace`→`agent-event`,
`resources`→`documents`/`faq`/`flowchart`. The frontend enum keys off `streamId`; the Zod
discriminated union keys off `responseType`.

**`ChoicesContent` fields.** Beyond `choices: list[str]` it carries three optional
fields, all single words so the camelCase alias generator leaves the wire names unchanged:

- `question` — the single question to put to the user. Absent on the legacy pre-loop
  disambiguation path, whose canned answer text already carries the question.
- `axis` — a short noun phrase naming the fact being asked about ("property
  classification", "assessment year"); used as the block's label.
- `kind` — `"clarification"` (adequacy judge) or `"disambiguation"` (legacy classifier).

The frontend renders question + chips as one lifted clarification block between the
answer and the source cards.

**`FlowchartMessage`** carries the chart's full structure (`nodes`, `edges`,
`authorities`, `start_node`, `disclaimer`, `router_score`) — it is not a graph document,
so it travels here rather than in a citation card.

**Heartbeat is out-of-band.** Keepalive pings are posted as a raw
`{"streamId": "heartbeat", "body": {}}` frame from the heartbeat thread — not a
`WebSocketMessage` subclass, not through the `send_json` router, and dropped by the
frontend *before* Zod validation.

### The shared-contract discipline

Every WebSocket message is validated by `WebSocketMessageSchema.parse()` (Zod
discriminated union). A shape the union doesn't know **drops the entire frame** and shows
an error. Any wire change touches three places together:

1. `backend/layers/websocket_utils/models.py` (+ the `send_json` router in `utils.py`)
2. `frontend/types/message-types.ts` (the `MessageUnionSchema` / `WebSocketMessageSchema`)
3. the `messageHandler` switch in `frontend/src/hooks/use-websocket-chat.ts`

**Adding a `SourceDocument`/`RAGDocument` field** additionally requires:
`RAGDocument` in `backend/layers/step_function_types/models.py`, population in
`agentic_retrieval/rag_documents.py`, wire mapping in `streaming/delivery.py`,
persistence in `chat_history.py`, and `SourceDocumentSchema` in `message-types.ts`.

Use `.nullish()` not `.optional()` for new Zod optional fields — Pydantic serializes
unset `Optional` as `null`, but `z.optional()` accepts only `undefined`. (The frontend
helpers `optStr`/`optInt`/`optNum` already do `.nullish().transform(v => v ?? undefined)`.)

### Batching

`batch_documents_for_ws` (in `websocket_utils.batching`) splits documents into multiple
frames. The enforced budget is `WS_FRAME_BUDGET_BYTES = 100_000` — deliberate headroom
below API Gateway's 128 KB hard limit for the JSON envelope and UTF-8 escape expansion.
Each doc's content is capped at `MAX_DOC_CONTENT_BYTES = 60_000`.

---

## 15. Agent Trace UI

The agent's per-turn reasoning and tool calls stream as a live, collapsible
chain-of-thought.

### Backend

`emit_trace` (`tracing/emitter.py`) is the single emission helper. Hard-gated: returns
immediately if emission is disabled (`config.EMIT_AGENT_TRACE`, wired via
`tracing/runtime.py`) or `ws_server` is None, and swallows all send errors — it never
raises. Emission points: phase events, tool_call/tool_result pairs, reasoning text,
loop_complete, and the `adequacy_judged` event. The backend pre-formats all
human-readable strings and camelCase metadata; the UI picks a verb and renders.

### Frontend

`appendAgentTraceEvent` (`frontend/src/stores/chat-store.ts`) dedupes by `seq`. For a
`tool_result` it finds the most recent same-key event (`traceStepKey`) and, if that event
is still `pending`, replaces it in place (one slot transitions "Searching" → "Found N").
Dot states: error (red), miss (hollow + muted), done (solid), pending (hollow).

### Metadata allow-list — a real and measurable drift

`ALLOWED_METADATA_KEYS` in `tracing/emitter.py` is a **73-key** frozenset. Its frontend
mirror, the `ALLOWED_METADATA_KEYS` Set in
`frontend/src/components/messages/trace-metadata.ts`, has **24 keys**. Both exist as
defense-in-depth so raw query/chunk text cannot leak to the UI.

Two things to know before you touch either:

- The frontend Set only governs the compact trace *summary* line. A key present in the
  backend but missing from the mirror is dropped from that summary but still present in
  the raw payload — so the 73-vs-24 gap is mostly harmless, not 49 broken features.
- The mirror is *also* stale in the other direction: it still lists `autoEnrichedCount`
  and `chainLength`, keys from the removed `auto_enrichment` stage and the retired
  `get_authority_chain` tool, and its header comment points at the long-gone
  `packages/graphrag/lambdas/agentic_retrieval/main.py`. Cleaning that up is a code
  change, not a docs change; it is noted in [tasks](tasks.md).

Worksheet keys are also missing from the backend allow-list, and `tracing/summaries.py`
has no `find_case_law` / `list_flowcharts` branch — both noted as Task 74 follow-ups.

---

## 16. Prompt Management

All LLM prompts are externalized to `config/model_configs.toml` and loaded from DynamoDB
at Lambda cold-start. The TOML is the source of truth; DynamoDB is the runtime store.

### Entries

- `agenticRetrieval` — system prompt for Phase A (the research loop tool instructions).
- `answerStream` — system prompt for Phase B (answer generation, citation formatting,
  and how to honor the `## RETRIEVAL FINDING` block).
- `adequacyJudge` — the judge's rubric and output schema.
- `personaGovernment` / `personaCitizen` — persona suffixes appended to the Phase B prompt.
- `disambiguationClassifier` — the legacy pre-loop classifier; retained for the
  gate-on rollback path only.

`backend/lambdas/agentic_retrieval/_prompt_fallback.py` mirrors these byte-identically as
a cold-start fallback; regenerate it from the TOML whenever a prompt changes
(`test_prompt.py` pins load-bearing phrases).

### Iteration workflow

```bash
# Edit the prompt in the TOML, then push to DynamoDB:
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 uv run python tools/upload_model_configs.py --only agenticRetrieval
```

Hot-reload without redeploy. `cdk deploy` does NOT write prompt content — only the upload
script does. `cdk deploy` is only needed when infra changes (new env vars, permissions).

---

## 17. The Ingestion Pipeline

Four scripts run in sequence against two S3 buckets (raw + work) and the Neptune graph.
Available as Fargate tasks or local execution.

```
scrape/upload → raw/{doc_id}/{doc_id}.{pdf|txt} + .metadata.json
       ▼
extract.py → work: extracted/{doc_id}.json (PDF→chunks, classify, statute_refs, aliases)
       ▼
embed.py → work: embedded/{doc_id}.json (Titan v2 1024-dim; empty case_law stubs SKIPPED)
       ▼
load.py → Neptune graph (10 sub-phases of batched Cypher)
```

### Load phases

`load.py` runs **10 sequential sub-phases**, numbered 1–10 with a 1:1 mapping to their
`phase_N_*` functions (no CLI-step vs. function offset). `--start-phase` /
`--stop-after-phase` take integers in `[1, 10]`.

| # | Name | What it does |
| ---: | --- | --- |
| 1 | Scaffold | Framework nodes, `DERIVED_FROM` edges, statute-family Statute nodes + `BELONGS_TO` |
| 2 | Document Nodes | MERGE per-doc-type labeled nodes with all properties (incl. WPAM `edition_year`); case-law doubled-title guard |
| 3 | Statute Hierarchy | `PART_OF` edges (section → chapter, subsection → parent); MERGE stub parents |
| 4 | Hierarchy Links | Wire orphan `Statute` / `AdminRule` stubs to their framework via `BELONGS_TO` |
| 5 | Chunk Nodes | Purge stale chunks, MERGE Chunk nodes + `EXTRACTED_FROM` + chunk-level `CITES` |
| 6 | Case Law CITES | `(Statute)-[:CITES]->(CaseLaw)` reverse edges |
| 7 | Stub Resolution | `DEFINED_BY` edges from Statute/AdminRule stubs to matching chunks (restricted to the loaded docs under `--source-filter`) |
| 8 | Vector Upserts | `neptune.algo.vectors.upsert`, one per query, 8 threads |
| 9 | Orphan Cleanup | GC orphan Statute stubs and stale CaseLaw nodes |
| 10 | Integrity Checks | Assert the load is sane (0 doubled case-law titles, 0 orphan chunks, …); exits 1 on failure |

> **Phase 4 no longer writes `HAS_SUBSECTION`** — the edge came from a `_parent_id` key
> nothing in the pipeline ever set, so the edge list was always empty (removed
> 2026-09-19). **Phase 9 no longer GCs orphan `Topic` nodes** — no phase creates them
> (removed the same day). An earlier semantic-edge / topic-clustering phase (LLM-classified
> `RELATED_TO`/`SUPPLEMENTS`/`SUPERSEDES`/`CONFLICTS_WITH`) was removed long before that;
> this is why very old notes mention "phases 1–11". One internal batch-writer helper is
> still named `_flush_phase_8_batch` but is used by Phase 5 — a naming vestige, not an
> offset.

### Document expansion (aliases)

On by default via `ingest_config.yaml` `alias_enrichment` (`enabled: true`,
`embed_input: enriched`):

- **extract** generates 2–3 plain-language questions plus everyday synonyms per chunk
  (Nova 2 Lite), cached under `aliases/{doc_id}.json` keyed by chunk-content hash, so a
  refresh only pays for changed chunks.
- Both extract and embed share one `EnrichPolicy` (`tools/ingestion/lib/aliases.py`) built
  from `alias_enrichment`, so **generation is gated to the same doc types the embed
  consumes** — `include_doc_types: [statute, admin_rule, advisory]`,
  `exclude_doc_types: [case_law, iaao_standard, uspap_standard]`, newest WPAM edition
  only, index/TOC headings excluded. Excluded chunks are never sent to Bedrock and are
  counted as `skipped_policy`. A full-corpus pass is ≈ $2.70 instead of ≈ $10.30.
- **embed** prepends title/heading + aliases (+ any attached gold tester queries) to the
  text sent to Titan for the included doc types only. Stored chunk text never changes —
  only the vector.
- Statute / admin-rule sections with ≥2 numbered subsections chunk one subsection per
  chunk (`STATUTE_SUBSECTION_TARGET_CHARS` in `pdfChunker.py`).

### Cache-aware resume

`extract.py`/`embed.py` skip already-processed docs unless `--force`; `--smart` only
re-processes docs whose upstream artifact is newer than the cache. All three phases
accept `--source-filter <prefix>` and `--cache-prefix <prefix>/` (staging runs that leave
production caches untouched). `load.py` adds `--start-phase`/`--stop-after-phase` and
defaults `--graph-id` to `$NEPTUNE_GRAPH_ID`, which the Fargate task definition sets from
the pinned graph — **a routine load needs no `--graph-id`**.

### When to re-ingest

- **Edge logic changes (phases 3/4/6/7):** full re-ingest required. MERGE doesn't
  retroactively remove stale edges.
- **Chunking or embedding changes:** full re-ingest. Run `ops/purge_orphan_chunks.py`
  after (MERGE never deletes high-index orphans; Phase 5 also purges stale chunks per-doc
  on each load).
- **Property-only mutation (e.g. `edition_year`):** a scoped `--source-filter` run is
  sufficient.

### Running on Fargate

```bash
./tools/ingestion/scripts/run_full_ingest.sh          # full pipeline (3 sequential tasks)
./tools/ingestion/scripts/run_fargate.sh extract      # single phase
./tools/ingestion/scripts/run_fargate.sh load --start-phase 5 --stop-after-phase 8
```

The Docker image must be built `--platform linux/amd64` and **re-pushed after any change
under `tools/ingestion/`** — the task runs the ECR image, never your filesystem. Full
detail: [fargate-ingestion](fargate-ingestion.md). Step-by-step annual procedure:
[annual-refresh-runbook](annual-refresh-runbook.md).

### Ops scripts (`tools/ingestion/ops/`)

Everything currently in the directory:

| Script | Purpose |
| --- | --- |
| `run_graph_regression.py` | The 63-case end-to-end accuracy harness (see [testing](testing.md)) |
| `run_recall_probe.py` | Raw vector-recall probe (recall@10/30, MRR) against any graph id, baseline vs after |
| `build_recall_eval.py` | Mine rated chat-history rows into `tests/recall_eval_queries.yaml` |
| `attach_gold_queries.py` | Attach up-rated tester queries to the chunk they cited, so those phrasings retrieve well |
| `run_classifier_regression.py` | Legacy — prod-parity harness for the pre-loop classifier, which is off |
| `purge_orphan_chunks.py` | Delete Chunk nodes whose index ≥ the current chunk count |
| `purge_dedup_losers.py` | Purge doc_ids recorded in `dedup/losers.json` from S3 and the graph |
| `purge_corrupt_case_law.py` | One-time purge of citation→text mis-assigned case-law nodes |
| `dedup_case_law.py` | One-time dedup keyed on shared CourtListener `source_url` |
| `dedup_case_law_docket.py` | One-time dedup keyed on docket number (catches the Google Scholar cohort) |
| `clean_stale_extracts.py` | Delete `extracted/`+`embedded/` artifacts for missing/drifted raw docs |
| `seed_faq_url_table.py` | Seed `FaqUrlTable` from `documents/faqs.json` |
| `extract_faq_qa_pairs.py` | Scrape FAQ pages into single Q&A files for the Bedrock FAQ KB |
| `test_diversity.py` | Live-AWS diversity probe; defines no `test_*` functions, so pytest collects nothing |

The destructive ones are dry-run by default. Authority resolution is **not** an ops
script — it lives inline in `extract.py` / `load.py::resolve_authority_level`.

---

## 18. Sessions, Auth, and Chat History

Cognito-authenticated users get a sidebar of past chats. Two DynamoDB tables:

- **SessionTable** — PK `sessionId`; GSIs `userIdIndex` (PK `userId`, SK `lastMessageAt`)
  and `connectionId` for WebSocket.
- **ChatHistoryTable** — PK `queryId`; GSIs `sessionIdKey` (PK `sessionId`, SK
  `timestamp`), `timestampIndex`, and **`activityIndexV3`** (the lean projection backing
  the admin activity list). The table's env var is `MESSAGES_TABLE_NAME` in chat_api,
  `CHAT_HISTORY_TABLE_NAME` in agentic_retrieval.

All routes live in one Powertools Lambda (`backend/lambdas/chat_api/main.py`).

**Session routes:** `POST /session`, `GET /sessions`, `PATCH /session/{id}`,
`DELETE /session/{id}`, `POST /session/{id}/message`, `GET /session/{id}/history`,
`POST /session/{id}/feedback`.

**Admin routes**, all gated server-side by `require_admin()` (Cognito `Admins` group):

| Route | Purpose |
| --- | --- |
| `GET /admin/activity` | Activity list (GSI-projected) |
| `GET /admin/activity/{queryId}` | One row incl. `richFeedback` |
| `GET /admin/chunks/documents` | Documents that have an `extracted/` artifact |
| `GET /admin/chunks/{docId}/index?offset&limit` | Chunk metadata only (paginated) |
| `GET /admin/chunks/{docId}/text?offset&limit` | Chunk text on demand |
| `GET /admin/chunks/{docId}` | Legacy whole-document read |
| `POST /admin/ingest` | Ingest a document by URL (downloads to raw, launches a Fargate task) |

The frontend `/admin/*` layout also requires the `Admins` group from the ID token
(`hasAdminGroup` in `auth-context`, `<ProtectedRoute requireAdmin>`), showing an "Admin
access required" panel otherwise. **The group is console-managed, not in CDK** — adopt it
via `cdk import` in the security pass. The chunks pages read `extracted/` from S3, which
is a pre-embedding artifact and never Neptune.

### Key invariants

- Chat history is written **after** streaming completes. DynamoDB is the source of truth
  for resumed sessions.
- `save_chat_history` rebuilds fields explicitly — adding a `RAGDocument` field requires
  adding it here or resumed cards lose it.
- JWT accessor: `request_context.authorizer.jwt_claim` (not `.jwt.claims`).
- `$connect` uses `update_item` with `ConditionExpression attribute_exists(sessionId)` —
  `put_item` would clobber `userId`/`title`.
- DynamoDB numbers deserialize as `Decimal`; all responses run through `_json_default`
  (whole→int, fractional→float).
- **Open security item:** the WebSocket API has **no `$connect` authorizer**, and several
  session routes do not check ownership. See [handoff-plan](handoff-plan.md) §3.

---

## 19. Deployment and Operations

### Where it runs

Everything is in **us-east-1**, one root stack `WisconsinBotGraphRAG` built from nested
stacks (see [`infra/README.md`](../infra/README.md) for the per-stack table). There is no
second production region; earlier notes describing a us-west-2 legacy OpenSearch
production stack are obsolete — the only thing still in us-west-2 is the master FAQ
source bucket (§9).

Commands below use `<your-profile>` as a placeholder — substitute your own AWS CLI/SSO
profile, or export `AWS_PROFILE`. Always run `cdk diff` before deploying and read the
resource list: the Neptune graph is outside CloudFormation precisely so an unrelated
change can never take the corpus with it.

### Deploy checklist

```bash
bun install
bun run bundle                          # copy Python lambdas to infra/bundle/
cd infra
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 cdk diff   -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 cdk deploy -c stackName=WisconsinBotGraphRAG --require-approval never
```

Context flags: `stackName`, plus `domainName` / `hostedZoneName` / `hostedZoneId` for a
custom domain. `neptuneGraphId` is pinned in `infra/cdk.json`; override for a one-off
synth with `-c neptuneGraphId=g-xxxxxxxx`.

### First-time setup

1. `cdk bootstrap` us-east-1.
2. `bun install` → `bun run bundle` → `cdk deploy`.
3. Seed `ModelConfigTable`: `uv run python tools/upload_model_configs.py`.
4. Sync FAQs: `sync_faq_bucket.sh` → `ops/seed_faq_url_table.py`.
5. Build + push the ingestion image, then run the ingestion pipeline.

### Environment gotchas

- **SSL on macOS:** `export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")`
  then set `AWS_CA_BUNDLE=$CERT`.
- **Bedrock model IDs:** require the full inference-profile format
  (`us.anthropic.claude-sonnet-4-6`).
- **Neptune scaling:** 32 m-NCU at rest (the floor — 16 is rejected), 128 during a full
  load. Scale back after; 128 is 4× the cost.
- **Full loads run outside business hours.** Phase 5 deletes and recreates every chunk
  and Phase 8 rewrites vectors — roughly 10 minutes during which live queries can get
  thin or empty answers with no error. Single-doc `--source-filter` loads are safe
  anytime.
- **`AWS_REGION` in shell:** scripts default to us-east-1 but `AWS_REGION` overrides.
  Set it explicitly.

### Observability

A single `query_id` threads through handler → tools → WebSocket → DynamoDB. To debug a
failed query:

1. DynamoDB `get-item` by `queryId` for the stored question/answer/feedback.
2. CloudWatch: fetch the recent log stream and grep the `query_id` (the filter tokenizer
   splits UUIDs poorly — see CLAUDE.md "Investigating a Query").
3. Look for structured log events keyed by `query_id`:
   `agentic_retrieval_request_received`, `clarification_reply_resolved`,
   `agent_tool_call`, `agent_tool_result`, `wpam_dedup`, `adequacy_judged`,
   `agent_loop_complete`, `answer_stream_complete`.

---

_When you change a subsystem, update its section here. Companion docs:
[auto-backfill](auto-backfill.md) (the `vector_search` stages and backfill arms),
[chunk-quality-controls](chunk-quality-controls.md) (ingestion chunking),
[fargate-ingestion](fargate-ingestion.md) (running the pipeline),
[annual-refresh-runbook](annual-refresh-runbook.md) (the yearly data refresh),
[testing](testing.md) (what to run and update),
[handoff-plan](handoff-plan.md) (moving to DOR's account),
[`infra/README.md`](../infra/README.md) (stacks and the graph pin)._
