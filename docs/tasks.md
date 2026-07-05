# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 5 | Replace LLM classification with structural parsers | — |
| 17 | Handle multipart queries (split or unified answering strategy) | — |
| 20 | Add user persona setting (government worker vs. citizen) | — |
| 21 | Add z-score normalization to search_document result filtering | — |
| 26 | Admin ingestion page — ingest documents via URL from the UI | — |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags | — |
| 32 | Show trimmed section page index in answer synthesis trace card | — |
| 35 | Graph wiring overhaul — stubs as routing nodes, not dead ends | — |
| 39 | Discover and ingest 2026 news pages | — |
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing | — |

## Done

| # | Task |
|---|------|
| 3 | Tune model tone — reduce overconfident statements |
| 4 | Replace Step Function with direct Lambda invoke |
| 7 | Refactor agentic retrieval Lambda (main.py) |
| 8 | Add boilerplate stripping before chunking |
| 10 | Externalize prompts from Lambda code |
| 11 | Add managed compute for ingestion (Fargate) |
| 12 | Fix WebSocket streaming hang on background tabs |
| 13 | Harden authority hierarchy enforcement (authority-aware re-ranking) |
| 9 | Reduce topic clustering batch size |
| 15 | Add settings modal with detailed trace toggle |
| 6 | Reduce PDF chunk size for consistency and precision |
| 16 | Support URL-based session routing and preserve "new chat" state on reload |
| 18 | Show traversed sources in UI during agentic retrieval |
| 19 | Fix train-of-thought flicker on sidebar session hover |
| 24 | Multi-citation source cards (aggregate inline citations per parent doc) |
| 1 | Disambiguate generic queries before full retrieval |
| 14 | Improve case law discovery in vector_search auto-enrichment |
| 23 | Strip WPAM running headers from chunk text |
| 25 | Fix WPAM 2025 garbled table chunks and heading metadata |
| 2 | Fixing linking issues |
| 22 | Apply over-fetch multiplier when target_wpam_year is set |
| 29 | Enable prompt caching for agentic retrieval (switch to invoke_model) |
| 30 | get_section chunk grid visualization — show cosine/z-score per chunk in trace UI |
| 28 | WPAM 2019 heading loss — boilerplate stripper keeps TOC copy, strips real chapter start |
| 36 | Full corpus refresh — scrape, ingest missing docs, reingest stale content |
| 38 | Restructure tools/ directory — consolidate ingestion pipeline |

---

## Task Details

---

### Task 5: Replace LLM classification with structural parsers

**Problem:** Every non-case-law document goes through a full `bedrock.converse()` call with Claude Sonnet for classification (doc_type, title, statute_refs, topics, summary). Case law (2468 docs = 77%) already has a fully structural extraction path (`process_case_law_document`), but the remaining ~742 docs each make an LLM call.

**Actual cost/time (measured 2026-06-23):** ~$8 total for 742 Sonnet calls (4000 input chars + ~500 output tokens each), ~10 minutes at 3 workers. Not a significant bottleneck relative to embed/load phases.

**What `classify_document()` produces and where it's used in the graph:**
| Field | Graph usage | Structurally derivable? |
|-------|-------------|------------------------|
| `title` | Document node label, source cards | Partially — predictable for WPAMs/statutes, varied for guides |
| `summary` | Document node content, `get_document` tool result, `find_stub_promotion` relies on `summary IS NOT NULL` | No — genuinely requires NL understanding |
| `statute_refs` | `CITES` edges (Doc→Statute) | Partially — already regex-extracted per-chunk, but doc-level only uses first 4000 chars |
| `admin_rule_refs` | `CITES` edges (Doc→AdminRule) | Same as above |
| `implements_refs` | `IMPLEMENTS` edges | No — requires understanding doc's relationship to statute |
| `topics` | `COVERS_TOPIC` edges to Topic nodes | No — requires NL understanding |
| `doc_type` | Already in `metadata.json` for all docs | Yes — fully redundant with metadata |

**Conclusion:** The LLM's primary value is `summary`, `topics`, and `implements_refs`. For docs where `metadata.json` already provides `doc_type`, `framework_id`, and `authority_level` (all docs), the LLM is mainly generating the human-readable summary and graph relationship edges. At $8/full-ingest, this is a low-priority optimization — only worth pursuing if extraction becomes a frequent bottleneck or Bedrock throttling becomes an issue.

**Direction (if pursued):** Build structural parsers only for the highest-volume remaining categories: news pages (~450 docs, highly templated job postings/announcements), FAQ pages (~40 docs, structured HTML). Keep LLM for gov publications, IAAO, and other varied documents where the summary and topic extraction genuinely require comprehension.

---

### Task 17: Handle multipart queries (split or unified answering strategy)

**Problem:** Users sometimes ask multipart questions (e.g., "What's the deadline for filing my assessment appeal, and what documents do I need?"). The system currently treats the entire input as a single query for retrieval, which can cause one part to dominate search results and the other part to get a shallow or missing answer.

**Direction (needs further design):**

- **Detection** — Add a prefilter step that scans the incoming query for multipart structure (multiple questions, conjunctions joining distinct topics, numbered sub-questions).
- **Strategy options (TBD):**
  - *Answer all at once* — Keep as a single retrieval pass but ensure the agent explicitly addresses each sub-question in its response. May require prompt reinforcement.
  - *Sequential split* — Decompose into separate sub-queries, run retrieval independently for each, then synthesize a combined response. Better retrieval precision but higher latency and token cost.
  - *Ask the user to narrow* — If the parts are too divergent (different topic areas), prompt the user to ask one at a time.
- **Open questions:** Where does the split happen — before or after FAQ search? Does the agent's existing tool loop already handle this well enough with the right prompt nudge, or does it need structural intervention?

---

### Task 20: Add user persona setting (government worker vs. citizen)

**Problem:** The chatbot currently gives the same style of answer regardless of who is asking. A government worker (assessor, clerk, DOR staff) needs advice framed as guidance they can relay to a citizen or apply in their professional role — citing authority, procedural steps, and internal references. A citizen asking the same question needs a plain-language explanation of what applies to *them* and what action *they* should take.

**Direction:**

- **Settings panel addition** — Add a "Personalization" section to the existing settings modal (`settings-modal.tsx`). Provide a toggle or radio group with two options:
  - *Citizen* (default) — "I'm a property owner or taxpayer looking for answers about my situation"
  - *Government worker* — "I'm an assessor, clerk, or DOR staff member looking for guidance to provide to others"
- **Persistence** — Store the selection in the existing Zustand settings store (`settings-store.ts`) with localStorage persistence, same pattern as the detailed-trace toggle.
- **Prompt conditioning** — Pass the selected persona to the backend (e.g., as a field on the chat message payload). The system prompt should adapt tone and framing:
  - *Citizen mode:* second-person ("you may be eligible…"), plain language, action-oriented
  - *Government worker mode:* third-person ("the property owner may qualify…"), cite specific manual sections and procedural checklists, reference internal processes
- **Open questions:** Should the persona also influence retrieval weighting (e.g., boost WPAM/internal guides for gov workers, boost plain-language FAQs for citizens)? Or is prompt-level framing sufficient?

**Key files:**
- `frontend/src/components/settings/settings-modal.tsx` — add personalization section
- `frontend/src/stores/settings-store.ts` — add persona field
- `config/model_configs.toml` — adapt system prompt to incorporate persona context
- `backend/lambdas/agentic_retrieval/main.py` — read persona from message payload and inject into prompt

---

### Task 21: Add z-score normalization to search_document result filtering

**Context:** Z-score normalization was investigated for `vector_search` (2026-06-25) and found non-viable there — heterogeneous source types create overlapping score distributions where below-mean often means "authoritative but linguistically different," not "irrelevant." However, `search_document` operates within a single document where all chunks share the same writing style, vocabulary density, and embedding characteristics.

**Why it works for search_document:** When searching within one document (e.g., "find the section about agricultural classification in WPAM"), score differences genuinely reflect relevance differences — it's one distribution, not five overlapping ones. Z-score can separate "this chapter answers your question" from "this chapter mentions a keyword in passing."

**Current behavior:** `search_document` fetches 800 chunks globally, filters to the target doc (often 30-100 matches for WPAM), then returns top_k. No adaptive quality floor — it always returns exactly top_k results regardless of whether the bottom results are noise.

**Proposed:** After filtering to the target doc's chunks, compute z-scores and drop chunks with z < 0 (below mean). This adaptively shrinks results when scores form a tight cluster with a few outliers, while preserving more results when scores are broadly distributed.

**Other potential locations:**
- **FAQ search** — signal "no confident match" when all scores are low and flat
- **Case law discovery** — separate on-point cases from tangential citations of the same broad statute

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — `search_document` (line 857), `faq_search` (line 534)
- `backend/lambdas/agentic_retrieval/neptune_client.py` — raw scores returned from Neptune

---

### Task 26: Admin ingestion page — ingest documents via URL from the UI

**Problem:** Ingesting new documents (PDFs, web pages) currently requires CLI access and manual invocation of the scrape → extract → embed → load pipeline. DOR staff or project maintainers should be able to paste a URL into an admin page and have the document ingested without SSH/CLI.

**Design:**

- **New admin route** — `/admin/ingest`, Cognito-gated (same as `/admin/activity`)
- **UI:** URL text input, category dropdown (gov_publications, faq_pages, news_pages, etc. — drives framework_id + authority_level), optional title override, "Ingest" button with progress/status display
- **Hybrid backend — Lambda for single docs, Fargate for bulk:**
  - **1-3 URLs** → Single Lambda handles all 4 phases inline (scrape → S3, extract+classify, embed, load to Neptune). ~2-3 min wall clock, well within Lambda 15-min limit. Stream status updates back over WebSocket (reuse existing infra).
  - **4+ URLs** → Lambda does scrape → S3 for all docs, then kicks off a Fargate task with `--source-filter` targeting the batch. Frontend polls ECS task status or tails CloudWatch for progress.
- **Status tracking:** WebSocket updates for Lambda path (reuse existing connection infra); ECS DescribeTask polling for Fargate path. Optionally a DynamoDB "ingestion jobs" table with status field for persistence across refreshes.
- **Validation:** Before ingesting, HEAD the URL to verify it's reachable and check content-type (PDF vs HTML). Show file size and last-modified to the user for confirmation.

**Key considerations:**

- The extract/embed/load code is pure Python with boto3 — runs fine in a Lambda with the existing layer. Only risk is very large PDFs (500+ pages) potentially exceeding Lambda memory/timeout — these should route to Fargate.
- Need to handle the `source_to_framework` mapping at upload time — the category dropdown populates metadata (framework_id, authority_level, doc_type) that the scraper currently hardcodes.
- The `doc_id` derivation (currently `make_doc_id()` in `scrape_documents.py`) needs to be reusable from the Lambda.
- Existing `scrape_documents.py` logic (download, HTML scraping, metadata generation, S3 upload) should be extracted into a shared module rather than duplicated.

**Key files:**
- `frontend/src/app/admin/ingest/page.tsx` — new admin page (to create)
- `backend/lambdas/agentic_retrieval/` — pattern reference for Lambda + WebSocket streaming
- `tools/graphrag/scrape_documents.py` — scrape/upload logic to extract into shared module
- `tools/graphrag/extract.py`, `embed.py`, `load.py` — pipeline steps to invoke from Lambda
- `infra/stacks/ingestion-stack.ts` — existing Fargate infra to reuse for bulk path
- `infra/stacks/sessions-stack.ts` — HTTP API routes (add `POST /admin/ingest`)

---

### Task 27: Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags

**Problem:** WPAM 2026 chunks have solid chapter-level headings (19 distinct chapters, clean "Chapter N Title" format) but subheadings are mostly empty. Only a handful of chunks have useful subheading metadata (e.g., "GRM", "3. Estimate accrued depreciation."). This limits the agent's ability to navigate within a chapter — it can retrieve all 7-15 chunks in a chapter but can't identify which section within that chapter is relevant without reading all of them.

**Root Cause:** The WPAM chunker (`chunk_document_wpam` in `pdfChunker.py`) detects section headers using only regex patterns:
- `^[A-Z]{2,}(?:\s+[A-Z]{2,}){0,5}\s*$` — ALL-CAPS (2-6 tokens)
- `^[A-Z]\.\s+[A-Z][A-Za-z]` — letter-prefix ("A. Title")
- `^\d+\.\s+[A-Z][A-Za-z]` — number-prefix ("1. Methodology")
- `^[IVX]+\.\s+[A-Z][A-Za-z]` — roman-numeral prefix

These miss the majority of real WPAM subheadings, which are bold/larger-font title-case lines without a letter/number prefix (e.g., "Sales Comparison Approach", "Gross Rent Multiplier", "Cost Approach Overview").

**The signal already exists but is discarded:** `pymupdf_extractor.py` classifies lines as `title`/`header`/`body` using font metrics (bold + size > body_size * 1.05). It wraps detected headers in `<header>` XML tags. However, the WPAM chunker's main loop calls `clean_line()` which strips all XML tags *before* checking for section headers. The font-based classification is never used.

**Compounding factor:** The `repair_wpam_subheadings` post-pass (`wpam_chunk_filter.py`) clears any subheading appearing on >5 chunks. This correctly removes leaked numbered-list items (e.g., "3. Estimate accrued depreciation" carried forward across 10+ chunks), but it means the few subheadings that *do* survive the regex are sometimes cleared too.

**Proposed Fix: Wire up `<header>` tags as subheading signal**

In the main chunk loop (`pdfChunker.py` ~line 696), before calling `clean_line()`, check if the segment contains a `<header>` tag. If so, and it passes length/word guards, treat it as a section header:

```python
for raw_line, page in line_page_mapping:
    for segment in sub_header_content_splitter(raw_line):
        # NEW: detect font-based headers before stripping tags
        is_font_header = "<header>" in segment
        line = clean_line(segment)
        if not line:
            continue

        # ... existing leader-dot and chapter-heading checks ...

        # Use font tag OR regex to detect section headers
        if _looks_like_section_header(line) or (
            is_font_header and len(line) < 60 and len(line.split()) < 6
        ):
            flush_chunk(buffer, current_chapter, current_section)
            current_section, buffer = line, []
            continue
```

**Guards to prevent false positives:**
1. Line must be < 60 chars and < 6 words (avoids bold sentences/paragraphs)
2. The existing `repair_wpam_subheadings` pass remains as a safety net (clears anything appearing >5 times)
3. Could add: reject if line ends with common sentence-ending patterns (period followed by lowercase continuation on next line)

**Validation plan:**
1. Run chunker locally on WPAM 2026 PDF with the fix applied
2. Compare subheading coverage: before (sparse) vs. after (should populate most chunks)
3. Spot-check for false positives: bold sentences that shouldn't be subheadings
4. If quality is good, reingest WPAM 2026 to Neptune and verify with the heading query

**Effort:** Low — ~15 lines of code change in the main loop. No new dependencies, no infra changes. Requires reingestion of WPAM documents afterward.

**Key files:**
- `tools/ingestion/chunking/pdfChunker.py` — `chunk_document_wpam()` main loop (~line 696)
- `tools/ingestion/chunking/pymupdf_extractor.py` — `_classify_line()` (the font-based classifier, already working)
- `tools/ingestion/chunking/wpam_chunk_filter.py` — `repair_wpam_subheadings()` (safety net, keep as-is)

### Task 28: WPAM 2019 heading loss — boilerplate stripper keeps TOC copy, strips real chapter start

**Problem:** The WPAM 2019 edition only gets 6 chapter headings detected during chunking (should be 22+). All content from chapters 6-22 gets lumped under "Chapter 5 Public Relations in the Assessment Office" because the chunker never sees a heading transition.

**Root cause:** `_strip_wpam_running_headers()` in `boilerplate.py` keeps the *first occurrence* of each repeated header and strips the rest. In 2019, the first occurrence of "Chapter 7 Parcel and Information Systems" (and 14 other headers) is on pages 4-7 — in the **Table of Contents**. The TOC uses the exact same string as the running headers (no trailing dots, same casing). So the stripper keeps the TOC copy and strips the real chapter-start at page 139. The TOC chunk is then discarded by `is_probably_toc()`, leaving no heading line for the chunker to split on.

**Why only 2019:** Earlier editions (2012-2018) have TOC lines with trailing dots or different casing (e.g., "Chapter 7 Real property valuation .......") which don't match the running header string. The stripper's first occurrence is the actual chapter start, not the TOC. Tested: 2012, 2015, 2017 all produce 22-25 chapter headings correctly.

**Scope:** Isolated to WPAM 2019 only. Possibly also 2020+ if their TOC format matches (needs verification — 2020-2026 were already reingested with correct results, so they may be fine).

**Possible fixes (needs careful evaluation):**

1. **Keep first occurrence after page N** — Change stripper to keep the first occurrence that appears after page 10 (skip TOC area). Risk: some legitimate first chapters start on page 3-4.
2. **Keep first AND second occurrence** — Keep first two, let the chunker's `is_probably_toc()` handle discarding the TOC one. Risk: if the TOC line isn't in a toc-like context, it creates a spurious chunk.
3. **TOC-aware stripping** — Detect the TOC region first (pages with many chapter-title lines clustered together), mark those as TOC, then keep-first only within non-TOC pages. More robust but more complex.
4. **Skip 2019 entirely** — If 2020 covers the same content, 2019 may not add retrieval value. Simplest but loses historical edition coverage.

**Safe to reingest:** 2012-2018 editions all produce correct chapter headings (22-25 detected) with the existing chunker. They can be reingested without any code changes.

**Caution:** The "keep first" logic was intentional — changing it could reintroduce running headers into chunks for other editions if not carefully scoped.

**Key files:**
- `tools/ingestion/chunking/boilerplate.py` — `_strip_wpam_running_headers()` (lines 76-107)
- `tools/ingestion/chunking/pdfChunker.py` — `chunk_document_wpam()` heading detection loop

---

---

### Task 32: Show trimmed section page index in answer synthesis trace card

**Problem:** The retrieval trace modal shows tool calls, chunks, and the answer plan — but doesn't expose the section page index that's passed to the answer model during `prepare_answer`. This index is critical for citation accuracy (maps section numbers to correct PDF pages) and was recently fixed to use the statute chunker's canonical heading pattern + chunk-text intersection filtering. Making it visible in the trace helps verify citation correctness without digging through CloudWatch logs.

**What to show:** The trimmed section page index for each statute doc in `cited_doc_ids`. Example:
```
statutes-70:
  § 70.04 → page 2
  § 70.17 → page 19
  § 70.27 → page 22
  § 70.32 → page 23
```

**Where to show it:** In the answer synthesis card (the `prepare_answer` tool result in the pipeline retrieval modal). This card already shows `cited_doc_ids` and `answer_plan` — the section index is the missing third piece.

**Implementation:**

1. **Backend** — In `_build_answer_context()` (main.py ~line 1175), after building `index_lines`, include them in the `prepare_answer` tool result that gets sent to the trace WebSocket message. Currently the tool result only sends `cited_doc_count`, `cited_doc_ids`, `has_plan` — add a `section_page_index` field (dict of doc_id → list of index lines).

2. **Frontend** — In the retrieval modal's answer synthesis card (`retrieval-modal.tsx`), render the section index below the answer plan. Use a collapsible section or compact list since it's typically 3-15 entries per statute doc.

3. **WebSocket schema** — Add `section_page_index` to the trace message Zod schema (optional field, only present when statute docs are cited).

**Key files:**
- `backend/lambdas/agentic_retrieval/main.py` — `_build_answer_context()`, trace emission near line 926
- `frontend/src/components/messages/retrieval-modal.tsx` — answer synthesis card rendering
- `frontend/types/message-types.ts` — Zod schema for trace messages
- `backend/layers/websocket_utils/models.py` — WebSocket message models

---

### Task 35: Graph wiring overhaul — stubs as routing nodes, not dead ends

**Problem:** The load phase has three structural issues that compound into noisy, imprecise traversals:

1. **Phase 3 (doc-level CITES)** creates edges from entire documents to statute stubs based on LLM classification (first 4K chars only). This is redundant with the deterministic chunk-level CITES from Phase 8, unreliable (LLM misses refs past page 2), and the primary cause of hub bloat — statute stubs like `WIS-STAT-70.32` accumulate dozens of incoming edges from whole documents, making `get_neighbors` return huge lists of document-level pointers with no indication of which passage is relevant.

2. **Phase 5 (topic merging)** burns an LLM call every ingestion to cluster topics into canonical names and wire `COVERS_TOPIC` edges. The retrieval agent never traverses these edges — confirmed zero references in `main.py`. Dead graph weight + wasted Bedrock cost.

3. **Phase 9 (stub resolution)** tries to collapse section stubs (`WIS-STAT-70.32`) into chapter documents (`statutes-wi-statute-ch70`). This loses section-level granularity — everything becomes "cites chapter 70." Meanwhile the stub stays as a dangling node anyway.

**Current traversal (broken):**
```
wpam_chunk -[CITES]→ WIS-STAT-70.32 (dead end, no content)
wpam_doc   -[CITES]→ WIS-STAT-70.32 (redundant, bloats hub)
                      WIS-STAT-70.32 -[PART_OF]→ CH-70
statutes-wi-statute-ch70_chunk_0042 (has the § 70.32 text, disconnected from stub)
```

**Desired traversal:**
```
wpam_chunk -[CITES]→ WIS-STAT-70.32 -[DEFINED_BY]→ statutes-wi-statute-ch70_chunk_0042
```

Stubs become bidirectional routing nodes:
- **Inbound CITES** (chunk-level only) = "which passages cite this section"
- **Outbound DEFINED_BY** = "what does this section actually say" (the statute chunk with matching heading)

**Changes to load.py:**

| Phase | Action |
|-------|--------|
| Phase 3 | **Remove entirely.** Doc-level CITES/IMPLEMENTS edges are redundant with chunk-level (Phase 8) and cause hub bloat. The `statute_refs`/`implements_refs` fields from LLM classification are no longer consumed. |
| Phase 5 | **Remove entirely.** Topic nodes and `COVERS_TOPIC` edges are never queried at retrieval time. |
| Phase 9 | **Rewrite.** Instead of re-pointing edges to chapter docs, wire `(stub)-[DEFINED_BY]->(chunk)` by matching stub ID `WIS-STAT-{section}` to statute chunks whose `heading` starts with `{section}`. Multiple chunks can define one section (long sections get split). |

**Changes to retrieval (get_neighbors):**

Add transparent stub traversal: when `get_neighbors` encounters a stub with a `DEFINED_BY` edge, auto-follow it and return the target chunk instead of the stub. One extra deterministic hop, invisible to the agent. Cypher:
```cypher
MATCH (n {id: $id})-[r]-(neighbor)
OPTIONAL MATCH (neighbor)-[:DEFINED_BY]->(resolved)
  WHERE neighbor.stub = true
RETURN CASE WHEN resolved IS NOT NULL THEN resolved ELSE neighbor END
```

**Phase 9 matching logic:**

Statute chunks already have `heading` metadata like `"70.32 Real estate, how valued."` from `chunk_document_statute()`. For each stub `WIS-STAT-{section}`:
1. Extract section number from stub ID (e.g., `70.32`)
2. Derive chapter number (e.g., `70`)
3. Find chunks from `statutes-wi-statute-ch{chapter}` where `heading` starts with `{section}`
4. Wire `(stub)-[DEFINED_BY]->(chunk)` for each match

**For stubs with NO matching chunks:** These reference statute sections we haven't ingested or sections that didn't get their own chunk (rare subsections like `70.32(2)(a)6`). Leave as stubs — they still serve as citation join points even without content. Optionally add `source_url` pointing to the legislature PDF.

**Side effects / cleanup:**
- `doc.statute_refs` and `doc.implements_refs` from LLM classification are no longer consumed by load. They remain in the extracted JSON (cached classification) but create no edges.
- `doc.topics` from LLM classification is no longer consumed. Topic nodes and edges stop being created.
- The `_build_answer_context()` temp fix in `main.py` (scanning for statute chapter refs) can be removed once stubs route to real content via `DEFINED_BY`.
- Classification caching (already implemented) means the now-unused LLM fields don't cost anything on re-runs — they're just ignored.

**Key files:**
- `tools/graphrag/load.py` — remove Phase 3, remove Phase 5, rewrite Phase 9
- `backend/lambdas/agentic_retrieval/neptune_client.py` — update `get_neighbors` Cypher for transparent stub traversal
- `backend/lambdas/agentic_retrieval/main.py` — remove `_build_answer_context()` statute ref scanning (after verification)

---

### Task 36: Full corpus refresh — scrape, ingest missing docs, reingest stale content

**Status:** Tooling complete (scraper with hash-based change detection, `--smart` extract mode). Ready to execute.

**Source of truth:** `tools/graphrag/document_manifest.yaml` — 198 documents across all categories.

**What's built:**
- `scrape_documents.py` — reads manifest, MD5/ETag change detection, `--dry-run`, `--force`, `--category` flags
- `extract.py --smart` — only re-extracts docs whose raw S3 file is newer than the extraction cache
- `run_fargate.sh --smart` / `entrypoint.sh` — Fargate support for smart mode

**What's left to ingest:**

| Gap | Category | Count | Notes |
|-----|----------|-------|-------|
| 2026 news | news_pages | ~20 | Assessor + COTVC 2026 messages |
| Form instructions | form_instructions (NEW) | ~50 PDFs + 4 xlsx | Needs new framework in `ingest_config.yaml` (authority_level 6) |
| dolcre.pdf | faq_pages | 1 | Dollar Lottery Credit FAQ (PDF, not HTML) |
| IAAO standards | iaao | 5 | Download from iaao.org |
| Admin rules | admin_rules | 7 | Already in S3, just needs reingest with updated chunker |
| Gov pubs | gov_publications | 3 | pb062, pa600, pb061 — in S3, need extract/embed/load |

**Execution order:**
1. Admin rules reingest + 3 gov pubs (no downloads, just Fargate runs)
2. dolcre.pdf (single download + pipeline)
3. 2026 news pages (scraper + pipeline)
4. 5 IAAO standards (download + pipeline)
5. Form instructions (new framework setup, ~50 downloads, pipeline — largest effort)

**Annual refresh workflow (for future content updates):**
```bash
# Scrape all categories — only uploads docs with changed content (hash-gated)
python tools/graphrag/scrape_documents.py --manifest tools/graphrag/document_manifest.yaml

# Re-extract/embed/load only stale docs
./tools/graphrag/run_fargate.sh extract --smart
./tools/graphrag/run_fargate.sh embed
./tools/graphrag/run_fargate.sh load
```

**Key files:**
- `tools/graphrag/document_manifest.yaml` — single source of truth for all corpus URLs
- `tools/graphrag/scrape_documents.py` — hash-gated scraper
- `tools/graphrag/ingest_config.yaml` — framework definitions (needs `form_instructions` added)
- `tools/graphrag/run_fargate.sh` — pipeline execution with `--smart` support

---

### Task 38: Restructure tools/ directory — consolidate ingestion pipeline

**Problem:** `tools/` is a grab bag of pipeline code, chunking library, one-shot ops scripts, dead text files, and dev utilities all mixed together. `pdf_chunking/` is a sibling of `graphrag/` but only ever imported by `graphrag/extract.py`. The name "graphrag" is misleading — it's the full ingestion pipeline. Dead files (`docs_missing_source_url.txt`, `all_ingested_documents.txt`, `required_documents_links_only.txt`) linger. One-shot maintenance scripts sit alongside core pipeline code. Chunk log output dirs (`pdf_chunking/chunk_logs/`) leak into the repo root.

**Current state:**
```
tools/
├── __init__.py
├── bedrock_utils.py              # unclear if used
├── bundle.py                     # used by `bun run bundle`
├── upload_model_configs.py       # used regularly
├── simulate_chunking.py          # dev tool
├── pdf_chunking/                 # chunking library (only used by graphrag/extract.py)
│   ├── pdfChunker.py
│   ├── pymupdf_extractor.py
│   ├── boilerplate.py
│   ├── wpam_chunk_filter.py
│   ├── toc_detector.py
│   ├── table_tools.py
│   ├── flowchart_tools.py
│   └── aws_utils.py
└── graphrag/                     # pipeline + config + docker + ops + tests + dead files
    ├── extract.py, embed.py, load.py      # core pipeline
    ├── scrape_documents.py                # scraper
    ├── ingest_case_law.py                 # case law discovery
    ├── ingest_config.yaml, document_manifest.yaml  # config
    ├── Dockerfile, entrypoint.sh, build_and_push.sh, requirements.txt  # docker
    ├── run_fargate.sh, run_full_ingest.sh, sync_faq_bucket.sh  # shell scripts
    ├── case_annotations.py, faq_url_map.py, wpam_year.py  # shared helpers
    ├── clean_stale_extracts.py, cleanup_orphan_chunks.py, purge_orphan_chunks.py  # one-shot ops
    ├── add_case_law.py, seed_faq_url_table.py, extract_faq_qa_pairs.py, upload_local_docs.py  # one-shot / superseded
    ├── test_diversity.py                  # misplaced test
    ├── docs_missing_source_url.txt        # DEAD (superseded by manifest)
    ├── all_ingested_documents.txt         # DEAD (scratch output)
    ├── required_documents_links_only.txt  # DEAD (scratch output)
    └── tests/                             # test suite
```

**Proposed structure:**
```
tools/
├── bundle.py                        # keep (bun run bundle)
├── upload_model_configs.py          # keep (prompt iteration)
├── simulate_chunking.py            # keep (dev tool)
│
├── ingestion/                       # rename from graphrag/
│   ├── __init__.py
│   ├── scrape_documents.py          # manifest-driven scraper
│   ├── ingest_case_law.py           # case law discovery + upload
│   ├── extract.py                   # extract + classify
│   ├── embed.py                     # embed chunks
│   ├── load.py                      # load into Neptune
│   │
│   ├── chunking/                    # move pdf_chunking/ in as subpackage
│   │   ├── __init__.py
│   │   ├── chunker.py              # rename pdfChunker.py
│   │   ├── pymupdf_extractor.py
│   │   ├── boilerplate.py
│   │   ├── wpam_chunk_filter.py
│   │   ├── toc_detector.py
│   │   ├── table_tools.py
│   │   ├── flowchart_tools.py
│   │   └── aws_utils.py
│   │
│   ├── config/
│   │   ├── ingest_config.yaml
│   │   └── document_manifest.yaml
│   │
│   ├── lib/                         # shared helpers
│   │   ├── case_annotations.py
│   │   ├── faq_url_map.py
│   │   └── wpam_year.py
│   │
│   ├── ops/                         # one-shot / maintenance scripts
│   │   ├── clean_stale_extracts.py
│   │   ├── cleanup_orphan_chunks.py
│   │   └── purge_orphan_chunks.py
│   │
│   ├── docker/
│   │   ├── Dockerfile
│   │   ├── .dockerignore
│   │   ├── entrypoint.sh
│   │   ├── build_and_push.sh
│   │   └── requirements.txt
│   │
│   ├── scripts/                     # shell wrappers
│   │   ├── run_fargate.sh
│   │   ├── run_full_ingest.sh
│   │   └── sync_faq_bucket.sh
│   │
│   └── tests/
│       └── ... (existing tests)
│
└── (delete)
    ├── bedrock_utils.py                          # verify unused, then delete
    ├── graphrag/docs_missing_source_url.txt      # superseded by manifest
    ├── graphrag/all_ingested_documents.txt       # scratch output
    ├── graphrag/required_documents_links_only.txt # scratch output
    ├── graphrag/test_diversity.py                # move to tests/ or delete
    ├── graphrag/add_case_law.py                  # superseded by ingest_case_law.py
    ├── graphrag/seed_faq_url_table.py            # one-shot, FAQ KB seeded
    ├── graphrag/extract_faq_qa_pairs.py          # one-shot, FAQ KB populated
    └── graphrag/upload_local_docs.py             # superseded by scrape_documents.py
```

**Key changes:**
1. **Rename `graphrag/` → `ingestion/`** — it's the ingestion pipeline, not GraphRAG-specific
2. **Move `pdf_chunking/` inside** as `ingestion/chunking/` — only consumer is `extract.py`
3. **Separate ops scripts** from core pipeline into `ops/` subdir
4. **Group config, docker, shell scripts** into their own subdirs
5. **Delete dead files** — superseded txt files, one-shot scripts that already ran
6. **Rename `pdfChunker.py` → `chunker.py`** — follow Python naming conventions

**Import path changes required:**
- `from pdf_chunking.pdfChunker import ...` → `from tools.ingestion.chunking.chunker import ...`
- `from tools.graphrag.extract import ...` → `from tools.ingestion.extract import ...`
- `from tools.graphrag.wpam_year import ...` → `from tools.ingestion.lib.wpam_year import ...`
- Dockerfile COPY paths, CLAUDE.md references, shell script paths

**Risk:** Docker build references, Fargate entrypoint, and `python -m tools.graphrag.extract` module invocation all need updating. Test in Docker build before merging.

**Effort:** Medium — mostly mechanical find-and-replace on import paths, but needs careful Docker build verification.

---

### Task 39: Discover and ingest 2026 news pages

**Problem:** The DOR sitemap doesn't include 2026 news URLs. The assessor and COTVC landing pages (`assessor-messages-home.aspx`, `cotvc-messages-home.aspx` without `?PubYear` param) are SharePoint pages that render content dynamically via JS — static scraping returns zero links.

**Options:**

1. **Headless browser (Playwright)** — Use Playwright to render the landing pages, wait for JS to populate the link list, then extract individual news page URLs. Pros: works regardless of the underlying CMS implementation. Cons: adds a heavyweight dependency (Chromium binary), fragile to DOM structure changes, slower.

2. **SharePoint REST API** — Hit the SharePoint list API directly to enumerate news items. The pages likely back onto a SharePoint list with columns for title, publish date, and URL. Pros: reliable, fast, returns structured data. Cons: requires discovering the correct list GUID and API endpoint, may need auth tokens.

3. **Ask DOR to update their sitemap** — Request that DOR include 2026 news pages in their XML sitemap or provide a machine-readable feed (RSS/Atom). Pros: zero maintenance on our side, canonical source. Cons: depends on DOR staff action and timeline.

**Once URLs are discovered:** Add them to `tools/graphrag/document_manifest.yaml` under the `news_pages` category and run the normal scrape pipeline (`scrape_documents.py` → extract → embed → load).

**Key files:**
- `tools/graphrag/document_manifest.yaml` — add discovered URLs here
- `tools/graphrag/scrape_documents.py` — existing scrape pipeline handles the rest
- `tools/graphrag/ingest_config.yaml` — `news_pages` framework already defined

---

### Task 40: Harden inline linking prose — quote verbatim instead of paraphrasing

**Problem:** The agent sometimes paraphrases content when generating inline link prose (the text that appears before a citation like "According to § 70.04 [source]"). This is problematic because:
- Paraphrased text may not match the actual source text, making it impossible for users to verify the citation
- Users can't search for the exact quoted text in the original document
- It undermines trust in the citations — if the quoted text doesn't match the source, users question the entire answer

**Current behavior:** The agent extracts the relevant chunk text, then rewrites it in its own words before appending the citation. Example:
```
Current: "The property owner must file an appeal within 30 days of receiving the notice [source]."
Source text: "The owner of the property shall file an appeal within 30 days after receiving the notice."
```

**Desired behavior:** Quote the exact text from the source document verbatim:
```
Desired: "The owner of the property shall file an appeal within 30 days after receiving the notice [source]."
```

**Root cause:** The agent's prompt encourages "natural language" responses, which leads the LLM to paraphrase. The citation extraction happens separately from the prose generation.

**Proposed solution:** Modify the inline link generation step to extract and quote the exact text from the retrieved chunks:
1. When the agent identifies relevant chunks, extract the exact text spans that support each claim
2. Pass these exact text spans (not paraphrased versions) to the answer generation model
3. The model then weaves the quoted text into its response, with citations pointing to the original source positions

**Implementation approach:**
- In `backend/lambdas/agentic_retrieval/main.py`, modify the answer synthesis step to preserve exact text spans from chunks
- Update the prompt to instruct the model to "quote the exact text from sources" rather than "paraphrase"
- Adjust citation formatting to include the quoted text with inline markers (e.g., `"exact quote" [source]`)

**Key files:**
- `backend/lambdas/agentic_retrieval/main.py` — answer synthesis logic
- `config/model_configs.toml` — system prompt for answer generation
- `backend/layers/websocket_utils/models.py` — if citation format needs updating

**Validation:** Compare paraphrased vs. verbatim responses for the same query, measure user satisfaction, verify citations match source text exactly.

**Effort:** Medium — requires changing the answer generation pipeline and prompt instructions.

