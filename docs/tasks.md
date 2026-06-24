# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 2 | Fixing linking issues | — |
| 5 | Replace LLM classification with structural parsers | — |
| 17 | Handle multipart queries (split or unified answering strategy) | — |
| 20 | Add user persona setting (government worker vs. citizen) | — |
| 21 | Investigate z-score normalization for vector_search result filtering | — |
| 22 | Apply over-fetch multiplier when target_wpam_year is set | — |
| 23 | Strip WPAM running headers from chunk text | — |
| 25 | Fix WPAM 2025 garbled table chunks and heading metadata | — |
| 26 | Admin ingestion page — ingest documents via URL from the UI | — |

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

---

## Task Details

### Task 2: Fixing linking issues

**Problem:** Multiple issues with how source links are generated and presented to users.

**Sub-issues:**

- [ ] **Link TTL** — Harden presigned URL expiration so links don't expire, or find an alternative solution so sources are always accessible to the user.
- [ ] **Page anchors** — Ensure all source documents can link to correct page numbers and anchors. Testers reported also an off-by-one issue where the link took them to the page after the relevant page. May need to reingest if chunk metadata (start_page/end_page) is sloppy. Additionally, inline citations point to the parent doc's first-chunk page rather than the specific section being cited — e.g., citing "Wis. Stat. § 70.47" links to page 1 of the statutes-70 PDF instead of the page where § 70.47 actually begins. This requires either per-subsection page metadata in Neptune or a smarter page lookup at link-resolution time.
- [ ] **Inline citations** — All sources cited as cards at the bottom of a response must also be mentioned as inline links within the answer text.
- [ ] **Remove redundant source list** — The chatbot should not additionally list sources as a line of text at the end of its response (the cards are sufficient).
- [ ] **Inline ↔ card reconciliation** — The model writes more inline `[Title](doc:id)` links than it puts in `cited_doc_ids`, so those links render in the answer but have no matching source card below. Need a post-filter to enforce the 1:1 invariant programmatically (the model drifts on this bookkeeping despite prompt instructions).
- [ ] **Statute link promotion** — When the agent describes a statutory rule (e.g., Wis. Stat. § 70.32) but only retrieved guide/publication chunks that paraphrase it, the inline citation links to the guide rather than the actual statute. The user sees "[Wis. Stat. § 70.32](doc:news_pages-...)" which is misleading. **Status: prompt approach deployed (step 5 in WORKFLOW), evaluating effectiveness before considering deterministic post-processing.**

  **Options:**
  1. **Post-filter (recommended)** — After the model finishes, regex-extract all `doc:ID` patterns from the answer body and union them into `cited_doc_ids` before sending resource cards. Deterministic, guarantees consistency regardless of model drift.
  2. **Prompt reinforcement** — Strengthen the existing system prompt instruction. Cheaper but unreliable — the model is already juggling complex instructions and will continue to drift on mechanical bookkeeping.

  **Direction question:** Should the filter *add missing IDs to `cited_doc_ids`* (so cards appear for everything linked inline) or *strip orphan inline links* (remove links that don't have cards)? Adding is better UX — the user sees a link, they should be able to click a card for it.

  **Key file:** `backend/lambdas/agentic_retrieval/main.py` — where `cited_doc_ids` is assembled after the tool loop completes.

**Root Cause Analysis (2026-06-23):**

The page anchor off-by-one and same-page-for-different-sections issues trace to a single root cause: **broken chunking strategy routing** in `pdfChunker.py`. The `CHUNKER_BY_SOURCE` dict had keys like `"state-laws"` and `"assessment-manual"` but the actual `source_id` values passed in were `"statutes-70"`, `"wpam-wisconsin-property-assessment-manual-2025"`, etc. — no match, so everything fell through to the `"general"` chunker.

- **WPAM off-by-one (page 618 vs 619):** The general chunker has no chapter-boundary awareness. A chunk straddling the Ch16→Ch17 boundary got `start_page=618` (the first page in the chunk), but Ch17 content actually starts on page 619. The dedicated `chunk_document_wpam` chunker flushes at `"Chapter N"` lines, which would produce a Ch17 chunk starting at page 619. Fix: route WPAMs to the wpam chunker via `source_id.startswith("wpam-")`.

- **Statutes same-page issue (§70.32 and §70.995 both → page 56):** The general chunker produced 2000-char windows with no section awareness. The model only retrieved chunks around pages 55-56 (relevant to "manufacturing") and never saw the §70.32 definition at page 23. It reused `#page=56` for both citations because that's all it had. The dedicated `chunk_document_statute` chunker splits each section into its own chunk with correct page metadata (§70.32 → page 23, §70.995 → page 55). Fix: route statutes/admin-rules to the statute chunker via prefix matching.

- **The inline link system already supports per-link page targeting** — `resolveHref` in `animated-markdown.tsx` parses `#page=N` from each inline link and overrides the base URL's page. The system prompt already instructs the model to use distinct `#page=N` per citation. The only broken link in the chain was chunk metadata accuracy.

**Fix applied:** Routing fix in `get_chunking_strategy()` + smarter `_is_chapter_heading()` for WPAM false-positive rejection. Requires full reingestion.

**Remaining after reingestion:**
- Statute chunker has ~2.4% false-split rate from cross-references (e.g., "70.995 shall be assessed..." mid-prose triggering a split). These create small decoy chunks with misleading headings but correct page numbers. They won't outrank real definitions in vector search for most queries — low priority.
- Statute PDF boilerplate ("Updated 23-24 Wis. Stats. Published and certified...") survives into chunk bodies (~55 occurrences per statute). Wastes tokens but doesn't affect page accuracy — low priority.

**Statute Link Promotion (2026-06-24, in progress):**

The agent frequently cites statutory rules (e.g., "Wis. Stat. § 70.32") with inline links pointing to a guide or news page that *describes* the statute rather than the statute document itself. This happens because the agent retrieves guide/publication chunks that paraphrase the statute, satisfying the "only cite docs you have chunks for" rule — but the user sees a statute-named link that opens a guide PDF, which is confusing.

**Observed examples:**
- Query "How are my property taxes determined?" → `[Wis. Stat. § 70.32](doc:statutes-70#page=23)` correct, but levy/credits citations link to `doc:gov_publications-2026-property-owners-guide#page=29`
- Query "Can the sale of a dark commercial property..." → `[Wis. Stat. § 70.32](doc:news_pages-assessor-news-2023-03-02)` — statute-named link pointing to a news advisory

**Current approach: prompt-level workflow step (step 5 in WORKFLOW section of `config/model_configs.toml`):**
> "If your vector_search results are dominated by guides or publications that describe statutory rules, call search_document on the relevant statute chapter to get the actual statute text + page number so you can cite the statute directly."

**Constraint:** The existing inline citation rule requires the agent to only link documents it has chunks for. This is correct and prevents hallucinated page numbers. The prompt approach tells the agent to *retrieve* the statute chunk before citing it.

**If prompt approach proves insufficient, deterministic post-processing options:**

1. **Post-loop statute link rewriting** — After `cite_documents`, regex-scan the answer for inline links where link text matches `Wis. Stat.` / `§ X.Y` but the `doc:` target is a non-statute doc. For each match, call `find_stub_promotion("WIS-STAT-{section}")` to resolve the statute chapter doc + page, then rewrite the href. Works for the DynamoDB-saved answer; for streaming, would need a correction event or the frontend doing client-side resolution.

2. **Frontend-side resolution** — When rendering a `doc:` link whose display text matches a statute pattern, check if a statute source card exists in the resource list and redirect the link target. Loses page-level specificity (card has a single page range, not per-citation pages).

3. **Hybrid: post-process saved copy only** — Rewrite links in the DynamoDB record and let the streamed version go as-is. History/admin views get correct links; live stream is slightly less precise but not broken.

**Streaming consideration:** If the answer is streamed token-by-token, post-processing is too late for the live experience. Options: (a) send a "correction" event after stream ends to replace the answer, (b) do it only on the saved copy, or (c) rely on the prompt approach for streaming and use post-processing only as a DB-level cleanup.

**Key files:**
- `config/model_configs.toml` — prompt step 5 (deployed 2026-06-24)
- `backend/lambdas/agentic_retrieval/main.py` — where `cite_documents` result is handled; insertion point for post-processing
- `backend/lambdas/agentic_retrieval/neptune_client.py` — `find_stub_promotion()` resolves stub → chapter doc + page

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

**Problem:** The `vector_search` tool currently uses a fixed top-N cutoff for chunk retrieval. A static threshold doesn't adapt to query quality — some queries produce a tight cluster of highly relevant results while others yield a flat distribution where the Nth result is barely better than noise. This can lead to either including irrelevant chunks (wasting context tokens and diluting precision) or excluding relevant ones that fall just outside the cutoff.

**Proposed Technique:** Z-score normalization of cosine similarity scores. Instead of "take the top N," compute the mean and standard deviation of all returned similarity scores, then keep only chunks whose z-score exceeds a threshold (e.g., z > 0). This dynamically adapts the number of results to the actual score distribution — tight clusters yield fewer, highly relevant results; broad distributions retain more.

```python
def calculate_zscores(cosine_scores):
    mean = np.mean(cosine_scores)
    std_deviation = np.std(cosine_scores, ddof=1)
    z_scores = [(x - mean) / std_deviation for x in cosine_scores]
    return z_scores
```

**Investigation Areas:**

1. **Primary vector_search retrieval** — After Neptune returns similarity scores, use z-score filtering instead of (or in addition to) the current top-N + minimum similarity threshold. Would this improve precision for high-quality queries while maintaining recall for ambiguous ones?

2. **Auto-enrichment pipeline** — The auto-enrichment step (neighbor discovery, citation resolution) also retrieves chunks. Could z-score filtering reduce noise in enriched results, especially for heavily-connected nodes where the neighbor cap is hit?

3. **Case law discovery** — When resolving case law via graph traversal + vector similarity, z-score filtering could help distinguish genuinely relevant cases from ones that happen to share vocabulary but aren't on-point.

4. **FAQ search** — The Bedrock KB FAQ search returns scored results. Would z-score filtering help separate true FAQ matches from partial-keyword hits?

**Considerations:**
- Small result sets (< 5) make z-score unstable — need a minimum sample size fallback
- Should this replace the existing similarity threshold or act as a secondary filter?
- Neptune's `vector_search` returns scores in a specific range — need to verify the distribution shape empirically before assuming normality
- Adding numpy as a Lambda dependency (or using a pure-Python implementation) has packaging implications

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — `vector_search` tool implementation and auto-enrichment logic
- `backend/lambdas/agentic_retrieval/neptune_client.py` — Neptune query layer (raw similarity scores available here)

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

### Task 23: Strip WPAM running headers from chunk text

**Problem:** Each page of the WPAM PDF carries a running header like "Chapter 14 Agricultural Valuation" or "Chapter 12 Residential Property Valuation". These survive boilerplate stripping because the current `WPAM_PATTERNS` only matches bare `Chapter N` lines (no title text). As a result, the header is repeated in ~579 chunks across the WPAM 2025 corpus — wasting embedding dimensions on repeated noise and slightly diluting retrieval precision.

**Evidence (2026-06-23 chunk inspection):**
| Running header | Occurrences (buried mid-chunk) |
|---|---|
| Chapter 14 Agricultural Valuation | 79 |
| Chapter 12 Residential Property Valuation | 69 |
| Chapter 13 Commercial Valuation | 67 |
| Chapter 10 Assessment/Sales Ratio Analysis | 53 |
| Chapter 7 Assessment Roll & Parcel Information | 48 |
| ... (15+ more) | ... |

**Fix:** Add a pattern to `WPAM_PATTERNS` in `tools/pdf_chunking/boilerplate.py`:
```python
re.compile(r"^Chapter\s+\d+[\s–—–—-]+.+$"),
```
This matches `Chapter N <title>` lines (with dash/em-dash separator or space) which are always running headers in WPAM PDFs. True chapter starts are multi-line (title + section number + content) and won't be stripped because the boilerplate filter operates line-by-line.

**Caveat:** Must not strip the actual chapter heading at the start of each chapter. The heading line itself (e.g., "Chapter 17 Manufacturing and Utility Assessment") appears identically as both the true heading and as the running header. However, in the WPAM chunker, the true heading triggers a chunk split and becomes the first line of a new chunk — boilerplate stripping runs BEFORE chunking, so stripping ALL `Chapter N Title` lines would remove the true headings too. Need either:
1. A frequency-based approach (strip only lines that appear >3 times in the document), or
2. A position-aware approach (strip only if the line appears after another content line on the same page — i.e., it's a header, not the first content line of a new section).

**Priority:** Low — doesn't affect citation accuracy or page linking. Cosmetic improvement to embedding quality. Roll into next reingestion cycle.

**Key file:** `tools/pdf_chunking/boilerplate.py` — `WPAM_PATTERNS` list

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

### Task 25: Fix WPAM 2025 garbled table chunks and heading metadata

**Problem:** The WPAM 2025 chunking produced ~48% of chunks with incorrect heading metadata and ~10-20% with garbled text from multi-column PDF table extraction. This pollutes vector search results with noise chunks that match on keywords but provide no useful information to the model.

**Evidence (2026-06-24 query audit):** Query "If a manufacturing property sells, is the assessment adjusted to the sale price?" surfaced 3 garbled WPAM chunks in the first `vector_search` turn:

| Chunk ID | Page | Issue |
|----------|------|-------|
| `_chunk_0825` | 355-356 | Text is fragmented table columns: `"various com\n\nization rate\n\n57 \| shment of t\n\nponents of t"` |
| `_chunk_2088` | 895-896 | Heading is a table label: `"X-axis of this cost table shows the tonnage capabilities of the scale; while the"` |
| `_chunk_1739` | 757-758 | Interleaved columns: `"reference point of ti\n\nthe assessor has to\n\nfor a short-term (\n\ninstitutional lender \| Per S.F. Value"` |

These matched because they contain "manufacturing" keywords buried in the garbled text, but contribute zero useful context to the answer.

**Sub-issues:**

#### 1. Heading metadata is catastrophically wrong for ~48% of WPAM chunks

| Heading assigned | Chunk count | What it actually is |
|-----------------|-------------|---------------------|
| "M-forms due, last day to submit request to DOR for manufacturing" | 548 | Appendix calendar table column header |
| "L - Lottery credit" | 521 | Appendix table row label |
| "X-axis of this cost table shows the tonnage capabilities of the scale; while the" | 108 | Cost table caption fragment |
| "V. Poor" | 199 | Condition rating table cell |

Out of 2226 WPAM 2025 chunks, ~1069 have completely wrong headings — these are table column headers, row labels, or captions that leaked into heading extraction for unrelated chunks across the entire document. This causes:
- WPAM dedup "heading group" logic to group by garbage headings instead of actual semantic topics
- Citation cards to display nonsensical headings to users
- The model to receive misleading context about what a chunk is about

#### 2. Garbled table text in chunk bodies

Multi-column PDF layouts (cost tables, comparison charts, calendars) produce chunks where PyMuPDF interleaves columns character-by-character rather than reading left-to-right per row. The text is syntactically meaningless — fragments like `"57 | shment of t"` (the number is from one column, the truncated word from another).

**Overall chunk quality stats:**
- Total chunks in graph: 46,626
- Average text length: 1,185 chars
- Chunks < 200 chars: 15,127 (32%) — many are 1-30 chars (single table cells or page numbers)
- Chunks at max (2400-2500 chars): 16,328 (35%)

#### 3. News pages lack page metadata

`news_pages-assessor-news-2023-10-27` (and likely other HTML-sourced docs) have `start_page=None, end_page=None` and empty headings for all chunks. Citation cards for these docs will lack anchor context. Low priority since news pages are web content, but the system should either assign synthetic page numbers or handle the null case gracefully in the UI.

**Root Cause Analysis:**

The WPAM 2025 PDF is a ~950-page document with extensive tables, appendices, calendars, and multi-column layouts. The heading extraction in the WPAM chunker picks the first "heading-like" line encountered, but for chunks that fall within table regions, the "heading-like" line is actually a table column header or row label that happens to be bold/larger font.

The garbled text is a PyMuPDF extraction failure on multi-column layouts — the library reads text in DOM order rather than visual reading order, interleaving columns. The existing `extraction_looks_good()` quality gate in `pymupdf_extractor.py` doesn't catch these cases because the text passes basic heuristics (has words, has whitespace, etc.) even though it's semantically garbled.

**Direction (ranked by impact):**

1. **Post-extraction garbled text detection** — Add a quality signal that detects interleaved-column text patterns (high frequency of `|`, very short "words" separated by newlines, high ratio of fragments < 5 chars). Flag or discard chunks that fail. Could be applied either at chunking time or as a graph-level filter at query time.

2. **Heading validation/repair** — After chunking, validate headings against a known set of WPAM chapter headings (e.g., "Chapter N Title" format). If a chunk's heading doesn't match any known chapter pattern AND appears on >50 other chunks, it's likely a leaked table header — replace with the nearest valid chapter heading based on page number.

3. **Table region detection** — Before chunking, identify table regions in the PDF (PyMuPDF has `page.find_tables()` API). Either skip table regions entirely, extract them as structured data, or at minimum mark chunks from table regions so they can be downranked in retrieval.

4. **Query-time filtering** — Add a lightweight garbled-text detector in the `vector_search` tool that filters out chunks whose text fails a coherence check before returning results to the model. Fastest to implement, but doesn't fix the root data quality issue.

**Relationship to other tasks:**
- Task 23 (strip running headers) addresses a subset of the heading pollution but not the table-header leak
- Task 6 (reduce chunk size) is marked done — the 2500-char max is working correctly; the issue is source text quality, not chunk size

**Key files:**
- `tools/pdf_chunking/pdfChunker.py` — WPAM chunker heading extraction logic
- `tools/pdf_chunking/pymupdf_extractor.py` — `extraction_looks_good()` quality gate
- `tools/graphrag/extract.py` — orchestrates extraction and chunking
- `backend/lambdas/agentic_retrieval/tools.py` — potential query-time filtering location

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

