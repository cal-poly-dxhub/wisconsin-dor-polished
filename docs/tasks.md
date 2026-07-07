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
| 32 | Show trimmed section page index in answer synthesis trace card |
| 35 | Graph wiring overhaul — stubs as routing nodes, not dead ends |

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

