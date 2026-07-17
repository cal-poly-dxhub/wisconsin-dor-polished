# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 5 | Replace LLM classification with structural parsers | — |
| 17 | Handle multipart queries (split or unified answering strategy) | — |
| 20 | Add user persona setting (government worker vs. citizen) | — |
| 21 | Add z-score normalization to search_document result filtering | — |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags | — |
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing | — |
| 41 | Fix statute citation page numbers, card titles, and chunker page-header pollution | — |
| 42 | Markarian hierarchy query fails to ground `statutes-70` (turn-budget exhaustion) | `8d5f49aa` |

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
| 39 | Discover and ingest 2026 news pages |
| 26 | Admin ingestion page — ingest documents via URL from the UI |

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

---

### Task 41: Fix statute citation page numbers, card titles, and chunker page-header pollution

**Origin:** Triaged from a live query — "How do you determine the tax increment for a tid" — whose citation card for `statutes-66` showed the wrong title ("66.0101 - Home Rule") and an inline citation to `p.161` that opened to § 66.1103 in the browser instead of § 66.1105.

**Root causes found (three distinct bugs):**

1. **Stale chunks in Neptune** — The June 23 load crashed during phase 1, so the July 4 re-extraction was never loaded. Worse, the load's `phase_purge_stale_chunks` only ran when `--source-filter` was set — a full load used `MERGE` which updates/creates chunk IDs but never deletes ones that no longer exist. `statutes-66` had 1171 chunks in Neptune but the current extraction only produces ~740. The 365 stale ghost chunks carried old (wrong) page numbers.

2. **Card title = document-level title** — `build_rag_documents()` in `rag_documents.py` grouped all chunks by `doc_id` and pulled the title from the Document node (`d.title`), which for a statute chapter is always the first section ("66.0101 - Home Rule"). Every cited section from that chapter collapsed into one card with that misleading title.

3. **Statute chunker page-header / TOC pollution** — The statute chunker's section regex matched bare section numbers appearing in running page headers (e.g., "66.1105" at the top of odd pages) and in the table of contents (pages 1-2). These spurious matches created chunks with incorrect `start_page` metadata and could poison the deterministic Section Page Index (mapping `§ 66.1105 → page 2` from a TOC entry).

**Fixes applied (all committed, main):**

- **`tools/ingestion/load.py`** — `phase_purge_stale_chunks` now runs unconditionally (not gated on `--source-filter`), so every load starts from a clean chunk slate. Also added S3-based caching for phase 9 (semantic edges) keyed on embedding fingerprints — subsequent loads skip ~$41/~40 min of Bedrock LLM calls when embeddings are unchanged.
- **`backend/lambdas/agentic_retrieval/rag_documents.py`** — citation cards now group by `(doc_id, heading)` and title each section card `Statute § {heading}`. Sections without headings (WPAM, FAQs, gov pubs) keep the existing doc-level behavior.
- **`tools/ingestion/chunking/pdfChunker.py`** — `chunk_document_statute` now skips bare section numbers at the top of a page (running headers) and all matches on pages 1-2 (TOC), so only real section headings trigger chunk boundaries. Verified: `§ 66.1105 → page 162` (correct), no TOC/header entries remain in the index.

**Also cleaned up:** removed the orphaned duplicate `gov_publications-tax-incremental-financing-manual` (same PDF as `gov_publications-tif-manual`, no longer in the manifest) from the raw bucket + extraction/embedding/classified caches.

**Deployment status:**
- Card title fix — **deployed** (`bun run bundle` + `cdk deploy`, 2026-07-08).
- Purge fix — **live**; a targeted load (phases 4-8) already ran and confirmed `statutes-66` back to correct 806→~740 chunks with right page numbers.

**Still needs to be done:**
- **Docker rebuild + full re-extract/embed/load** to apply the chunker page-header/TOC fix and phase 9 caching across the whole corpus. Blocked on a separate in-flight change swapping the phase 9 model from Sonnet to Haiku — batch the Docker rebuild so both go out together.
  - `cd tools/ingestion/docker && ./build_and_push.sh`
  - `run_fargate.sh extract --smart` → `embed` → `load`
- **Verify end-to-end** after re-load: re-ask the TID query, confirm the card title reads "Statute § 66.1105" and the inline citation opens to the correct PDF page.
- **Section Page Index** requires no code change — it reads chunk metadata from Neptune, so it self-corrects once the clean chunker data is loaded.

**Key files:**
- `tools/ingestion/load.py` — purge + phase 9 caching
- `backend/lambdas/agentic_retrieval/rag_documents.py` — card grouping/title
- `tools/ingestion/chunking/pdfChunker.py` — `chunk_document_statute` header/TOC filter
- `backend/lambdas/agentic_retrieval/loop/phase_b.py` — Section Page Index consumer (no change needed)

---

### Task 42: Markarian hierarchy query fails to ground `statutes-70` (turn-budget exhaustion)

**Origin:** Surfaced by the Direction-1 (auto-enrichment retarget) regression baseline
run on 2026-07-17. Golden-set query `8d5f49aa` — "What is the hierarchy of
assessment methods under Markarian?" (Stratum B, case-law two-hop control) — is the
one query of 15 that fails its baseline gate: `must_cite: [statutes-70]` is missing.

**Important:** This is a **pre-existing production weakness, NOT caused by the
auto-enrichment change.** It fails with enrichment fully live. None of the query's 6
cited docs came from the `auto-enrichment` path, so Direction 1 Option A does not make
it worse. It is logged here so it isn't mistaken for a Direction-1 regression during
the after-run comparison.

**What happens:** The agent finds both Markarian case-law nodes
(`case-law-45-wis-2d-683`, `case-law-173-n-w-2d-627`) and the WPAM / gov-pub guidance
that describes the assessment hierarchy, but never retrieves a chunk from `statutes-70`
(the § 70.32 assessment statute) to ground the rule in its statutory authority. The run
**exhausts all 10 turns** (`terminal_reason=turn_budget_exhausted`), which is the likely
proximate cause — it runs out of turns before tracing back to the statute. This is
exactly the "trace back from guides" requirement (system prompt Requirement 2) not
firing in time.

**Secondary observation (attribution blind spot):** The two case-law citations are
tagged `discovery="unknown"` because they enter `cited_doc_ids` via the internal
case-law discovery pipeline (`resolve_case_citations` / `get_cases_for_subsections`
inside `vector_search`), which appends to `related_case_law` but never writes to the
`discovery` map in `phase_a.py`. Consider tagging that path (e.g.
`"case-law-discovery"`) so cited-doc attribution is complete. This path is preserved
under Option A, so it is not itself at risk — but the `unknown` tag makes the
regression comparison less precise for case-law citations.

**Direction (needs investigation):**
- Why does this query need 10 turns? Check whether it loops on redundant
  vector_search / get_neighbors calls instead of tracing to `statutes-70` early.
- Consider whether the turn-budget warning (injected at turn 7) should more forcefully
  steer toward statute grounding when case law is already in hand.
- Tag the internal case-law discovery path in the `discovery` map (attribution fix).

**Key files:**
- `backend/lambdas/agentic_retrieval/loop/phase_a.py` — turn loop, discovery tagging
- `backend/lambdas/agentic_retrieval/agent_tools/executor.py` — `vector_search`
  internal case-law discovery blocks
- `config/model_configs.toml` — Requirement 2 (trace back from guides), turn-budget prompt
- `tools/ingestion/tests/graph_regression_queries.yaml` — `8d5f49aa` golden entry

