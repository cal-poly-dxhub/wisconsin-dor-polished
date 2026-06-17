# GraphRAG Engineering Guide

This document covers the work done on the `feat/graphrag-migration` branch since the project was inherited from the original Cal Poly DxHub team. The branch diverged from the inherited codebase at commit `dc34139` (2026-02-13); the first new commit is `5d40004` (2026-04-14). What follows is **209 commits** of work that replaced a legacy OpenSearch RAG pipeline with a Neptune-Analytics GraphRAG system, an agentic retrieval Lambda, a live agent-trace UI, multi-session chat, and a click-time citation resolver.

---

## How to read this guide

- **"Current state"** describes what the code does now. Trust this over commit messages, which were sometimes WIP or later modified.
- **⚠️ Reversed / Do NOT revert** marks a design that was deliberately changed. The commit history still contains the old approach; do not resurrect it.
- **Gotcha** marks an invariant or footgun that will silently break things if violated.
- File references are `path:line` and clickable. Line numbers drift; treat them as "look near here."
- Commit hashes (`a1b2c3d`) point at the rationale. `git show <hash>` — the commit bodies in this repo are unusually detailed and are the primary source for "why."

### Table of contents

1. [Orientation](#1-orientation)
2. [How this team works](#2-how-this-team-works)
3. [The request lifecycle (happy path)](#3-the-request-lifecycle-happy-path)
4. [The Neptune graph data model](#4-the-neptune-graph-data-model)
5. [Cross-cutting Neptune rules](#5-cross-cutting-neptune-rules)
6. [The offline ingestion pipeline](#6-the-offline-ingestion-pipeline)
7. [PDF extraction and chunking](#7-pdf-extraction-and-chunking)
8. [The agentic retrieval Lambda](#8-the-agentic-retrieval-lambda)
9. [The FAQ layer](#9-the-faq-layer)
10. [Case law — the deliberately constrained secondary source](#10-case-law--the-deliberately-constrained-secondary-source)
11. [WPAM edition recency](#11-wpam-edition-recency)
12. [Citations and source cards](#12-citations-and-source-cards)
13. [The shared-contract discipline](#13-the-shared-contract-discipline)
14. [Live agent-trace streaming](#14-live-agent-trace-streaming)
15. [Sessions, auth, and chat history](#15-sessions-auth-and-chat-history)
16. [Frontend presentation layer](#16-frontend-presentation-layer)
17. [Deployment and operations](#17-deployment-and-operations)
18. [Appendix: glossary, do-not-revert catalog, and known-stale docs](#18-appendix)

---

## 1. Orientation

The chatbot answers property-tax questions for the Wisconsin Department of Revenue. It has **two mutually exclusive retrieval paths**, selected at deploy time by the CDK context flag `useGraphRAG`:

- **Legacy path** (`useGraphRAG=false`): EventBridge → `MessagesStack` Step Function → Classifier Lambda → OpenSearch RAG or FAQ branch → streaming. This is the original team's design.
- **GraphRAG path** (`useGraphRAG=true`): EventBridge → `GraphRAGMessagesStack` Step Function → a single **agentic retrieval Lambda** that runs Claude in a tool loop against a Neptune Analytics knowledge graph → streaming.

**Only the GraphRAG path is live and under active development.** The legacy path still ships and is not deleted, but no new features go into `packages/messages/lambdas/` (classifier/retrieval). The crucial exception: **both paths share the same two streaming Lambdas** — `ResponseStreaming` (synthesizes and streams the answer) and `ResourceStreaming` (streams citation cards) — which live in `MessagesStack` and are passed into the GraphRAG stack as props. If you touch those, you touch both paths. (Jonah's note: this means that the two versions of the backend use the same avenues to communicate with the frontend; currently, we have the GraphRAG backend hooked up to talk to the frontend)

Selection is enforced structurally, not with an `if` inside one rule. Each stack creates its **own** EventBridge rule with an identical event pattern (`source: wisconsin-dor.chat-api`, `detailType: ChatMessageReceived`) but `enabled: props.enabled`. Exactly one rule is enabled per deploy, so the two Step Functions can never both fire on one event (`cf8ca85`). This is the project's core invariant in physical form: **GraphRAG was added without modifying the legacy path.**

```
                        ┌─────────────────────────────────────┐
   Chat API ──emit──▶   │ EventBridge: ChatMessageReceived     │
   (POST /message)      └───────────────┬──────────────────────┘
                                        │  (exactly one rule enabled)
                 ┌──────────────────────┴───────────────────────┐
                 ▼                                               ▼
   ┌──────────────────────────┐                  ┌──────────────────────────────┐
   │ Legacy Step Function     │                  │ GraphRAG Step Function        │
   │  Classifier → RAG/FAQ    │                  │  AgenticRetrieval (Claude loop │
   │  (OpenSearch)            │                  │   over Neptune) → Choice       │
   └───────────┬──────────────┘                  └──────────────┬───────────────┘
               │                                                 │  flat payload
               └──────────────┬──────────────────────────────────┘
                              ▼
              ┌──────────────────────────────────────┐
              │ Parallel: ResourceStreaming +         │  ← SHARED by both paths
              │           ResponseStreaming           │
              └──────────────────┬────────────────────┘
                                 ▼  WebSocket (API Gateway)
                            Frontend (Next.js)
```

Deployment regions matter and are easy to get wrong:

- **us-west-2** = production (the legacy OpenSearch stack). **Do not deploy from feature branches.**
- **us-east-1** = the GraphRAG test stack `WisconsinBotGraphRAG`. All GraphRAG development deploys here. The live graph is `g-ndvl4j73v4`.

---

## 2. How this team works, stuff to keep in mind moving forward with dev

Basically, your LLM should be aware of these things that came up during development.

Before the subsystems, internalize the engineering patterns. They recur in nearly every commit and explain _why_ the code looks the way it does. A change that violates one of these is almost always wrong.

**Additive, mutually-exclusive feature gating.** GraphRAG was introduced as separate stacks and EventBridge rules gated by one flag, never by editing the legacy path. New schema fields follow the same rule: they are added as `Optional` with null-omitting serialization, never as required fields that would break existing callers.

**MERGE-idempotent graph loads, plus explicit GC for residue.** Every ingestion write uses Cypher `MERGE` so re-runs are safe. But idempotency does **not** retroactively fix or remove stale edges/chunks from a prior buggy run — hence the hard rule that edge/chunk/embedding changes require a _full re-ingest_, and the existence of dry-run-by-default cleanup scripts (`purge_orphan_chunks.py`, `clean_stale_extracts.py`, and `load.py`'s own phase-12 GC) for what MERGE can't clean.

**Non-destructive defaults; mutation is opt-in.** `extract.py`/`embed.py` skip already-processed docs unless `--force`. The patch and purge scripts default to dry-run and require `--apply`. Assume any script you run will _not_ mutate unless you ask it to.

**Best-effort side effects never abort the correctness path.** Trace emission no-ops on a dead WebSocket and swallows errors. `save_chat_history` swallows exceptions. FAQ-URL lookup returns `None` on any miss. `vector_search` auto-enrichment swallows neighbor errors. A single tool exception becomes an error tool-result fed back to the model rather than crashing the Lambda. The pattern: isolate observability and UX-sugar failures from the answer path.

**Backend is the source of truth; the frontend stays dumb.** The Lambda pre-formats every human-readable trace string and emits camelCase metadata. The UI only picks a verb and renders. Citation cards are restricted to exactly the agent's `cited_doc_ids` on the backend.

**Bound everything that talks to a metered service.** These limits are first-class design inputs, not afterthoughts: Step Functions 256 KB I/O (flat Lambda payload + `Pass`-state projection), API Gateway 128 KB per WebSocket frame (devPayload truncation), Titan v2's 8000-char silent truncation (`CHUNK_MAX_CHARS=7500`), Neptune per-query memory (byte-capped UNWIND), and the Bedrock turn budget (`MAX_TURNS=10` + degraded fallback).

**Root-cause before fixing; prove it end-to-end.** The history repeatedly shows a first plausible fix being insufficient and a second independent cause found (phase 4 produced 0 edges for _two_ unrelated reasons; the discovery-tag no-op hid a second crash once fixed). Fixes ship with regression tests and verified counts ("1,742 PART_OF edges, was 0").

**Spec → plan → implement, in writing.** Major features have a dated design spec in `docs/superpowers/specs/` and a task-by-task plan in `docs/superpowers/plans/`. **Read the relevant spec and plan before changing a subsystem — but verify against the code, because specs go stale** (the appendix lists which ones).

**Comment the non-obvious workaround, at the call site and in the commit.** Much of this codebase documents _why a tempting simplification is wrong_, specifically to stop a future contributor from "fixing" it. When you see an odd-looking workaround with a comment, the comment is load-bearing.

---

## 3. The request lifecycle (happy path)

One worked trace, GraphRAG path, before the subsystem deep-dives.

1. **Frontend → API.** The user sends a message. The webapp `POST`s `/session/{id}/message` on the Sessions HTTP API (Cognito-authenticated). The handler emits an EventBridge event `wisconsin-dor.chat-api : ChatMessageReceived` carrying a `UserQuery {query, query_id, session_id}` in `detail`, bumps `lastMessageAt`, and sets the session title from the first message.
2. **EventBridge → Step Function.** The enabled GraphRAG rule forwards `$.detail` (just the `UserQuery`, not the envelope) into `GraphRAGStreamingStateMachine`.
3. **AgenticRetrieval Lambda.** It loads up to 5 prior turns of chat history, looks up the WebSocket connection (best-effort, for trace), and runs `run_agentic_loop`:
   - **Turn 0a** (only if history exists): `refine_query` rewrites a context-dependent follow-up.
   - **Turn 0b**: a deterministic, Claude-bypassing `faq_search` on the verbatim query. Its result is _seeded_ into the message list. If the top score ≥ 0.70, a steering message marks the FAQ as primary truth.
   - **Turns 1–10**: `bedrock.converse` with the tool set; Claude calls `vector_search` (auto-enriched with graph neighbors), `get_neighbors`, `get_authority_chain`, etc., then calls the terminal `answer` tool.
4. **Flat return.** The Lambda returns `{successful, query, query_id, session_id, faqs, documents}` — a **flat** payload (`main.py:~1825`).
5. **Choice + Parallel.** `CheckRetrievalSuccess`: if `successful=false` → `Fail`. Otherwise → `Parallel` with two branches, each fed by a `Pass` state that projects a subset of the flat payload (`SelectResourceStreamingJob`, `SelectGenerateResponseJob`).
6. **Streaming.** `ResourceStreaming` emits citation cards over the WebSocket; `ResponseStreaming` synthesizes and streams the answer text. Both are the shared legacy Lambdas.
7. **Frontend.** Each WebSocket frame is Zod-validated; fragments drive the streaming markdown, `documents`/`faq` frames drive inline source cards, `agent-event` frames drive the live trace.

> **Gotcha — the flat payload contract.** The agentic Lambda returns flat keys _specifically_ to avoid duplicating the (potentially large) `documents` array into two nested job objects and blowing the 256 KB Step Functions limit (`4f0e95a`). If you change the keys it returns, you must update **both** `Pass` states in `graphrag-messages-stack.ts` or the streaming branches break with a JSONPath error.

---

## 4. The Neptune graph data model

**Engine.** Neptune Analytics (`neptune-graph`), graph `g-ndvl4j73v4`, us-east-1, 1024-dim vector index (matches Titan Embed Text V2), 32 m-NCU, IAM auth, **public connectivity, no VPC**. Public connectivity is deliberate (`6bf3ec1`): the project has no VPC, so the graph is publicly reachable but IAM-gated, scoped to the agentic Lambda's `neptune-graph:ExecuteQuery / ReadDataViaQuery / GetQueryStatus`. The trade-off is documented — no network boundary, IAM is the only protection.

**Node spine.** `Framework → Document → Chunk`, plus `Topic` nodes and on-demand `stub` nodes.

- **Document** carries `title, source_key, summary, source_url, doc_type, authority_level, citation, effective_date, edition_year`.
- **Chunk** carries `text, doc_id, source_url, chunk_index, s3_key, start_page, end_page, heading, subheading, edition_year`, plus a 1024-dim `embedding`.
- **Stub** (`stub: true`) `Statute`/`AdminRule` nodes are `MERGE`d on demand when a citation regex matches a section that was never indexed as full text. They carry only an id/title — see the stub-promotion footgun in §10.

Node labels come from `ingest_config.yaml` `doc_types` (e.g. `statute → Statute`, `case_law → CaseLaw`, `assessment_manual → AssessmentManual`).

### The 9-level authority hierarchy

Defined once in `scripts/graphrag/ingest_config.yaml` under `frameworks`, ordered by legal precedence, with a single-parent chain wired as `DERIVED_FROM` edges (child → parent). **It is a tree, not a strict line.**

| Level | Framework                        | Parent       |
| ----: | -------------------------------- | ------------ |
|     1 | Constitution (`FW-CONSTITUTION`) | —            |
|     2 | Statutes (`FW-STATUTES`)         | Constitution |
|     3 | Case Law (`FW-CASE-LAW`)         | Statutes     |
|     4 | Admin Rules (`FW-ADMIN-RULES`)   | Statutes     |
|     5 | WPAM (`FW-WPAM`)                 | Admin Rules  |
|     6 | FAQ (`FW-FAQ`)                   | WPAM         |
|     7 | Gov Pubs (`FW-GOV-PUBS`)         | WPAM         |
|     8 | IAAO (`FW-IAAO`)                 | Gov Pubs     |
|     9 | USPAP (`FW-USPAP`)               | Gov Pubs     |

`authority_level` (the integer) is the **single source of truth** for the UI's AuthorityBadge. It is resolved per-doc by `resolve_authority_level()` with strict precedence: explicit value → framework-canonical level → **`None`**.

> **⚠️ Do NOT default a missing authority level to a number.** An earlier default of `6` (FAQ) mislabeled 607 gov-pub/advisory nodes as FAQs in the UI — a Supreme Court news page showed the FAQ badge (`f1fc513`). The design now returns `None` (render no badge) rather than a misleading one. This invariant is duplicated in both `extract.py` and `load.py`; keep both.

### Edge types — the _real_ ones

`load.py` actually writes these eight edge labels (verified by grep, not by docs):

| Edge             | Direction                                                     | Meaning                    |
| ---------------- | ------------------------------------------------------------- | -------------------------- |
| `CITES`          | Doc/Chunk → Statute/AdminRule; **Statute → CaseLaw (mirror)** | citation reference         |
| `IMPLEMENTS`     | Doc → Statute                                                 | rule implements statute    |
| `PART_OF`        | Section → Chapter                                             | statute hierarchy          |
| `BELONGS_TO`     | Doc → Framework                                               | framework membership       |
| `HAS_SUBSECTION` | Doc → Doc                                                     | multi-part documents       |
| `EXTRACTED_FROM` | Chunk → Doc                                                   | chunk provenance           |
| `DERIVED_FROM`   | Framework → Framework                                         | authority precedence chain |
| `COVERS_TOPIC`   | Doc → Topic                                                   | semantic grouping          |

Phase 11 additionally creates four **semantic** edge types from `PHASE_11_ALLOWED_TYPES` (`load.py:883`): `RELATED_TO`, `SUPPLEMENTS`, `SUPERSEDES`, `CONFLICTS_WITH`.

---

## 5. Cross-cutting Neptune rules

Neptune Analytics has sharp edges that surfaced _independently_ in the offline loader, the runtime agent, and the ingestion scripts. Learn them once here; they explain a cluster of otherwise-baffling code.

### 5.1 The StreamingBody footgun (highest-impact bug in the codebase)

`neptune-graph`'s boto3 `execute_query` returns results inside a **streaming body** under `response["payload"]`, _not_ a pre-parsed `response["results"]` dict. The original code read `response.get("results", [])`, so **every query silently returned `[]`** — no error, just empty.

The blast radius was enormous and silent:

- **Offline loader**: phase 4 (`PART_OF`), phase 9 (stub resolution), phase 12 (GC) were all no-ops. After the fix, a full re-load produced **1,742 `PART_OF` edges (was 0)** and **3,041 Statute→CaseLaw mirror edges (was 0)**; `WIS-STAT-70.32` then resolved 127 cited cases including _Markarian_.
- **Runtime agent**: every `vector_search` / `get_neighbors` / `get_document` returned nothing, so the agent answered with no citations and the frontend hung.

The same bug had to be fixed in **two** places: `neptune_client.py` (runtime, `4f0e95a`) and `load.py::execute_query` (offline, `610972f`). Both now `json.loads(payload.read())` once, centrally.

> **⚠️ Any new Neptune codepath must decode `payload.read()`.** If anyone "simplifies" `execute_query` back to `response["results"]`, the whole system silently returns empty. This parse is load-bearing.

### 5.2 No parameterized `CALL` args or path bounds

Neptune Analytics rejects `$parameters` inside `CALL` procedure arguments and in variable-length path bounds. So:

- `vector_search` inlines the 1024-float embedding and `topK` as **string literals** into the Cypher.
- `get_authority_chain` inlines `max_depth` into the variable-length path.
- Phase 10 (`vector upserts`) inlines each embedding literal and upserts **one vector per query** (the `neptune.algo.vectors.upsert` CALL can't UNWIND a batch), parallelized over 8 threads.

`_compact_cypher` masks the inlined embedding before logging so logs stay small. **Do not "fix" these to use `$params`** — it silently fails (`de53482`).

### 5.3 Throttling has two faces

Neptune signals overload via **both** `ThrottlingException` _and_ `UnprocessableException` with message _"Retry for SDK query requests is suppressed, please resubmit the query."_ `execute_query`'s retry loop treats both as throttling (8 attempts, backoff capped at 60s). Catching only `ThrottlingException` crashes the loader mid-run (`c198bc0`). New query helpers must catch both.

### 5.4 UNWIND must be capped by bytes, not just count

Ingestion write phases batch with `UNWIND $rows`. Capping by **document/row count alone is insufficient**: a single outlier doc (a case-law opinion or WPAM chunk with ~2000+ chars) blows Neptune's per-query memory budget and deterministically OOMs. Phase 8 therefore caps by cumulative chunk-text **bytes** (`PHASE_8_MAX_BYTES_PER_FLUSH = 50_000`) in addition to count (50) and ref-pairs (400) (`26a81e3`). The byte cap is the one that actually prevents the OOM — do not remove it in favor of count-only caps.

---

## 6. The offline ingestion pipeline

This is the most operationally dangerous subsystem. It is four standalone Python scripts run in sequence against two S3 buckets (raw + work) and the Neptune graph. There is **no orchestrator** — phase ordering, `--force` discipline, and post-re-ingest cleanup are operator responsibility (`run_ingestion.sh` in the repo root is the closest thing). Jonah's note: if Wisconsin likes the product, a probable future step is figuring out a way to automate this on a fixed interval to make sure that the chatbot is up to date. This is kinda like an "eventually consistent" rather than a "strongly consistent" type of system, unless we want to set up a trigger or some automation that does this every time they upload a new version of any document onto their website. Additionally, we will need to re-run the entire ingestion pipeline because the edges that are generated depend on the new chunks that are scraped and parsed.

```
scrape_documents.py ──▶ raw/{doc_id}/{doc_id}.{pdf|txt} + .metadata.json
        │
        ▼
extract.py ──▶ work: extracted/{doc_id}.json   (PDF→pdfChunker; classify; statute_refs)
        │
        ▼
embed.py ──▶ work: embedded/{doc_id}.json      (Titan v2, 1024-dim; case_law SKIPPED)
        │
        ▼
load.py ──▶ Neptune graph                      (11 CLI phases of UNWIND-batched Cypher)
```

### 6.1 The four stages

- **`scrape_documents.py`** pulls from a hardcoded `DOCUMENT_SOURCES` dict plus a sitemap-driven `news_pages` category, writing raw PDFs/text + a `.metadata.json` sidecar (`doc_id, source_url, doc_type, framework_id, authority_level` as a _string_, `category`, optional `effective_date`).
- **`extract.py`** routes PDFs through `pdf_chunking/` (see §7), regex-extracts per-chunk `statute_refs`/`admin_rule_refs`, LLM-classifies the doc (first 4000 chars), and writes `extracted/{doc_id}.json`.
- **`embed.py`** embeds every chunk + a synthetic doc-level vector with Titan v2 (truncating at `text[:8000]`). **`case_law` is skipped entirely** (no embeddings — see §10).
- **`load.py`** reads all of `embedded/` and runs the graph build.

### 6.2 `load.py` phase numbering (a real trap)

`load.py`'s `main()` registers **11 CLI-numbered steps**, but the internal **function names diverge by one from the chunk phase onward** because the old phases 6 and 7 were merged into `phase_6_7_hierarchy`:

| CLI step | Name              | Function                    |
| -------: | ----------------- | --------------------------- |
|        1 | Scaffold          | `phase_1_scaffold`          |
|        2 | Document Nodes    | `phase_2_document_nodes`    |
|        3 | Cross-References  | `phase_3_cross_references`  |
|        4 | Statute Hierarchy | `phase_4_statute_hierarchy` |
|        5 | Topic Merging     | `phase_5_topic_merging`     |
|        6 | Hierarchy Links   | `phase_6_7_hierarchy`       |
|    **7** | **Chunk Nodes**   | **`phase_8_chunks`**        |
|        8 | Stub Resolution   | `phase_9_stub_resolution`   |
|        9 | Vector Upserts    | `phase_10_vectors`          |
|       10 | Semantic Edges    | `phase_11_semantic_edges`   |
|       11 | Orphan Cleanup    | `phase_12_cleanup`          |

> **Gotcha.** `--start-phase 7` runs `phase_8_chunks`; CLI step 11 = `phase_12_cleanup`. When a commit message says "phase 8 OOM," it means the function `phase_8_chunks`, which shows in logs as **CLI step 7**. Do not assume `--start-phase N` maps to `phase_N_*`.

### 6.3 Cache-aware resume and scoped re-ingestion

`extract.py`/`embed.py` skip doc_ids already present in their output prefix **unless `--force`**. All three of extract/embed/load accept `--source-filter <prefix>` (e.g. `wpam-`) to scope to a doc_id prefix; in `load.py`, graph-wide phases still run but are MERGE-idempotent. `load.py` also has `--start-phase`/`--stop-after-phase`.

> **⚠️ Edge-logic changes require a FULL re-ingest.** Any change to phase 3/4/11 edge logic, or to chunking/embedding, invalidates stale edges/chunks. MERGE-idempotent re-runs do **not** retroactively fix or remove them. Redeploying code alone does nothing because the work-bucket caches are content-keyed. The one exception that proves the rule: a property-only mutation on existing nodes (like WPAM `edition_year`) can land via a scoped `--source-filter` re-run, because no edges change (see §11).

### 6.4 Cleanup and patch scripts (all dry-run by default)

| Script                        | Purpose                                                                                                                                                                                                                                                                                                                                           |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `purge_orphan_chunks.py`      | Deletes `Chunk` nodes whose deterministic id `{doc_id}_chunk_{i:04d}` has no slot in the current embedded JSON. **Required after any re-ingest that shrinks a doc's chunk count** — MERGE never deletes the now-orphaned high-index chunks, and they pollute `vector_search` until purged. First run removed 71 orphans from the pre-cap chunker. |
| `clean_stale_extracts.py`     | Deletes `extracted/`+`embedded/` artifacts on missing-raw-doc OR source-key drift (e.g. a stub replaced by full text under a new key).                                                                                                                                                                                                            |
| `patch_metadata_authority.py` | Rewrites `authority_level` in raw `.metadata.json` to the framework-canonical level (string-preserving).                                                                                                                                                                                                                                          |
| `patch_work_authority.py`     | Companion: rewrites the cached `authority_level` in `extracted/`+`embedded/` JSONs (int) so a plain `load.py` re-run corrects the graph **without** re-classify/re-embed.                                                                                                                                                                         |

> **Gotcha.** The authority fix needed _both_ patch scripts plus an `extract.py`/`load.py` change, because the wrong value was baked into raw metadata _and_ the work cache _and_ defaulted in code. Forgetting either patch leaves the stale explicit value winning precedence over the framework default (`331d60e`, `7f776b3`, `2b3ef89`).

### 6.5 Known sharp edges in ingestion

- **Phase 4 parent IDs are `WIS-STAT-{N}`, not `CH-{N}`.** The original `CH-{N}` form matched nothing (CITES/PART*OF live in the `WIS-STAT-*` namespace) \_and* only 7 hardcoded chapters existed — a second independent reason phase 4 produced 0 edges (`8ea71a9`). The `statute*families` `CH-*`nodes from phase 1 are now effectively a dead namespace; don't assume they link to`WIS-STAT-\_`.
- **`make_doc_id` prepends a path discriminator** for generic stems and news sections — `/Manufacturing/home.aspx` and `/RETr/Home.aspx` both stemmed to `home` and silently overwrote each other; COTVC-News and Assessor-News post on the same dates (~62 collisions) (`5688d7a`).
- **Phase 11's prompt** carries per-doc summaries + positive/negative examples + a decision order (`SUPERSEDES > CONFLICTS_WITH > SUPPLEMENTS > RELATED_TO`). Before this, the LLM defaulted everything to `RELATED_TO` and emitted `CONFLICTS_WITH` once in 11k+ edges (`8ea71a9`). Per-type counts are logged so you can verify.

---

## 7. PDF extraction and chunking

`pdf_chunking/` turns a PDF into page-tracked, sub-8000-char chunks. It is invoked once, from `extract.py`.

**PyMuPDF-first, Textract-fallback.** `process_pdf_from_s3` downloads the PDF, tries `extract_with_pymupdf` (font-metric title/header tagging, statute noise stripping), and gates the result with `extraction_looks_good()` (≥5 lines, ≥1 non-empty, avg stripped line length ≥3). On failure or a failed gate, it falls through to async Textract (LAYOUT+TABLES), whose output is always cleaned up in a `finally`.

> **⚠️ Do NOT switch to Textract-only as a "fix" for chunking problems.** PyMuPDF-first is the recorded correct design: the corpus is digital-native (OCR adds nothing), Textract is ~$1.50/1k pages and slow, and — critically — Textract text has the _same_ repeating per-page footers, so it would _not_ have fixed the page-range bug below. The chunker was the problem, not the extractor.

Both extractors emit the same contract the chunkers consume: `header_split` (text blobs) and `line_page_mapping` (a flat `list[tuple[str, int]]` of every line paired with its 1-based page).

**Three chunkers, routed by `CHUNKER_BY_SOURCE`:** `statute`, `wpam`, `general`.

### The bugs that were fixed (and must not return)

- **Per-line page tracking** (`9bc8346`). The old `get_pages_for_chunk()` reconstructed each chunk's page range by substring-matching its lines against the _whole_ document. Repeating boilerplate (footers, headers) appears on every page, so the match set exploded — corrupting **55% of the deployed corpus** (one chunk spanned pages 1–1349). Now each buffered line is a `(text, page)` tuple and a chunk's `start_page`/`end_page` is the min/max of its buffer. **Do not reintroduce any "reconstruct pages by matching text" helper.**
- **TOC suppression** (`9bc8346`). A dot-leader line (`"XV. Contact Info . . . 57"`) matches the roman-numeral heading regex and would become the heading for every subsequent chunk. The `_LEADER_IN_LINE` pattern forces such lines to body text, and `is_toc_chunk()` drops whole TOC chunks post-chunking. These are two separate defenses; both exist for a reason.
- **Table false-positive rejection** (`9bc8346`, `610972f`). PyMuPDF's `find_tables()` flags multi-column prose as tables; row-joining with `" | "` scrambles reading order. `looks_like_real_table()` rejects candidates by row-count/cell-length signature and a >60%-empty-cell ratio.
- **7500-char cap** (`40f8747`). Titan v2 **silently truncates** input past 8000 chars — an oversized chunk was stored and shown, but its tail never contributed to the embedding (match-invisible). `CHUNK_MAX_CHARS=7500` is enforced at three layers (in-loop, per-chunker `_enforce_chunk_cap`, and a final pass after assembly, because heading prefixes and line-rejoining grow the string after the buffer-time measurement).

> **Gotcha — routing is effectively dead for statute/WPAM in production.** `extract.py` passes `source_id = metadata["doc_id"]` (the per-doc id), but `CHUNKER_BY_SOURCE` keys are categories (`"state-laws"`, `"assessment-manual"`). They almost never match, so nearly everything falls through to the `general` chunker. The specialized `chunk_document_statute`/`chunk_document_wpam` paths are maintained and unit-tested but rarely exercised by the real pipeline. If you intend statutes/WPAM to use their specialized chunkers, fix the `source_id` passed by `extract.py` _or_ the dict keys — re-running ingestion alone won't change which chunker fires.

> **Gotcha — import side effects.** `pdfChunker.py` calls `ensure_bucket_exists(...)` at _import time_ against a hardcoded bucket name. Tests must pre-stub boto3 in `sys.modules` before importing it (see `test_chunk_size_cap.py`).

---

## 8. The agentic retrieval Lambda

`run_agentic_loop` in `packages/graphrag/lambdas/agentic_retrieval/main.py` is the heart of the GraphRAG path. It replaced the legacy classifier + retrieval Lambdas with a single bounded Claude tool loop.

### The loop, step by step

- **Turn 0a — `refine_query`** runs only when `chat_history` exists (fresh sessions skip it to save a Bedrock call). It rewrites context-dependent follow-ups ("what about agriculture") and extracts an optional `target_wpam_year` (see §11).
- **Turn 0b — deterministic `faq_search`.** A hardcoded, Claude-bypassing FAQ search on the verbatim (or history-refined) query. Its result is **seeded** into the message list as a synthetic `toolUse`/`toolResult` pair (id `faq_search_turn0`) so Claude enters the loop already seeing FAQ context without re-invoking the tool. (Why: Claude paraphrases queries, which measurably hurt KB recall — §9.)
- **Turns 1–10** (`MAX_TURNS=10`): `bedrock.converse(modelId=AGENTIC_MODEL_ID, maxTokens=4096, temperature=0.0)` with `SYSTEM_PROMPT` and the tool set. Each turn appends the assistant message, executes every `toolUse` block, and appends `toolResult`s.
- **Termination** is the `answer` tool. The loop returns `(answer_text, cited_doc_ids, rag_documents, faq_resource)`.

**Turn budget and degradation** (`8d45e04`): at loop index `turn == 7` (the 8th turn — `turn_number = turn + 1`) a "running low on turns" message is injected. If the `for` exhausts without `answer`, a degraded fallback extracts the last assistant text and appends `_(Response incomplete: turn budget reached)_`. On the no-tool path, a `max_tokens` stop reason appends `_(Response may be incomplete)_`. Turn 0 is _outside_ this loop and doesn't count against `MAX_TURNS`.

### The tools (`tools.py::execute_tool`)

| Tool                  | Role                                                                                                                   |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `faq_search`          | Bedrock FAQ KB retrieve (`SEMANTIC`).                                                                                  |
| `refine_query`        | LLM query rewrite + `target_wpam_year` extraction.                                                                     |
| `vector_search`       | Titan-embed the query → Neptune vector index → WPAM dedup → **auto-enrich** top-3 parent doc_ids with `get_neighbors` → direct citation resolution → **neighbor-doc citation discovery**. |
| `get_neighbors`       | Graph traversal; accepts `edge_types` filter.                                                                          |
| `get_document`        | Node lookup; falls back to vector search on miss.                                                                      |
| `get_authority_chain` | Walk `DERIVED_FROM`/`PART_OF` up and down.                                                                             |
| `list_framework_docs` | Enumerate a framework's docs.                                                                                          |
| `fetch_case_opinion`  | Last-resort: read full opinion `.txt` from S3 (§10).                                                                   |
| `answer`              | Terminal; returns its input.                                                                                           |

### Key design decisions and footguns

- **Auto-enrichment** (`1864142`): after `vector_search` returns chunks, the executor fetches `get_neighbors` for the top-3 distinct parent doc_ids and returns `{chunks, graph_context}`. The agent gets graph context for free without an extra turn. Best-effort — neighbor errors are swallowed.
- **Neighbor-doc citation discovery** (`f87e6d7`): after auto-enrichment, `vector_search` runs an additional pass to surface case law mentioned in neighbor doc text but not in the directly-retrieved chunks. It filters neighbors to non-WPAM document nodes, ranks them by shared statute overlap with the query chunks (top 3), fetches their chunk text, regex-extracts case citations, and resolves them to CaseLaw nodes. The chunk text stays in the Lambda (regex only, never enters Claude's context). See `docs/co-cited-case-law-discovery.md` for the full traversal path. This replaced a pure graph-traversal approach that failed because high-degree statute nodes (1500+ CITES edges) made LIMIT-based discovery arbitrary.
- **`cited_doc_ids` is the authoritative sidebar.** On `answer`, the returned cards are restricted to _exactly_ the agent's cited ids, not all discovered docs — auto-enrichment pulls in far more docs (case-law stubs CITES-linked to statutes) than the answer uses. The turn-budget fallback path is the exception (it returns all discovered ids).
- **Tool exceptions never crash the loop** (`686da54`). The model once called `get_document` with `node_id` (the param used by `get_neighbors`) instead of `doc_id`; the handler indexed `tool_input["doc_id"]` directly, raised `KeyError`, crashed the Lambda, and stranded the user on "answering." Now: `get_document` accepts both keys via `.get()`, and any tool exception is converted to an `{"error": ...}` tool-result fed back so the model self-corrects.

> **⚠️ Do NOT revert** the StreamingBody parse (§5.1), the inlined-literal Cypher (§5.2), or the `node_id`/`doc_id` alias. Each is a dead design's replacement.

> **Tech-debt note.** `MAX_TURNS`, `FAQ_SCORE_THRESHOLD`, `MAX_FAQS`, `MAX_HISTORY_TURNS`, the turn-8 index, and `inferenceConfig` are all hardcoded constants in `main.py`. Tuning requires a code change + rebundle. `execute_tool` is a long if/elif with no registry — adding a tool means editing `tools.py` _and_ several summary/discovery sites in `main.py`.

---

## 9. The FAQ layer

A Bedrock FAQ Knowledge Base (Titan v2, `ChunkingStrategy.NONE` because each file is exactly one Q&A pair) answers common questions and anchors the final answer.

**Turn 0 is deterministic** (`13a90c3`). The prompt-level "call faq*search first" rule had collapsed into \_parallel* `faq_search` + `vector_search` in one turn, citing graph docs the user never needed — and Claude paraphrased the query, hurting KB recall. So turn 0 is now hardcoded Python (`_faq_search_direct`) running the verbatim query, bypassing Claude entirely.

**0.70 = primary truth, not a short-circuit.** `FAQ_SCORE_THRESHOLD = 0.70`. When the top score clears it, `_build_faq_resource` builds a `high_confidence_faq` up front and a steering user message tells Claude to treat the FAQ Q&A as the **primary source of truth** that statutes/rules/WPAM supplement and cite but never contradict. The `FAQResource` is returned **unconditionally** even if Claude's `cited_doc_ids` omit the FAQ id, so downstream synthesis always anchors on it.
Jonah's note: I believe Darren had mentioned that, instead of hardcoding a value, we could look into seeing whether or not the cosine similarity was within a certain percentage compared to the results from the semantic search. Future development could look into this, if it seems like we could get better results that way. 0.70 seemed to work pretty well for the provided test questions, though.

> **⚠️ The loop ALWAYS continues into the graph now. There is NO short-circuit.** This is the single most important reversal in the FAQ subsystem (`3ae5894`). Originally a score ≥ 0.70 returned _only_ the FAQ and skipped the agentic loop (16.9s → 473ms). But FAQ-only answers had zero citable graph evidence — no statute/rule cards. The new design keeps the plain-language FAQ as the backbone while forcing the agent to find authoritative supporting documents. **Do not re-add the early return on score ≥ 0.70**; that exact design was deliberately removed.
> Jonah's note: this was a feature that the customer wanted -- even if we get an FAQ, we can still search the graph and provide a richer answer than just spitting out the FAQ answer.

**FAQ → revenue.wi.gov links.** At query time, each FAQ's public source page is resolved via `_lookup_faq_url(question)`, a DynamoDB `get_item` against `FaqUrlTable` keyed on `_normalize_faq_question(question)`. The table maps normalized question → source_url; it is seeded from `documents/faqs.json` by `seed_faq_url_table.py` and kept fresh by `extract_faq_qa_pairs.py --faq-url-table`. A miss returns `None` (no link button, never fails the query). Fuzzy recovery (`faq_url_map.py`): exact question → exact answer → 50-char prefix, lifting coverage to ~94.5%.

> **⚠️ Gotcha — the byte-identical normalizer.** `_normalize_faq_question` in the Lambda (`main.py`) and `normalize_question` in `scripts/graphrag/faq_url_map.py` **must stay byte-identical**. They are uncoupled copies because the Lambda bundle cannot import from `scripts/`. The seed script writes keys with its normalizer; the Lambda reads keys with its copy. Any silent drift (trailing `?`, whitespace, nbsp/zwsp/BOM, case) makes every lookup miss and silently drops the link. The parity test `test_faq_question_normalizer_matches_seed_script` (`adc34a1`) fails the moment one copy is edited alone — run it after touching either.

> **Operational gotcha — refreshing FAQs is a multi-region dance.** The master FAQ files live in `wis-faq-bucket` (us-west-2); the KB and `FaqUrlTable` are us-east-1. After adding FAQs you must (1) run `sync_faq_bucket.sh` to copy east + trigger a Bedrock `StartIngestionJob` (code redeploy does nothing — the KB is only rebuilt by ingestion), and (2) seed `FaqUrlTable` or new questions get no link. `sync_faq_bucket.sh` reads **root-stack** outputs (`GraphRAGFaqBucketName`, etc.), not the nested-stack output names.

---

## 10. Case law — the deliberately constrained secondary source

**This feature was reworked more than any other. The instinct to make case law vector-searchable is exactly what the team built and then deleted — twice.** Read this section before touching anything case-law.

### Current state

Case law is **thin citation stubs only**. `extract.py::process_case_law_document` emits identity fields (`doc_id, citation, title, statute_refs, source_url, authority_level=3`) with `summary=""`, `topics=[]`, `chunks=[]`. `embed.py` early-returns for `doc_type == "case_law"` — **no chunk embeddings, no doc-level embedding**. Consequences:

- Case-law nodes **never appear in `vector_search`** and are excluded from phase-11 semantic discovery.
- The **only** way the agent reaches a case is by traversing an inbound `CITES` edge from a statute it already found.
- Full opinion text lives in S3 (`raw/case-law-{slug}/...txt`) and is fetched on demand by `fetch_case_opinion`, not stored in the graph.

### How discovery works (three complementary paths)

Cases reach the agent through three paths, each catching what the others miss:

1. **Mirror edge traversal** (`aa99634`): phase 3 writes both `(CaseLaw)-[:CITES]->(Statute)` and the mirror `(Statute)-[:CITES]->(CaseLaw)`. The agent's prompt mandates `get_neighbors` on the controlling statute with `edge_types=["CITES"]` (`dbb06c5`). This catches any case directly linked to a statute the agent already found.

2. **Direct citation resolution**: after `vector_search` returns chunks, their text is regex-scanned for case citations (e.g., "45 Wis. 2d 683") and resolved to CaseLaw nodes via an exact-match property lookup. This catches cases mentioned in the chunks you already have — without traversing any graph edges.

3. **Neighbor-doc citation discovery** (`f87e6d7`): catches cases mentioned only in *neighbor* doc text (not the directly-retrieved chunks). The motivating example: Peter Ogden (2019 WI 23) is cited in the Ag Assessment Guide's chunks but never in the WPAM ag chunks that vector search returns. The approach: use shared statutes to rank which neighbor docs are topically relevant, then read their chunk text and regex-extract citations. See `docs/co-cited-case-law-discovery.md` for the full implementation.

   > **⚠️ The pure graph-traversal approach was tried and abandoned.** An earlier implementation traversed neighbor doc → shared statutes → case law (LIMIT 5). This failed because high-degree statute nodes like WIS-STAT-70.32 have 1500+ CITES edges to CaseLaw — no structural graph signal can pick the right 5. The distinction is in **text** (a specific doc literally cites "2019 WI 23"), not in graph topology. Do not re-attempt a graph-only path for this problem.

### The citation extraction system (shared foundation for paths 2 and 3)

Paths 2 and 3 — and also the `find_case_law` tool — share the same extraction-and-resolution machinery. The core insight: every CaseLaw node in Neptune carries a `citation` property set at ingestion time (e.g., `"45 Wis. 2d 683"`). Regex-extracting citations from text and matching them to that property is an O(1) lookup — no traversal needed, no LIMIT arbitrariness.

**Regex extraction** (`tools.py::extract_citations`). Three compiled patterns cover Wisconsin case citation formats:

| Pattern | Catches |
| --- | --- |
| `\d+\s+Wis\.?\s*2d\s+\d+` | Wisconsin Reports 2d (e.g., "45 Wis. 2d 683") |
| `\d+\s+N\.W\.(?:2d\|3d)\s+\d+` | North Western Reporter (e.g., "985 N.W.2d 69") |
| `\d{4}\s+WI(?:\s+App)?\s+\d+` | Public-domain cites (e.g., "2019 WI 23", "2025 WI App 43") |

After matching, a normalization pass canonicalizes whitespace and `Wis.2d` → `Wis. 2d`. The function returns sorted, deduplicated citation strings.

**Resolution** (`neptune_client.py::resolve_case_citations`). Takes the normalized citation list and does a single parameterized query:

```cypher
MATCH (n:CaseLaw) WHERE n.citation IN $citations
RETURN n.id, n.title, n.citation, n.doc_type, n.authority_level, n.source_url, labels(n)
```

This works because ingestion stamped `citation` on every CaseLaw node. It's an exact property match — no CONTAINS, no fuzzy logic, no traversal.

**Where it's used:**

- **`vector_search` — direct citation resolution (path 2):** joins all retrieved chunk text → `extract_citations` → `resolve_case_citations`. Results populate `related_case_law` in the tool response.
- **`vector_search` — neighbor-doc citation discovery (path 3):** fetches chunk text from topically-ranked neighbor docs → same `extract_citations` → same `resolve_case_citations`. New cases are appended to `related_case_law`.
- **`find_case_law` tool:** if the user's `search_text` itself contains a citation pattern, `extract_citations` runs first and attempts resolution. Only if that yields nothing does it fall back to the slower title-substring search (`neptune.find_case_law`).

**Why this replaced the prior approach:** previously, the only way to discover a case was by traversing graph edges from a statute (`get_neighbors` with CITES). But high-degree statute nodes (WIS-STAT-70.32 has 1500+ CITES edges to CaseLaw) made LIMIT-based discovery arbitrary — there was no structural signal to distinguish the right 5 cases from the other 1495. Citation extraction moved the discriminator from graph topology to **text** — a specific document literally writing "2019 WI 23" is the signal that the case matters in that context.

**Supporting infrastructure:**

- **Section-level `statute_refs`** (`6b28018`): refs are emitted at both chapter (`70`) and section (`70.32`) granularity by reading the running page header of the local statute PDF mirror in `docs/state-laws/`. Without section-level refs, `WIS-STAT-70.32` had zero case-law neighbors.
- **Always link to CourtListener/Scholar** (`6da060c`): both stub cards and fetched-opinion cards link to an external legal database, never the S3 `.txt` (which has no page anchors). ~95% of cases link to CourtListener; the remaining ~5% fall back to Google Scholar. `_apply_case_law_links` enforces this in one post-pass; `_build_opinion_card` sets `s3_key=None`. Opinion text still rides in the card's `content` to inform synthesis — only the user-facing link changed.
- **Parallel-citation collapse** (`ce7e17c`, `93a229c`): one decision gets a separate node per reporter (N.W.2d + Wis.2d + WI App). `_collapse_case_law_by_title` merges them by normalized case-name + year.

### ⚠️ The reversals (do not resurrect)

1. **First-class nodes → annotation-grounded chunks → thin stubs** (`c835f66`). Case law was once vector-searchable with its own embeddings and topics. An annotation-grounding pipeline was then built (and abandoned _the same day_) to store editorial summaries as case chunks. Both were deleted because (a) the annotation already lives on the citing statute's chunks, and (b) embedding the case let the agent answer "from the case" while bypassing the controlling statute — inverting legal authority. The pivot deleted ~180 lines. **Do not re-enable case-law embeddings or the LLM-summary fallback** (`07b2336`, `04e2447` are dead for case nodes). Only `case_annotations.extract_section_for_page` survives in the live path.
2. **S3 `.txt` links → CourtListener/Scholar** (`6da060c`/`7ecaf0d`). The interim "link to the S3 `.txt` until PDF links land" (`6a9bdac`) was replaced. ~95% of cases link to CourtListener; the remainder fall back to Google Scholar. **Do not reintroduce `s3_key` on case-law cards.**
3. **`fetch_case_opinion` plumbing** (`7677cbc`): the tool was called _zero times in 24h of production_ because `load.py`'s phase 2 omitted `d.citation`, so the agent never had the verbatim citation string the tool needs. The fix plumbed `citation` through phase 2 → `neptune_client` → prompt. All three layers are load-bearing; the slug derivation (`citation_to_raw_slug`) is the exact inverse of `upload_local_docs.make_doc_id`.

> **Gotcha — silent degradation.** Section-level refs depend on the local PDF mirror in `docs/state-laws/` being present at extract time. If it's missing, `_derive_case_statute_refs` _silently_ falls back to chapter-only refs, and section-anchored CITES traversals find no cases. Not an error — a quiet quality drop.

> **Stub-promotion authority footgun** (`2631d5f`): `WIS-STAT-*` section stubs carry no `authority_level`. When `_build_rag_documents` promotes such a stub to a parent Document for content (often a WPAM doc), it must keep the stub's **own** identity authority (default Statute=2), not borrow the parent's — else "Wis. Stat. 70.49(2)" renders a WPAM badge.

---

## 11. WPAM edition recency

The Wisconsin Property Assessment Manual is republished annually (the current edition is posted each December for the subsequent calendar year). The graph contains ~15 editions (`wpam-...-2011` through `-2026`). **DOR rule: ALWAYS use the most current WPAM unless the user explicitly asks about a specific year.** Old editions have different chapter structures (the WPAM was reorganized in 2017 — e.g., Chapter 9 changed from Commercial Valuation to Real Property Valuation, with commercial content moving to Chapter 13) and MUST NOT be cited for current guidance.

### 11.1 The problem

Neptune Analytics `topKByEmbedding` has **no pre-filtering capability** — you cannot filter by `edition_year` before the vector search runs. With ~15 editions of semantically near-identical content, a naive `top_k=10` vector search returns mostly old-edition chunks (the sheer volume of historical editions dominates the vector space). Heading-based dedup alone was insufficient because the 2017 reorganization means old chapters have different headings that don't collapse with current ones.

### 11.2 The solution: over-fetch + strict edition filter

Three layers enforce recency:

1. **`current_wpam_year` — dynamic resolution from Neptune** (`neptune_client.py`). A cached property queries `max(d.edition_year)` across all `FW-WPAM` documents on Lambda cold start. This is the authoritative "what year is current" — no hardcoded year constant.

2. **Over-fetch at retrieval time** (`tools.py`). When no `target_wpam_year` is set, `vector_search` requests `top_k * 3` chunks from Neptune (e.g., 30 instead of 10). This casts a wide enough net that current-edition chunks appear in the result set even when old editions dominate the top ranks. When a `target_wpam_year` IS set, no over-fetch (the user wants a specific edition, so old results are expected).

3. **Two-pass dedup** (`wpam_dedup.py::dedupe_wpam_chunks`):
   - **Pass 1 — heading collapse:** groups WPAM chunks by normalized heading, keeps one per group (prefer `target_year` if set, else `max(edition_year)`).
   - **Pass 2 — strict edition filter:** `allowed_years = {current_wpam_year}`. If `target_year` is set, add it: `allowed_years = {current_wpam_year, target_year}`. Every WPAM chunk whose `edition_year ∉ allowed_years` is **dropped** — including singletons with unique headings from old editions. Non-WPAM chunks and WPAM chunks missing `edition_year` pass through unchanged.

   After dedup, the result is truncated back to `top_k`.

4. **Prompt reinforcement** (`prompt.py`): instructs Claude to ONLY cite the current WPAM edition, ignore any old-edition chunks that slip through, and pass `target_wpam_year` to subsequent tool calls when `refine_query` extracts one.

### 11.3 The two retrieval paths

**Default (no year in query):**

1. User asks: "What information is used to determine my assessment?"
2. `faq_search` runs on verbatim query (always first)
3. Claude calls `vector_search(query="...")` — no `target_wpam_year`
4. Neptune returns top 30 chunks (`top_k * 3` over-fetch)
5. `dedupe_wpam_chunks` runs:
   - Pass 1: heading collapse (same heading across editions → keep newest)
   - Pass 2: edition filter — `allowed_years = {2026}` (from `neptune.current_wpam_year`). Everything not 2026 is dropped.
6. Truncate to 10 chunks, return to Claude
7. Claude only sees current WPAM + non-WPAM sources (statutes, admin rules, FAQs, etc.)

**User specifies a year:**

1. User asks: "What did the 2018 WPAM say about commercial valuation?"
2. `faq_search` runs
3. Claude calls `refine_query` → extracts `target_wpam_year: 2018`
4. Claude calls `vector_search(query="...", target_wpam_year=2018)` — no over-fetch (`top_k` stays at 10)
5. `dedupe_wpam_chunks` runs:
   - Pass 1: heading collapse → prefers 2018 chunks
   - Pass 2: edition filter — `allowed_years = {2026, 2018}`. Only these two editions survive; 2011, 2020, 2022, etc. are dropped.
6. Claude sees both the requested historical edition and current for comparison
7. Prompt tells Claude to cite what the user asked about

### 11.4 Stamping `edition_year` at load time

`wpam_year.py::extract_wpam_year_from_doc_id` grabs the last 4-digit group from the doc_id, plausibility-gated (2010 .. current year + 1; the +1 covers a December-published edition). Both phase 2 (Doc nodes) and phase 7/`phase_8_chunks` (Chunk nodes) call it for `FW-WPAM` docs. `edition_year` is **denormalized onto every Chunk** (`05092f3`) so dedup needs no Neptune join.

### 11.5 Wire path

`edition_year` is carried by `RAGDocument`, set by the agentic Lambda, persisted by `save_chat_history`, copied into `websocket_utils.SourceDocument` by `resource_streaming`, and accepted by the Zod `SourceDocumentSchema` + frontend `Document` type. It reaches the browser store but **no UI component renders it yet** (wire-only v1). Adding an edition badge to the citation card is a frontend-only follow-up; the data is already there. (History note: until this fix, `websocket_utils.SourceDocument` lacked the field and `resource_streaming` didn't copy it, so the value was silently dropped at the WebSocket boundary — guarded now by `test_source_document_carries_edition_year` in `test_resource_streaming.py`.)

> **⚠️ The cosine dedup "pass B" was cut (YAGNI), and there are NO `SUPERSEDES` edges between editions.** Neptune's `vector_search`/`get_neighbors` Cypher returns only similarity _scores_, not raw chunk embeddings, so a cosine pass would need a second algo call + ~10-20KB extra payload per query (`cb13072`). Recency is handled purely at the retrieval layer (over-fetch + edition filter), not as graph edges. The prompt mentions `SUPERSEDES` generically — do not assume edition-supersession is modeled as edges.

> **Gotcha — re-ingest is scoped here, not full.** Because `edition_year` is a property-only mutation (no edges change), populate it with `load.py --source-filter wpam- --start-phase 2 --stop-after-phase 2` then `--start-phase 7 --stop-after-phase 7`. Chunks loaded _before_ this feature have no `edition_year` and are dedup-ineligible (they pass through, never collapsed) — a mixed graph silently under-dedups until a full WPAM chunk re-ingest.

---

## 12. Citations and source cards

Citation cards carry a **stable reference** (`s3_key`, `start_page`, `end_page`), never a live URL. The URL is minted **on demand at click time**.

> **⚠️ The biggest reversal here, and CLAUDE.md is stale on it.** `CLAUDE.md:131` still says _"agentic_retrieval/main.py generates presigned S3 URLs with #page=N fragments."_ **That is the dead design.** Eager presigned URLs (`_generate_source_links`, the module-level `s3_client`, `PRESIGNED_URL_EXPIRY`) were removed (`410b833`). They expired in ~15 min and were frozen into DynamoDB chat history, so every citation in a _restored_ session pointed at a dead URL. Now `agentic_retrieval` populates only `s3_key/start_page/end_page` (`_generate_source_label` returns a label only); a dedicated **`citation_resolver` Lambda** mints a fresh 15-min URL per click. `RAW_BUCKET` still exists in `agentic_retrieval` _only_ because `fetch_case_opinion` GETs the case-law `.txt` directly — unrelated to URL minting.

### The click-time flow

1. Card click → `chooseSourceTarget(document)` (`source-target.ts`) decides the destination: a **PDF `s3Key` wins** (presigned + `#page` anchor); a flat `.txt`/case-law card **yields to a clean public `sourceUrl`** (Scholar/revenue.wi.gov) — because `.txt` has no page anchor (`876a6df`).
2. For an `s3` target: `window.open('about:blank')` opens a popup _synchronously_ (browsers only allow `window.open` in the sync tail of a click), then `buildResolverUrl` fetches the Cognito id token async and redirects the popup to `GET /citation?s3Key=...&token=<jwt>&page=N`.
3. The `citation_resolver` Lambda validates the `raw/` prefix + page, `head_object`s the key, mints a 900s presigned URL, appends `#page=N`, and returns a **302** with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

> **⚠️ Several non-obvious things are load-bearing and were each a fix:**
>
> - `GET /citation` has its **own** JWT authorizer reading the token from `?token=` (`04f47cf`), because `window.open` can't set an `Authorization` header and an HTTP API JWT authorizer allows only one identity source.
> - The 302's `Referrer-Policy: no-referrer` stops the JWT leaking to S3 via `Referer`; `Cache-Control: no-store` stops a cached dead redirect after expiry. Both required.
> - The `s3` `window.open` deliberately **omits `noopener`** — `noopener` makes `window.open` return `null`, breaking the popup-first pattern (`5337432`). The `url` case (Scholar/gov) _does_ use `noopener,noreferrer`.
> - `citation_resolver` reads `RAW_BUCKET` **inside the handler**, not at import, for test isolation and fail-fast on an empty CDK wire (`e3e118a`).

### Inline prose citations

The response LLM writes `[Title](doc:documentId)` instead of embedding 500-char presigned URLs that broke the markdown parser (`37f0c9c`). The frontend's `animated-markdown.tsx::resolveHref` rewrites `doc:<id>` to a URL from a per-message `docUrls` map. That map is built from `sourceUrl` **only**, so a PDF-only doc (s3Key, no sourceUrl) renders a non-clickable inline span — its click-through is the citation card's resolver path, not the inline link. Intentional but surprising.

---

## 13. The shared-contract discipline

This is the most error-prone cross-boundary concern in the codebase. Inter-Lambda contracts live in `packages/shared/lambda_layers/step_function_types/models.py` (Pydantic). The `CamelCaseModel` base converts snake_case Python to camelCase JSON via an alias generator — so `start_page` becomes `startPage` on the wire.

**The frontend hard-rejects unknown shapes.** Every WebSocket message is validated by `WebSocketMessageSchema.parse()` against a Zod discriminated union. A backend-only field addition, or a shape the union doesn't know, **drops the entire frame** and shows the user an error. This applies to trace and logging messages too.

> **⚠️ Adding a citation field requires editing FIVE places** or it silently drops on some path:
>
> 1. `RAGDocument` (`step_function_types/models.py`)
> 2. `SourceDocument` (`websocket_utils/models.py`) — note this is a _second_, hand-duplicated `CamelCaseModel`
> 3. `resource_streaming`'s `SourceDocument` construction
> 4. **Both** chat-history writers (`agentic_retrieval.save_chat_history` _and_ legacy `messages/streaming.log_chat_history`)
> 5. the Zod `SourceDocumentSchema`
>
> (Worked example: `editionYear` was added to places 1, 4, and 5 but missed places 2–3, so it was silently dropped at the wire boundary until a follow-up added it to `websocket_utils.SourceDocument` and `resource_streaming`. See §11.)

> **⚠️ Use `.nullish()`, not `.optional()`, for new Zod optional fields.** Pydantic serializes an unset `Optional` as JSON `null`, but `z.optional()` accepts only `undefined` (key absent). A single `null` on `s3Key` once rejected the _entire_ documents frame and dropped every citation card (`7bd99e0`). The helpers `optStr`/`optInt`/`optNum` do `.nullish().transform(v => v ?? undefined)`.

The CLAUDE.md "WebSocket Contract" section is the canonical statement of this rule for runtime messages; this section extends it to the persistence and resource paths.

---

## 14. Live agent-trace streaming

The agent's per-turn reasoning and tool calls stream to the chat UI as a live, collapsible "Thought for Xs" chain-of-thought, replacing a static 3-step placeholder.

**Backend.** `_emit_trace` (`main.py`) is the single emission helper. It builds an `AgentEventMessage` (Pydantic, `responseType: "agent-event"`) and ships it. It is hard-gated: returns immediately if `EMIT_AGENT_TRACE` is false or `ws_server is None`, and swallows all send errors — a dead/stale WebSocket can never abort the loop. `seq` is a per-request monotonic counter. Emission points: `phase` events, turn-0 synthetic `tool_call`→`tool_result` pairs, per-turn `reasoning`/`tool_call`/`tool_result`, and a terminal `loop_complete`. The `answer` tool deliberately emits no `tool_result` (its meaning is in `loop_complete`).

**Backend pre-formats everything.** `_build_tool_call_summary`/`_build_tool_result_summary` produce the human-readable strings and a camelCase metadata dict; `doc_titles` are best-effort-resolved with doc_id fallback so array lengths always match. The UI just picks a verb and renders.

**Frontend.** `appendAgentTraceEvent` dedupes by `seq`, then for a `tool_result` finds the most recent same-key (`tool:<name>:<turn>`) event and, if it's still `pending`, **replaces it in place** (one slot transitions hollow "Searching" → filled "Found N"). Rendering collapses consecutive completed results for the same tool, and encodes dot states: error (red), miss (hollow + muted = ran, found nothing), done (solid), pending (hollow).

> **⚠️ Two reversals + one deliberate vestige:**
>
> - **Protocol replaced** (`fa210de`): an older `responseType: "agent-trace"` + `TraceContent{event,label,status,...}` was thrown out for `"agent-event"` + `AgentEventMessage{kind,turn,seq,...}` because the frontend schema had drifted from what the deployed Lambda sent. `TraceContent`/`TraceMessage`/`AgentTraceMessageSchema` are **dead**.
> - **Vestige (not a bug):** the WebSocket _envelope_ `streamId` is still the literal `"agent-trace"` (`utils.py`) even though `responseType` is `"agent-event"`; the frontend `streamId` enum still lists `"agent-trace"` to accept it. Don't "fix" one side without the other.
> - **`faq_short_circuit` is not a terminalReason.** The 2026-04-30 spec and `f62696e` describe a short-circuit terminal event that no longer exists (the FAQ policy reversal, §9). The only terminalReasons are `answer_tool` and `assistant_text_or_fallback`. Don't test for `faq_short_circuit`.

> **⚠️ The metadata allow-list is duplicated and must stay in lockstep:** `ALLOWED_METADATA_KEYS` (`main.py`) mirrors a Set in `trace-metadata.ts`. This is defense-in-depth so a future edit can't leak raw query/chunk text into the UI. A new key added to only one side is silently dropped. (Three env flags govern this area and are distinct: `EMIT_AGENT_TRACE` = WebSocket kill switch, `LOG_AGENT_TRACE` = CloudWatch logging, `LOG_QUERY_TEXT` = raw query preview in logs.)

> **Gotcha — trace is not persisted.** `agentTrace` is per-Query and dropped on reset. Resuming a past conversation from DynamoDB shows the static 3-step placeholder, not the real trace.

---

## 15. Sessions, auth, and chat history

Cognito-authenticated users get a sidebar of past chats they can resume, rename, or delete. Two DynamoDB tables, **different key schemas**:

- **SessionTable** — PK `sessionId`; `userIdIndex` GSI (PK `userId`, SK `lastMessageAt`) for per-user listing; `connectionId` GSI for WebSocket.
- **ChatHistoryTable** — PK `queryId`; `sessionIdKey` GSI (PK `sessionId`, SK `timestamp`) for per-session reads and the delete cascade.

All session/history handlers live in one Powertools Lambda (`packages/sessions/lambdas/chat_api/main.py`) behind an HTTP API with a Cognito JWT authorizer. Routes: `POST /session`, `GET /sessions`, `PATCH /session/{id}`, `DELETE /session/{id}`, `POST /session/{id}/message`, `GET /session/{id}/history`, `POST /session/{id}/feedback`, and the separately-authorized `GET /citation` (§12).

**Chat history is written by the GraphRAG path itself.** `agentic_retrieval::save_chat_history` persists `{queryId, sessionId, timestamp, query, answer, resources}` **after the loop returns but before ResponseStreaming runs**, so resume works even if streaming fails. DynamoDB is the source of truth for resume; the WebSocket stream is a live optimization. `get_chat_history` reads back, caps at `MAX_HISTORY_TURNS=5`, and (legacy path) accepts `exclude_query_id` so the in-progress turn isn't fed back into the model.

> **⚠️ Adding a `RAGDocument` field requires adding it to `save_chat_history`'s field-by-field rebuild,** or resumed cards silently lose it. This bit twice: `authorityLevel` + `content` were omitted (cards resumed with no badge, blank preview — `b49c6fb`), and `discoveryTag` on the legacy path (`d695792`). Both have regression tests now.

> **⚠️ JWT accessor: `request_context.authorizer.jwt_claim`** (the flat Powertools property), **not `.jwt.claims`** (which raises `AttributeError` and 500s every authenticated request — `8a00aff`). Easy to "correct" the wrong way.

> **⚠️ Decimal encoding.** `TypeDeserializer` returns `Decimal` for any DDB number; `json.dumps` rejects it. Once resources carried numeric `startPage`/`endPage`, `GET /history` began 500ing for _new_ chats while older string-only rows loaded — a confusing partial failure. Every `create_api_response` runs a `_json_default` mapping whole→int, fractional→float (`98621f1`).

Other fixed traps: `$connect` uses `update_item` with `ConditionExpression attribute_exists(sessionId)` (a `put_item` would clobber `userId`/`title` and break the GSI — `8a00aff`); delete-session clears the store **only** when the deleted id is the active session (`c8871a2`); `reset()` clears `sessionId` so "New Chat" starts fresh (`8c2bd0e`); CORS allows `GET`/`DELETE` (`a810d21`); the API client unwraps both Lambda-proxy v1 and HTTP-API v2 response shapes (`3e80bd4`).

---

## 16. Frontend presentation layer

Next.js + Zustand (immer) + a WebSocket hook. Per query, `ChatMessage` renders four stacked regions: the right-aligned user bubble, a "Thinking for Xs" shimmer label, a collapsible status-step timeline, and the streamed answer + inline sources + feedback bar.

**Optimistic UI with ID swap.** On send, the store immediately adds a `Query` with an optimistic id `pending-<timestamp>` so the user bubble + "Thinking" appear before any round-trip. When the server returns the real `queryId` (or a WebSocket frame arrives first), `replaceQueryId` rewrites the `queries` map, `queryOrder`, and `currentQueryId`. The thinking timer is anchored to the query timestamp so it survives the swap (`d8dd572`).

**Markdown is a local react-markdown wrapper** (`animated-markdown.tsx`), **not flowtoken** (removed from `package.json`). flowtoken had no remark/rehype pipeline, so `$$math$$` leaked as raw text and GFM tables didn't render (`66c3a24`). The wrapper runs `remark-gfm` + `remark-math` + `rehype-katex` and reproduces the streaming word-reveal by recursively splitting string leaves into spans with a CSS animation (`animation-iteration-count: 1` = animate once per mount, so only newly-added words animate). **KaTeX nodes and table cells are passed through intact** — word-splitting them shreds KaTeX internals and shifts column widths. **Do not reintroduce flowtoken.**

**Inline per-message sources** (`f14911b`): each message owns its source cards in a collapsible grid; there is no shared side panel (it made source→answer attribution ambiguous in multi-turn chats). `DocumentList` survives but is used only by the mock-chat demo page — don't wire it back into the live layout.

**Badges.** `AuthorityBadge` maps the 9 integer levels to colors (FAQ hardcodes level 6 client-side). `DiscoveryBadge` maps `discoveryTag` to an outline badge (`unknown` suppressed).

**Light-theme a11y pass** (`91bac57`, `439e9e9`): the background was switched to pure white; the team **darkened** the muted-foreground/input tokens and bumped badge color levels to pass WCAG AA, rather than reverting the white. One Tailwind v4 quirk: `bg-card/90` (token + slash-opacity) failed to resolve on the input, so it uses the literal `bg-white/90`. Don't "fix" low-contrast badges by lightening them back to 100-level colors.

> **Tech-debt notes for the UI:** `LoadingStrip` (`loading-grid.tsx`) is dead code; `getSourceActionLabel` is a brittle string-substring heuristic; `mergeTraceMetadata` carries dead snake_case fallbacks the camelCase backend never emits; there's leftover `console.log` in `MessageOptionsBar`.

---

## 17. Deployment and operations

### Regions and discipline

| Region        | Stack                            | Rule                                     |
| ------------- | -------------------------------- | ---------------------------------------- |
| **us-west-2** | Production (legacy OpenSearch)   | **Do not deploy from feature branches.** |
| **us-east-1** | `WisconsinBotGraphRAG` (Neptune) | All GraphRAG development.                |

Always use `--profile wisco`. Always run `cdk diff` before deploy to confirm only additive changes. GraphRAG changes must be purely additive — the existing stack is protected.

### First-time bring-up checklist

The pieces are scattered; here is the consolidated order for a fresh GraphRAG stack:

1. `cdk bootstrap` the account/region (us-east-1).
2. `bun install`, `bun run bundle`, then `cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG`.
3. **Seed `ModelConfigTable`.** On a fresh stack this table is empty, and an unseeded config makes `ResponseStreaming` fail _silently_ after sending `answer-event:start` — the frontend hangs on "loading." The `ragResponse` config (model id, system prompt) lives in `config/model_configs.toml`. (This is a known footgun recorded in project memory, not yet in any per-subsystem doc.)
4. **Sync FAQs + ingest the KB:** `sync_faq_bucket.sh` (copies us-west-2 → us-east-1, triggers Bedrock `StartIngestionJob`).
5. **Seed `FaqUrlTable`:** `seed_faq_url_table.py` from `documents/faqs.json`.
6. **Run the full ingestion** (§6) to populate Neptune: scrape → extract → embed → load.

### Running ingestion locally

Ingestion runs on your machine, not the EC2 instance, unless asked otherwise. Two recurring environment gotchas:

- **SSL on macOS:** set `AWS_CA_BUNDLE` to the certifi cert path, or Python 3.13+/3.14 fails with `SSLError` after ~200 S3 calls. `export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")`.
- **Bedrock model IDs** require the full inference-profile format (`us.anthropic.claude-sonnet-4-6`). Verify via `aws bedrock list-inference-profiles`.
- Use `uv venv` + `requirements.txt`, not one-off pip installs. (Note: lint with `uvx ruff`, not `uv run ruff` — ruff isn't a uv dep.)

### Testing

- TypeScript: `bun run test` (Jest).
- Python: `uv run pytest`. **But GraphRAG Lambda tests pollute `sys.modules`** — run them per-directory, not combined with the messages tests, or you'll get import collisions. `pdfChunker` requires boto3 pre-stubbed at import (§7).
- 32 Python test files, 6 TS test files. Notable regression guards: the FAQ normalizer parity test, the `save_chat_history` authority/content test, the phase-8 batching test, the page-tracking/TOC/table tests.

### Observability — tracing one query

A single `query_id` threads through the handler → Step Function → streaming Lambdas → WebSocket. To debug a failed query: read the agent trace (if `EMIT_AGENT_TRACE`), then CloudWatch structured logs (gated by `LOG_AGENT_TRACE`/`LOG_QUERY_TEXT`). The recurring error signatures to recognize: a silent `[]` from an undecoded Neptune payload (§5.1), a dropped WebSocket frame from a Zod rejection (§13), and a 404 popup from a rotated/re-prefixed raw bucket (§12).

---

## 18. Appendix

### 18.1 Glossary

- **Agentic retrieval** — the Claude tool loop that replaced the legacy classifier + retrieval Lambdas.
- **Stub node** — a `Statute`/`AdminRule`/`CaseLaw` node with identity only (no chunks/summary), created on demand from a citation regex.
- **Discovery tag** — how a doc entered the evidence set (`vector-search`, `graph-neighbor`, `fetched`, `framework-list`, `opinion-fetched`, `unknown`); drives the DiscoveryBadge.
- **Primary truth** — a high-confidence (≥0.70) FAQ that anchors the answer while the graph supplements it.
- **WPAM** — Wisconsin Property Assessment Manual (annual editions).

### 18.2 Catalog of intentional "do NOT revert" decisions

| Decision                                                   | Why it's right                                                          | Commit               |
| ---------------------------------------------------------- | ----------------------------------------------------------------------- | -------------------- |
| `execute_query` decodes `payload.read()`                   | neptune-graph returns a StreamingBody; raw `["results"]` is always `[]` | `610972f`, `4f0e95a` |
| Inlined-literal Cypher (embedding/topK/depth)              | Neptune rejects parameterized CALL args                                 | `de53482`            |
| Catch `UnprocessableException` as throttling               | Neptune signals overload two ways                                       | `c198bc0`            |
| Phase-8 byte-cap on UNWIND                                 | count caps don't prevent per-query OOM                                  | `26a81e3`            |
| FAQ loop **always continues** (no short-circuit)           | FAQ-only answers had no citable evidence                                | `3ae5894`            |
| Deterministic turn-0 `faq_search` (bypass Claude)          | Claude paraphrase hurt KB recall                                        | `13a90c3`            |
| Case law = thin stubs, no embeddings                       | annotation lives on the statute; embedding inverts authority            | `c835f66`            |
| Statute→CaseLaw mirror edge                                | cases unreachable by outgoing traversal otherwise                       | `aa99634`            |
| Neighbor-doc text scanning (not graph-only traversal)      | high-degree statutes (1500+ CITES) make LIMIT arbitrary; text has the signal | `f87e6d7`      |
| Case/opinion cards link to CourtListener/Scholar, not S3   | `.txt` has no page anchor                                               | `6da060c`            |
| On-demand citation resolver (no eager presigned URLs)      | URLs expire and rot into chat history                                   | `410b833`            |
| `chooseSourceTarget`: PDF s3Key wins, `.txt` yields to URL | `.txt` has no `#page` anchor                                            | `876a6df`            |
| Tool exception → error tool-result (no crash)              | one bad tool call killed the whole request                              | `686da54`            |
| `.nullish()` not `.optional()` in Zod                      | Pydantic emits `null`; one null dropped the frame                       | `7bd99e0`            |
| Per-line page tracking (no substring matching)             | substring matching inflated 55% of page ranges                          | `9bc8346`            |
| Local react-markdown wrapper (not flowtoken)               | flowtoken couldn't render math/tables                                   | `66c3a24`            |
| `update_item` on `$connect` (not `put_item`)               | put_item clobbers userId/title                                          | `8a00aff`            |
| JWT via `jwt_claim` (not `.jwt.claims`)                    | the latter 500s every request                                           | `8a00aff`            |
| Authority level defaults to `None`, never a number         | a `6` default badged 607 nodes as FAQ                                   | `f1fc513`            |

### 18.3 Known-stale docs (do not trust at face value)

| Doc                                                          | What's stale                                                                                                                            | Reality                                                                                                                                                                                                                        |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `CLAUDE.md:131` (Citation Support)                           | "generates presigned S3 URLs"                                                                                                           | Eager minting was removed; URLs resolve at click time via `citation_resolver` (§12).                                                                                                                                           |
| `docs/graphrag.md`                                           | Domain examples are **healthcare/PII compliance** (FedRAMP, MARS-E, SAM-5340); claims the prompt is externalized to `system_prompt.txt` | This is an inherited philosophy doc from a sibling project. The WI prompt is **inline** in `prompt.py`; the WI edge types are §4's. Read it for _principles_ (auto-enrichment, anti-hallucination), not for WI-specific facts. |
| `docs/superpowers/specs/2026-04-30-agent-trace-ui-design.md` | Pre-replacement schema, a `faq_short_circuit` terminalReason, and `totalInputTokens`/`totalOutputTokens` dev fields                     | None match shipped code (§14).                                                                                                                                                                                                 |
| `docs/superpowers/plans/2026-05-24-wpam-edition-recency.md`  | Shows `datetime.utcnow()`                                                                                                               | Shipped code uses `datetime.now(UTC)`.                                                                                                                                                                                         |
| `README.md`                                                  | Last touched by the original team (2026-02-13)                                                                                          | Predates the entire GraphRAG migration.                                                                                                                                                                                        |

### 18.4 Where to start, by task

- **Changing retrieval behavior** → §8 (the loop) + `prompt.py`; remember §13 (contracts) if you add a field.
- **Changing the graph shape or edges** → §4 + §6; budget for a **full re-ingest** (§6.3).
- **Changing chunking** → §7; full re-ingest; run `purge_orphan_chunks.py` after.
- **Adding a citation/source field** → §13's five-place checklist.
- **Touching FAQs** → §9; run the normalizer parity test; remember the multi-region sync.
- **Anything case-law** → §10, and read the reversals before writing code.

---

## Known Issues / Future Cleanup

### Duplicate CaseLaw nodes for parallel reporter citations

The same court opinion often has multiple reporter citations (e.g., `45 Wis. 2d 683` and `173 N.W.2d 627` are both *State Ex Rel. Markarian v. City of Cudahy*). The ingestion pipeline creates a separate CaseLaw node for each citation, and may create duplicate CITES edges from the same statute to the same node across ingestion passes.

**Impact:** `find_case_law` and `get_neighbors` return what appear to be duplicates (same title, different node IDs). The agent may cite both IDs, producing two source cards for one case.

**Future fix:** Merge parallel reporter nodes into a single canonical node (keyed on the Wisconsin Reports citation when available, falling back to N.W.2d/3d). Store alternate citations as a list property. Deduplicate CITES edges during ingestion (idempotent upsert). This is a data model change in the load phase (`scripts/graphrag/load.py`) and would require a re-ingest or a one-off migration script.

---

_This guide documents the state of `feat/graphrag-migration` as of commit `f87e6d7` (2026-06-16). When you change a subsystem, update its section here and add any new "do NOT revert" decision to §18.2 — that table is the cheapest insurance against re-breaking solved problems._

Other random notes from Jonah:
during one of the client meetings, they noticed a message that asked them to allow their computer to discover other computers on the same network. This happens because they have their own cognito setup.
