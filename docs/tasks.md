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
| 23 | Strip WPAM running headers from chunk text |
| 25 | Fix WPAM 2025 garbled table chunks and heading metadata |

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

