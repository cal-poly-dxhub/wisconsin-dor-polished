# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 5 | Replace LLM classification with structural parsers | — |
| 17 | Handle multipart queries (split or unified answering strategy) | — |
| 20 | Add user persona setting (government worker vs. citizen) | — |
| 21 | Investigate z-score normalization for vector_search result filtering | — |
| 22 | Apply over-fetch multiplier when target_wpam_year is set | — |
| 26 | Admin ingestion page — ingest documents via URL from the UI | — |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags | — |
| 28 | WPAM 2019 heading loss — boilerplate stripper keeps TOC copy, strips real chapter start | — |
| 29 | Enable prompt caching for agentic retrieval (switch to invoke_model) | — |
| 30 | get_section chunk grid visualization — show cosine/z-score per chunk in trace UI | — |

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

### Task 21: Investigate z-score normalization for vector_search result filtering

**Status: Investigated 2026-06-25 — not viable for primary vector_search; viable for `search_document` only.**

**Original Idea:** Use z-score normalization (keep chunks with z >= 0) as an adaptive quality floor after Neptune returns results. Instead of a static top-N cutoff, dynamically shrink the result set based on score distribution — tight clusters yield fewer results, broad distributions retain more.

---

#### Why z-score doesn't work after vector_search + dedup

The pipeline after WPAM dedup produces a **heterogeneous result set** mixing chunks from statutes, WPAM, guides, FAQs, news pages, and admin rules. These source types have fundamentally different embedding characteristics, which means their scores form **multiple overlapping distributions, not one**. Z-score assumes a single population where below-mean = noise — but in practice, below-mean = authoritative sources with different linguistic patterns.

**The core problem: embedding similarity ≠ answer quality.** The embedding model (Titan Embed v2) produces vectors that are closer when text shares vocabulary, phrasing patterns, and topical density with the query. A guide that says "the levy limit restricts how much a municipality can increase its property tax levy..." scores high because it *sounds like the question* — same words, explanatory register, repeated keywords. A statute that says "No political subdivision may increase its levy by a percentage that exceeds..." scores lower because it's terse legal language with formal phrasing, no repetition, and fewer shared surface tokens. The statute *is the authoritative answer* but doesn't resemble the question linguistically.

After dedup removes WPAM duplicates, what remains is:
- A cluster of high-scoring dense-prose chunks (guides, WPAM, news pages — verbose, keyword-rich)
- Scattered lower-scoring but highly authoritative sources (statutes, admin rules, FAQs — terse, formal)

The mean gets pulled up by the dense-prose cluster, and z-score cuts the authoritative sources.

---

#### Empirical evidence (2026-06-25, live Neptune queries)

**Query: "what is a levy limit?"** — 115 chunks post-dedup, mean=1.26, std=0.22

| Source | Score | Z-score | Verdict |
|--------|-------|---------|---------|
| `statutes-66 §66.0602` (the actual levy limit law) | 0.83 | -1.95 | **CUT** |
| `faq_pages-slf-levy` (FAQ page literally about levy limits) | 0.60 | -3.01 | **CUT** |
| 72 news/guide chunks mentioning levies tangentially | 1.26+ | ≥ 0 | kept |

Z-score would remove the primary statute AND the dedicated FAQ page, returning only tangential mentions from guides and news posts.

**Query: "Does my 501c3 property qualify for exemption?"** — 21 chunks, mean=1.26, std=0.046, spread=0.14

| Source | Score | Z-score | Verdict |
|--------|-------|---------|---------|
| `statutes-70 §70.11` (the property tax exemption statute) | 1.24 | -0.62 | **CUT** |
| `faq_pages-slf-taxempt` (tax exemption FAQ) | 1.26 | -0.04 | **CUT** (barely) |
| IAAO policy papers, property owner guides | 1.28-1.32 | ≥ 0 | kept |

The total spread is only 0.14 — scores form a continuous slope with no natural gap. The z=0 cut point is arbitrary and happens to land just above the most important results.

**Query: "How much of a building needs to be used for manufacturing..."** — 17 chunks, mean=1.17, std=0.11

| Source | Score | Z-score | Verdict |
|--------|-------|---------|---------|
| `statutes-70 §70.995` (manufacturing assessment statute) | 0.97 | -1.86 | **CUT** |
| `gov_publications manufacturing classification` (directly relevant) | 1.08 | -0.87 | **CUT** |
| Other statute sections and guide chunks | 1.17+ | ≥ 0 | kept |

Would remove 6 of 15 chunks including the primary statute.

---

#### Why the current pipeline already handles this correctly

The existing pipeline — dedup → diversity cap (5/doc) → truncate(top_k) → authority tiebreak — solves the problem differently and better:

- **Diversity cap** ensures no single dense-prose source monopolizes all 15 slots
- **Authority tiebreak** promotes statutes over guides when scores are within 0.03 of each other
- **Over-fetch (6x)** gives these filters enough runway to surface authoritative minority sources

Z-score would run *before* diversity/authority and eliminate the very sources those later stages exist to promote.

---

#### Where z-score IS viable: `search_document`

When searching **within a single document** (e.g., "find the section about agricultural classification in WPAM"), all chunks share the same writing style, vocabulary density, and embedding characteristics. Score differences within one document genuinely reflect relevance differences — it's one distribution, not five overlapping ones.

`search_document` fetches 800 chunks globally, filters to the target doc (often 30-100 matches for WPAM), then returns top_k. Z-score here could separate "this chapter answers your question" from "this chapter mentions a keyword in passing." The garbled table chunks from Task 25 would also tend to cluster below-mean within their own document.

**Other potential locations:**
- **FAQ search** — signal "no confident match" when all scores are low and flat
- **Case law discovery** — separate on-point cases from tangential citations of the same broad statute

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — `search_document` (line 857), `faq_search` (line 534)
- `backend/lambdas/agentic_retrieval/neptune_client.py` — raw scores returned from Neptune

**Priority:** Low. The primary retrieval path doesn't benefit. `search_document` and FAQ are secondary tools that the agent calls less frequently. Revisit if garbled chunk filtering (Task 25) proves insufficient at the data layer.

---

### Task 22: Apply over-fetch multiplier when target_wpam_year is set

**Problem:** When `target_wpam_year` is set, `vector_search` uses `fetch_k = top_k` (no multiplier). The intent is that the user explicitly wants WPAM content so diversity isn't needed. However, Neptune's vector index still returns chunks from *all* WPAM editions — if the user asks for 15 results and 10 of those are from other edition years, dedup removes them and only ~5 results survive. This under-fetching degrades answer quality for year-targeted queries.

**Why it works today (sometimes):** Year-targeted queries tend to be narrow enough (specific chapter/section) that the target year's chunks rank high in similarity. But for broader WPAM queries scoped to a year (e.g., "WPAM 2023 guidance on agricultural land"), the other editions' chunks are semantically near-identical and dominate the top results.

**Direction:** Apply a smaller over-fetch multiplier (e.g., 2x) when `target_wpam_year` is set, instead of no multiplier at all. The 6x multiplier is designed to surface non-WPAM diversity — that's not needed here — but a 2x buffer gives dedup enough runway to filter other-year editions without leaving the result set too thin.

```python
# Current (tools.py:630):
fetch_k = top_k * 6 if target_year is None else top_k

# Proposed:
fetch_k = top_k * 6 if target_year is None else top_k * 2
```

**Validation:** Run a few year-targeted queries and compare chunk counts pre/post dedup with and without the multiplier. If post-dedup count consistently stays near `top_k`, no multiplier is needed. If it drops significantly below, the 2x buffer is justified.

**Key file:** `backend/lambdas/agentic_retrieval/tools.py` line 630

---

---

## Flawed Responses

### Response A

**Query:** What Information is Used to Determine my assessment?

**Issue:** Cites manufacturing manual when question didn't specifically ask about manufacturing.

<details>
<summary>Full response</summary>

> What Information Is Used to Determine Your Assessment?
>
> The information used depends on whether your property is real estate or involves a general assessment cycle. Here's what the documents say:
>
> **Approaches to Value** — According to the Guide to Wisconsin Manufacturing Property Assessment – 2026, appraisers consider one or more of the following approaches:
> - Sales Comparison Approach
> - Cost Approach
> - Income Approach
>
> **Current Market Activity** — The Guide to Wisconsin Manufacturing Property Assessment – 2026 also notes current market activity is considered (improved/vacant land sales, listings, lease rates, vacancy rates, economic changes, lending practices, new construction).
>
> **Property Record Data** — The 2025 Wisconsin Property Assessment Manual outlines data sources (physical view, public area view, owner-requested data, other info such as listings/permits).
>
> **Sales Ratio Studies and Market Analysis** — assessors analyze sales through assessment/sales ratio studies, monitor zoning changes, building permits, and land use factors.

</details>

---

### Response B

**Query:** Is the expansion of a non-profit hospital that is under construction on the assessment date qualify for an exemption from property tax?

**Issue:** On 06/21, the system failed to discover and cite *Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa* (2025 WI App 43) — the most directly on-point case — despite it existing in the graph with a CITES edge to WIS-STAT-70.11. The 06/18 run of the same query correctly found and cited it, producing a substantially better answer with the case's specific facts and holding.

<details>
<summary>06/21 response (missing Children's Hospital)</summary>

> # Nonprofit Hospital Expansion Under Construction: Property Tax Exemption
>
> ## Short Answer
>
> **Generally, no.** A hospital expansion that is still under construction on the January 1 assessment date typically does **not** qualify for the property tax exemption under Wis. Stat. § 70.11(4m)(a), because the property is not yet being **"owned and used"** exclusively for hospital purposes on that date. However, the analysis is fact-specific and depends on the degree to which the property is actually being put to hospital use.
>
> [cites Covenant Healthcare (2011 WI 80), Saint Joseph's Hospital (276 Wis. 2d 574), but NOT Children's Hospital (2025 WI App 43)]

</details>

<details>
<summary>06/18 response (correctly cites Children's Hospital)</summary>

> # Non-Profit Hospital Expansion Under Construction: Property Tax Exemption
>
> ## Short Answer
>
> **Generally, no** — a non-profit hospital expansion that is only partially constructed on the January 1 assessment date does **not** qualify for the property tax exemption under Wis. Stat. § 70.11(4m). However, a fully constructed expansion that is in the **final stages of being readied** for hospital use (but not yet operational) **may** qualify under the "readying rule."
>
> ### The 2025 Court of Appeals Decision — Directly On Point
>
> In ***Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa*, 2025 WI App 43**, the Court of Appeals addressed precisely the question you are asking: whether a non-profit hospital's **expansion tower under construction** on the assessment date qualifies for the § 70.11(4m) exemption.
>
> **Facts:** Children's Hospital of Wisconsin (CHW) began constructing the "North Tower" addition to its existing hospital complex. On January 1, 2020 (the assessment date), the North Tower was only approximately **14% completed** (excavation, footings, foundation walls, some exterior walls). CHW argued the property was exempt because it was being "readied" for use as a nonprofit hospital.
>
> **Holding:** The Court of Appeals **affirmed** the City of Wauwatosa's denial of the exemption...

</details>

---

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
- `tools/pdf_chunking/pdfChunker.py` — `chunk_document_wpam()` main loop (~line 696)
- `tools/pdf_chunking/pymupdf_extractor.py` — `_classify_line()` (the font-based classifier, already working)
- `tools/pdf_chunking/wpam_chunk_filter.py` — `repair_wpam_subheadings()` (safety net, keep as-is)

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
- `tools/pdf_chunking/boilerplate.py` — `_strip_wpam_running_headers()` (lines 76-107)
- `tools/pdf_chunking/pdfChunker.py` — `chunk_document_wpam()` heading detection loop

---

### Task 29: Enable prompt caching for agentic retrieval (switch to invoke_model)

**Problem:** The agentic retrieval Lambda uses the Bedrock Converse API (`bedrock.converse()` / `bedrock.converse_stream()`), which does not support prompt caching. Each turn in the multi-turn tool loop resends the full message history — input tokens grow from ~8.5K (turn 1) to ~92K (turn 6). The system prompt (~4K tokens) and tool definitions (~4K tokens) are identical on every turn and every request, but are re-billed at full input price each time.

**Measured cost (single query, 6 turns):** 233K total input tokens, 0 cache hits. With caching, the stable prefix (tools + system + first user message ≈ 10K tokens) would be cached for turns 2-6, saving ~50K tokens of full-price input billing per query.

**Estimated savings:** ~$0.10/query at Sonnet pricing ($3/MTok input → $0.30/MTok cache read = 90% discount on cached prefix). At 100 queries/day = ~$300/month savings.

**Required change:** Switch from Converse API to `invoke_model` / `invoke_model_with_response_stream` with the raw Anthropic Messages API format, adding `cache_control: {"type": "ephemeral"}` breakpoints.

**Cache breakpoint placement (max 4):**

1. Last tool definition in the `tools` array — caches all tool schemas (stable across all requests)
2. System prompt text block — caches the system prompt (stable across all requests)
3. First user message (optional) — contains pre-seeded FAQ results (stable within a single query's multi-turn loop)

Render order for prefix matching: `tools` → `system` → `messages`. Breakpoints 1+2 create a shared cache across all queries (5-min TTL). Breakpoint 3 is per-query but saves across turns 2-6 within that query.

**Breaking changes requiring refactoring:**

| Area | Converse API (current) | Anthropic Messages format (target) |
|------|----------------------|-----------------------------------|
| Tool definitions | `{"toolSpec": {"name": ..., "inputSchema": {"json": {...}}}}` | `{"name": ..., "input_schema": {...}}` |
| Message content | `[{"text": "..."}]`, `[{"toolUse": {...}}]`, `[{"toolResult": {...}}]` | `[{"type": "text", "text": "..."}]`, `[{"type": "tool_use", ...}]`, `[{"type": "tool_result", ...}]` |
| Response parsing | `response["output"]["message"]`, `response["stopReason"]` | `response["content"]`, `response["stop_reason"]` |
| Usage fields | `response["usage"]["inputTokens"]` | `response["usage"]["input_tokens"]`, `response["usage"]["cache_read_input_tokens"]` |
| Streaming (Phase B) | `converse_stream()` event types | `invoke_model_with_response_stream()` SSE events: `message_start`, `content_block_delta`, `message_delta`, `message_stop` |

**Implementation approach:**

1. Create a helper module (`bedrock_messages.py`) that wraps `invoke_model` with:
   - Tool definition format conversion (toolSpec → Anthropic format)
   - Message format conversion (Converse → Messages)
   - Response parsing back to the dict shape the rest of `main.py` expects
   - Cache breakpoint injection on tools[-1] and system[0]
2. Replace `bedrock.converse()` call in the agent loop (line 530) with the wrapper
3. Replace `bedrock.converse_stream()` in Phase B (line 1186) with streaming wrapper
4. Add `cache_read_input_tokens` and `cache_creation_input_tokens` to usage logging

**Risk assessment:**
- No functional behavior change — same model, same prompts, same tools, same message content
- Streaming still works via `invoke_model_with_response_stream()` (SSE format, not Converse event format)
- Minimum cacheable prefix for Sonnet: 2048 tokens (our prefix is ~10K — well above)
- Cache TTL is 5 minutes; if traffic is sparse, cache misses increase (first request per 5-min window pays `cache_creation_input_tokens` at 25% premium, subsequent requests get 90% discount)
- The `additionalModelRequestFields` param on Converse API does NOT enable caching — confirmed not supported

**Validation:**
1. Deploy and run a test query
2. Check `usage` in response for `cache_read_input_tokens > 0` on turns 2+
3. Compare total billed input tokens before/after across a set of queries

**Effort:** Medium (half day). Mostly mechanical format conversion with a thin adapter layer. No infra/CDK changes needed.

**Key files:**
- `backend/lambdas/agentic_retrieval/main.py` — lines 530-536 (Phase A converse call), lines 1186-1191 (Phase B stream call)
- `backend/lambdas/agentic_retrieval/agent_tools.py` — `TOOL_DEFINITIONS` (line 112, needs format conversion)
- `backend/lambdas/agentic_retrieval/bedrock_messages.py` — new adapter module (to create)

---

### Task 30: get_section chunk grid visualization — show cosine/z-score per chunk in trace UI

**Problem:** When the agent calls `get_section`, the trace UI currently shows only the section heading, document name, chunk count, and latency. There's no visibility into *which* chunks were selected or how relevant each one was (cosine similarity, z-score). This makes it impossible to debug retrieval quality from the frontend — you have to dig through CloudWatch logs (which don't even log scores today).

**Two parts:**

#### Part A: Backend — emit per-chunk scores in the WebSocket trace payload

The `_rank_chunks_by_relevance()` function in `agent_tools.py` (line 594) computes cosine similarity and z-scores but doesn't log or emit them. The `get_section` tool result sent over WebSocket (as a trace event) needs to include per-chunk scoring data.

**Data to emit per chunk:**
- `chunk_index` (positional rank, 0-based, sorted by relevance)
- `chunk_id` (the Neptune chunk ID)
- `cosine` (raw cosine similarity score, 4 decimal places)
- `z_score` (normalized z-score, 2 decimal places)
- `heading` / `subheading` (for display context)
- `start_page` / `end_page`

Also emit:
- `query` — the query string used for ranking (from the tool input)
- `section_chunk_count` — total chunks in the section (before filtering)
- `returned_chunk_count` — how many passed the z-score threshold

**Where to emit:** The existing trace/logging WebSocket message for `get_section` tool results. The frontend already receives tool call trace events — extend the payload shape.

#### Part B: Frontend — 4x4 chunk relevance grid in the trace detail view

**Design (based on screenshot of current Get Section trace card):**

Below the existing metadata (heading, document name, chunk count, latency), add:

1. **Query label:** `Searched for "[query]"` — shows the semantic query used for ranking
2. **4x4 grid (16 cells):** Represents the chunk "slots" available
   - Chunks fill cells left-to-right, top-to-bottom, ranked by relevance (top-left = best match)
   - Maximum 10 filled cells (top_k max is 10); remaining cells stay empty/grey
   - Each filled cell shows:
     - **Title:** "Chunk 1", "Chunk 2", etc. (rank number)
     - **Cosine:** e.g., `0.8234`
     - **Z-score:** e.g., `1.42`
   - Color coding: higher z-score = more saturated/brighter fill (visual heat)
   - Empty cells: dark grey placeholder with dashed border
3. **Section total indicator:** "5 of 13 chunks" or similar, showing how selective the filter was

**Why 4x4:** Simulates the "enormity" of large sections — a 13-chunk section like WPAM Chapter 14 visually fills most of the grid, while a 1-chunk section (like the Ag Guide "C. Assessing other" heading) shows 1 filled cell and 15 empty, making it immediately obvious how much content was available vs. retrieved.

**WebSocket contract change:** This adds fields to the `get_section` trace event payload. Per project conventions, must update:
1. Backend — `websocket_utils/models.py` (add chunk score array to tool result trace model)
2. Frontend — `frontend/types/message-types.ts` (extend Zod schema for the trace event)
3. Frontend — trace detail component (render the grid)

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools.py` — `_rank_chunks_by_relevance()` (line 594), `execute_tool()` get_section handler
- `backend/layers/websocket_utils/models.py` — trace event model
- `backend/layers/websocket_utils/utils.py` — `send_json` trace emission
- `frontend/types/message-types.ts` — Zod schema for tool trace events
- `frontend/src/components/messages/` — trace detail rendering component (find the Get Section card)
- `frontend/src/components/messages/chat-message.tsx` — may contain or reference trace rendering

