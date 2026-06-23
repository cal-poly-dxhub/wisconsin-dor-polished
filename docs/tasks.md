# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 1 | Disambiguate generic queries before full retrieval | [Response A](#response-a) |
| 2 | Fixing linking issues | — |
| 5 | Replace LLM classification with structural parsers | — |
| 14 | Improve case law discovery in vector_search auto-enrichment | [Response B](#response-b) |
| 17 | Handle multipart queries (split or unified answering strategy) | — |
| 18 | Show traversed sources in UI during agentic retrieval | — |
| 19 | Fix train-of-thought flicker on sidebar session hover | — |
| 20 | Add user persona setting (government worker vs. citizen) | — |
| 21 | Investigate z-score normalization for vector_search result filtering | — |
| 22 | Apply over-fetch multiplier when target_wpam_year is set | — |

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

---

## Task Details

### Task 1: Disambiguate generic queries before full retrieval

**Problem:** For generic queries that don't specify a certain type/classification of land, the system adds classification-specific detail to the answer (e.g., citing the manufacturing manual when the user never asked about manufacturing).

**Suggested Direction:** Add a structural change to the retrieval path — after FAQ semantic search, first gather enough information against the query to understand if the answer depends on the user's specific needs (e.g., different answer for agricultural vs. manufacturing vs. residential land). If so, before doing the full retrieval path, ask a follow-up back to the user: "There are multiple types of X — which one best suits your needs?" Once the user responds, we have a much more targeted query to retrieve against.

---

### Task 2: Fixing linking issues

**Problem:** Multiple issues with how source links are generated and presented to users.

**Sub-issues:**

- [ ] **Link TTL** — Harden presigned URL expiration so links don't expire, or find an alternative solution so sources are always accessible to the user.
- [ ] **Page anchors** — Ensure all source documents can link to correct page numbers and anchors. Testers reported also an off-by-one issue where the link took them to the page after the relevant page. May need to reingest if chunk metadata (start_page/end_page) is sloppy. Additionally, inline citations point to the parent doc's first-chunk page rather than the specific section being cited — e.g., citing "Wis. Stat. § 70.47" links to page 1 of the statutes-70 PDF instead of the page where § 70.47 actually begins. This requires either per-subsection page metadata in Neptune or a smarter page lookup at link-resolution time.
- [ ] **Inline citations** — All sources cited as cards at the bottom of a response must also be mentioned as inline links within the answer text.
- [ ] **Remove redundant source list** — The chatbot should not additionally list sources as a line of text at the end of its response (the cards are sufficient).
- [ ] **Inline ↔ card reconciliation** — The model writes more inline `[Title](doc:id)` links than it puts in `cited_doc_ids`, so those links render in the answer but have no matching source card below. Need a post-filter to enforce the 1:1 invariant programmatically (the model drifts on this bookkeeping despite prompt instructions).

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

### Task 14: Improve case law discovery in vector_search auto-enrichment

**Problem:** The `vector_search` tool's auto-enrichment does not reliably surface relevant case law — especially recent decisions connected to statutes via `CITES` edges. The current approach depends on: (1) text regex citation extraction from retrieved chunks, and (2) `get_neighbors` on parent docs with a hard cap of 50 and no ordering. For heavily-connected nodes like `statutes-70` (hundreds of CITES edges), which 50 neighbors are returned is non-deterministic. This causes critical case law (e.g., *Children's Hospital of Wisconsin v. City of Wauwatosa*, 2025 WI App 43) to appear in some runs but not others for the same query.

**Evidence:** Same query ("Is the expansion of a non-profit hospital that is under construction on the assessment date qualify for an exemption from property tax?") run on 06/18 and 06/21. On 06/18 the agent happened to call `get_neighbors` on `WIS-STAT-70.11` with `title_filter: "hospital"`, found the 2025 case, fetched its full opinion from S3, and produced an excellent answer citing the case's facts and holding. On 06/21 the agent took a different tool path, never discovered the case, and produced a weaker generic answer. The auto-enrichment surfaced the same 5 generic cases both times — Children's Hospital was never in them because it's connected via graph edge (CITES), not mentioned in the chunk text.

**Root Cause:** The auto-enrichment resolves cases from *text mentions* (regex) but not from *graph structure* (CITES edges to statute subsections). The `get_neighbors` call on parent docs is too broad (entire statute doc) and too small (50 cap, no ordering) to reliably capture the right cases.

**Direction (ranked by effectiveness):**

1. **Chunk → Statute subsection → CaseLaw path** — Retrieved chunks already have `CITES` edges to specific statute subsection nodes (e.g., `WIS-STAT-70.11`). Add a step: collect the statute subsection IDs cited by the top chunks, then query for CaseLaw nodes that also have `CITES` edges to those same subsections. This is deterministic, targeted, and uses exactly the graph structure built during ingestion.

2. **Order the 50-cap by recency** — Add `ORDER BY n.effective_date DESC` to the `get_neighbors` query. Newer cases (2025) always beat older ones (1967) for a slot in the cap. Simple one-line change.

3. **Label-aware budget splitting** — Reserve a portion of the 50 neighbor slots specifically for CaseLaw nodes. Currently the 50 slots may be dominated by Documents/Frameworks, pushing case law out entirely.

4. **Prompt nudge** — Instruct the agent to always call `get_neighbors` with `title_filter` on relevant statutes. Cheapest to implement but fundamentally unreliable — the same prompt produced different behavior on 06/18 vs 06/21.

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — auto-enrichment logic (lines 665–791)
- `backend/lambdas/agentic_retrieval/neptune_client.py` — `get_neighbors()` (line 279), `resolve_case_citations()`, would need new method for subsection→CaseLaw traversal

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

### Task 18: Show traversed sources in UI during agentic retrieval

**Problem:** While the agent is performing retrieval (tool loop running), the user only sees the train-of-thought trace steps. There's no visual indication of which specific sources/documents the agent has looked at or traversed so far. This makes the wait feel opaque and doesn't build confidence that the system is finding relevant material.

**Direction:**

- **Live source feed** — As the agent calls `vector_search`, `get_neighbors`, or `get_authority_chain`, stream the document titles/names it encounters back to the frontend and display them in a lightweight UI element (e.g., a scrolling list of source chips, a sidebar panel, or inline badges under the trace).
- **Progressive accumulation** — Sources should accumulate as retrieval progresses, giving the user a sense of the breadth of material being consulted before the final answer arrives.
- **Tie into existing trace stream** — The backend already streams tool call metadata over WebSocket. Extract document titles from tool results and emit them as a dedicated message type (or enrich existing trace messages) so the frontend can render them distinctly from the reasoning steps.
- **Design considerations:** Keep it non-intrusive — it should enhance the waiting experience without overwhelming the UI. Consider collapsibility or a compact chip/pill format that expands on hover/click.

---

### Task 19: Fix train-of-thought flicker on sidebar session hover

**Problem:** When viewing an active session with the train-of-thought trace visible, hovering over session cards in the sidebar causes multiple UI elements in the chat window to flicker. Reproducible by moving the cursor back and forth between the chat window and a non-selected session card (the one that shows a light hover rectangle).

**Affected elements:**
- Train-of-thought trace section flickers
- Source cards' title font color flickers
- Feedback buttons (thumbs up/down) underneath the chat response — their border and background flicker

**Attempted fixes (did not resolve):**
- Narrowing sidebar `transition-all` to `transition-[width]`
- Wrapping `ChatMessage` in `React.memo` with stable `EMPTY_RESOURCES` array ref
- Replacing the outer `<motion.div>` wrapper on `ChatMessage` with a plain `<div>`
- Adding `initial={false}` to the trace `AnimatePresence` and `layout={false}` to the trace `motion.div`

**Key files:**
- `frontend/src/components/messages/chat-message.tsx` — trace rendering (`AnimatePresence` + `motion.div` with `height: 'auto'`)
- `frontend/src/components/messages/chat-container.tsx` — scroll handler that calls `setSelectedMessageId`
- `frontend/src/components/layout/sessions-sidebar.tsx` — sidebar with hover state + width transition

**Likely direction:** The root cause may be deeper than framer-motion re-renders — possibly CSS `hover:bg-muted` on the sidebar cards triggering a reflow that propagates through the flex layout to the chat container, causing the scroll handler or a resize observer to fire and update state. Needs profiling with React DevTools and/or Chrome Performance tab to identify the exact re-render trigger.

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

