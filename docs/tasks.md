# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing | — |
| 43 | Prompt rewrite: compress FRAMEWORK APPLICABILITY section | — |
| 44 | Prompt rewrite: compress CITATION RULES section | — |
| 45 | Scholar-sourced case-law dedup pass (docket-number keyed) | 2f57489d (Lowe's 379/405 dup) |
| 46 | Backfill case-law titles from opinion-text captions | — |
| 47 | Route case-law / flat-structure docs straight to search_document (skip list_sections/get_section) | — |
| 48 | Investigate WPAM get_section gap — agents re-search doc-globally after get_section on same chapter | — |
| 49 | Validate Scholar-fetched opinion matches requested citation (prevent citation→text mis-assignment) | 2f57489d |

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
| 17 | Handle multipart queries (split or unified answering strategy) |
| 20 | Add user persona setting (government worker vs. citizen) |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags |
| 41 | Fix statute citation page numbers, card titles, and chunker page-header pollution |
| 42 | Markarian hierarchy query fails to ground `statutes-70` (turn-budget exhaustion) |
| 5 | Replace LLM classification with structural parsers |
| 21 | Add z-score normalization to search_document result filtering (investigated — declined) |

---

## Task Details

---

---

---

### Task 43: Prompt rewrite — compress FRAMEWORK APPLICABILITY section

**Risk level:** High — the model might treat IAAO/USPAP as Wisconsin law without the explicit warning.

**Current:** 10 lines listing every authority tier with full descriptions (Constitution, Statutes, Case Law, Admin Rules, WPAM, FAQs, Guides, IAAO, USPAP).

**Proposed:** Compress to a hierarchy line + one warning:
```
Authority hierarchy: Constitution > Statutes > Case Law > Admin Rules > WPAM > FAQs > Guides.
IAAO and USPAP are national recommendations only — NOT Wisconsin law. Always note this when citing them.
```

From 10 lines to 2. The authority hierarchy is already encoded in the `authority_level` field on every chunk/document the model sees — it doesn't need the full prose explanation of each tier. The critical constraint (IAAO/USPAP are not law) must remain explicit.

**Validation:** Run queries that surface IAAO content (e.g., "What information is used to determine my residential land assessment?" which retrieves IAAO standards alongside WPAM). Verify the model still notes IAAO as "recommendations, not requirements."

**Key files:**
- `config/model_configs.toml` — agenticRetrieval prompt, FRAMEWORK APPLICABILITY section

---

### Task 44: Prompt rewrite — compress CITATION RULES section

**Risk level:** High — removing guardrails might cause hallucinated citations or empty source cards.

**Current:** 18 lines of ALWAYS/NEVER rules covering: only cite retrieved docs, cite chapter docs not stubs, authority hierarchy, WPAM edition filtering, advisory supersession, WPAM draft announcements, citation-means-reliance, case names need IDs, don't fabricate, don't cite without sources, prefer newer guidance, don't treat IAAO as law.

**Proposed:** Keep critical rules, drop ones the retrieval layer already handles:

Keep (critical, model-behavior-affecting):
- Only cite documents you actually retrieved via tools
- For statutes, cite the chapter doc (`statutes-70`) — never stub nodes like `WIS-STAT-70.32`
- If you NAME a case in prose, its node ID MUST be in cited_doc_ids
- Never fabricate statute references or case citations
- Do not cite without sources

Drop (automated by pipeline or redundant):
- WPAM edition filtering paragraph (retrieval layer already filters old editions)
- Advisory/news supersession logic (niche edge case)
- WPAM draft announcement rule (niche edge case)
- "citation means reliance, not awareness" (restates "only cite retrieved docs")
- "distinguish authority levels" (already in FRAMEWORK APPLICABILITY)
- "compare edition_year / effective_date" (niche, covered by "prefer newer guidance")

From 18 lines to ~6 lines.

**Validation:** Run queries and verify: (1) model doesn't cite docs it didn't retrieve, (2) model doesn't cite stub nodes, (3) case names have matching IDs in cited_doc_ids, (4) no fabricated statute numbers.

**Key files:**
- `config/model_configs.toml` — agenticRetrieval prompt, CITATION RULES section

---

### Task 21: Add z-score normalization to search_document result filtering — INVESTIGATED, DECLINED (2026-08-13)

**Verdict:** Do **not** ship blanket z-score filtering on `search_document` as proposed. Real-traffic replay shows it removes useful near-tie chunks far more often than it removes noise. The narrow gap-cliff variant (below) is defensible but low-value (~2 calls in 3 weeks).

**Original hypothesis (why it *seemed* to work):** Z-score was ruled out for `vector_search` (2026-06-25) because heterogeneous sources create overlapping distributions. `search_document` operates within one document, so — the reasoning went — score differences should cleanly separate "this section answers the question" from "this section mentions a keyword in passing."

**Method (real data, not synthetic):** Pulled all **22 real `search_document` calls** from CloudWatch over the prior 3 weeks (actual `doc_id` + sub-query pairs), then **replayed each against the live Neptune graph** (`g-ndvl4j73v4`): embed with Titan v2 → `vector_search(top_k=800)` → filter to target doc → recover the *full* within-doc score distribution (what the tool truncates with `[:top_k]`) → apply the exact z-logic already in `_rank_chunks_by_relevance` (`z ≥ 0.5`, keep ≥1). Note: `vector_search` returns Neptune's raw `score` per chunk, so no chunk re-embedding was needed. Replay harness: `/tmp/replay_search_document.py` (not committed).

**Findings:**
- **First half of the hypothesis confirmed:** single-doc distributions really are tight (std 0.04–0.25) vs. the heterogeneous cross-source case.
- **Conclusion does NOT follow:** because the distribution is tight *and smoothly decaying*, `z ≥ 0.5` mostly slices through near-identical chunks. z-filter changed the result on **6 of 22 calls (27%)**, always shrinking. Of those 6 cut points, **5 severed near-ties** (last-kept vs. first-dropped gap 0.5–2.7%) and only **1 dropped a genuine low-relevance tail** (10% gap).
- The WPAM/statute queries the task specifically motivated show scores decaying ~1% per rank (e.g. `[1.489, 1.472, 1.471, 1.463, 1.445]`) — z-filter would drop chunk #5 that's 1.2% behind #4. That's a near-tie, not noise.
- Genuine "junk tails" inside the current `top_k` appear in only **3 of 22 calls**, all in **small case-law docs (7–17 chunks)** — precisely where z-statistics are least reliable (tiny *n*).

**If revisited — the honest signal is an absolute relative-gap cliff, not a z-threshold:** only trim the tail when an adjacent chunk is >7–10% below the prior one. In this dataset that fires cleanly on the 2 small case-law calls and leaves smooth WPAM/statute distributions intact. Low ROI, but non-harmful.

**Related discovery (see Tasks 47/48):** The tool-usage audit that came out of this replay found the real inefficiency isn't result filtering — it's *routing*. 11 of 22 calls re-ran `search_document` on a WPAM doc where `get_section` had already run on the same chapter, and case-law docs (flat structure) reach `search_document` after wasted `list_sections`/`get_document` hops.

**Note:** file paths in the original task (`tools.py`, `neptune_client.py` at repo root) are stale post-Task-38. Current locations: `backend/lambdas/agentic_retrieval/agent_tools/executor.py` (`search_document` ~line 427, `_rank_chunks_by_relevance` ~line 149, `faq_search`), `backend/lambdas/agentic_retrieval/graph/neptune_client.py` (`vector_search` ~line 221).

---

### Task 47: Route case-law / flat-structure docs straight to search_document

**Context (from the Task 21 tool-usage audit, 2026-08-13):** `search_document` is highly effective in practice — the target doc ended up **cited in 21 of 22 calls (95%)**, so it is NOT the "hail-mary" it reads like. But agents waste turns reaching it for flat-structured docs.

**Finding:** Case-law docs have **`distinct_headings == chunk_count`** (verified in graph: `case-law-405-wis-2d-616` = 17 chunks / 17 headings; `case-law-2025-wi-app-43` = 7/7). Every chunk is its own "heading," so `list_sections`/`get_section` are structurally useless there — there is no chapter hierarchy to navigate. Short gov-pubs are similar. Yet the tool descriptions steer the agent toward `list_sections` → `get_section` first ("consider list_sections + get_section for multi-chapter documents"), so on case law the agent burns a `get_document` or `list_sections` hop before correctly falling back to `search_document`.

**Proposed:** In `agent_tools/definitions.py`, tell the agent to go **straight to `search_document`** (or `fetch_case_opinion` when the full opinion is needed) for case-law docs and other flat/short documents — skip `list_sections`/`get_section`, which only pay off for genuinely multi-chapter docs (WPAM, large guides, multi-section statutes). Optionally detect flat structure server-side (heading count ≈ chunk count) and hint it back in the tool result.

**Validation:** Re-run the dark-store / Lowe's case-law queries; confirm the agent reaches case-law content in one hop with no wasted `list_sections`/`get_document` call, and turns-used drops.

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools/definitions.py` — `search_document` / `list_sections` / `get_section` descriptions
- `config/model_configs.toml` — `agenticRetrieval` prompt, if routing guidance lives there too

---

### Task 48: Investigate WPAM get_section gap — agents re-search doc-globally after get_section

**Context (from the Task 21 tool-usage audit, 2026-08-13):** The single biggest `search_document` usage pattern — **11 of 22 calls** — was the agent running `search_document` on a WPAM doc where it had *already* pulled a section via `get_section` on the same chapter in a prior turn.

**Finding:** The dominant example is the "dark store / assessor should avoid distressed sales" query family: agent does `get_section(WPAM, "Chapter 13 Commercial Valuation")`, then next turn fires `search_document(WPAM, "assessor should avoid ... vacant dark distressed ...")`. The doc gets cited either way, so it *works* — but it means heading-based navigation didn't surface the specific passage on the first hop, forcing a doc-global re-search that costs an extra turn and re-runs the full 800-chunk over-fetch.

**Hypothesis:** the target passage spans a heading boundary, or lives under a subheading that `get_section`'s ranking didn't surface. Same territory as Task 27 (sparse WPAM subheadings) and Task 30 (`get_section` z-score ranking) — worth checking whether the "assessor should avoid distressed sales" text is chunked under the chapter heading the agent picked, or under a sibling/subheading.

**Proposed:** Trace one dark-store query end-to-end; inspect which WPAM chunk carries the "avoid distressed sales" language and under what `heading`/`subheading`; determine why `get_section` on Chapter 13 didn't return it. Fix may be chunk-heading metadata (Task 27 family) or `get_section` ranking, not a `search_document` change.

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools/executor.py` — `get_section` (~line 498), `_rank_chunks_by_relevance`
- `backend/lambdas/agentic_retrieval/graph/neptune_client.py` — `get_section_chunks_with_embeddings`, `list_document_sections`
- `tools/ingestion/chunking/` — WPAM chunk heading/subheading assignment

---

### Task 45: Scholar-sourced case-law dedup pass (docket-number keyed) — ONE-TIME PASS APPLIED (2026-08-13)

**Status:** `ops/dedup_case_law_docket.py` built and applied. Groups by UNION of docket ∪ normalized case-name, each gated by a ≥0.6 word-set-Jaccard text-similarity confirmation over the opinion body. **34 confident merges → 54 nodes deleted** (graph 1202→1148, 0 orphan chunks). The Lowe's `379`/`405` cross-host dup is resolved (kept `405`, edges re-pointed). **32 groups correctly flagged** (different opinions sharing a name/docket — e.g. `State v. Davis` ×3, and the appeals-vs-supreme same-docket trap `Tetra Tech`/`Baron` at sim 0.17). **2 groups routed to corruption** (see Task 49). Durable prevention (persist docket in `extract.py`, add as secondary key to `load.dedup_case_law_docs`) still TODO.

**Context:** The one-time `ops/dedup_case_law.py` pass (merged in #14) collapsed parallel-citation duplicates keyed on a shared CourtListener `source_url`. That caught every case whose opinion text came from CourtListener. It did **not** catch cases whose text came from the **Google Scholar fallback** in `ingest_case_law.py` (`upload_case`, tier 2 — fires when CourtListener returns no `opinion_id`).

**Why the existing dedup missed them:** Scholar-sourced nodes never get a CourtListener opinion URL. Their `source_url` stays the bare `docs.legis.wisconsin.gov/document/courts/{citation}` — which is **different for each parallel citation of the same opinion** (`2023 WI App 22`, `990 N.W.2d 783`, `407 Wis. 2d 628` are three URLs for one case). Source-url-keyed dedup treats them as distinct, so all three nodes survive.

**Quantified damage (2026-08-13):** Grouping case-law nodes by the docket number (`NNNNAP NNN`) parsed from their opinion text found **40 docket groups with >1 node = 69 redundant nodes**. This is a **floor, not a ceiling** — the quick docket regex only matched 211 of the 1,193 nodes with `.txt` (the older ~982 opinions use a caption format the probe regex didn't hit), so the true count is higher. Examples:
- `2022AP289` → `case-law-2023-wi-app-22` + `case-law-990-n-w-2d-783` + `case-law-407-wis-2d-628` (Delavan Lake Sanitary District v. Walworth County)
- `2021AP1076` → `case-law-2022-wi-app-40` + `case-law-978-n-w-2d-558` + `case-law-404-wis-2d-141` (Waupaca County v. Golla)

**Impact:** Same class of bug the #14 pass fixed — the agent can cite one opinion under multiple node IDs, and citation cards fragment. Lower severity than the CL cohort only because it's fewer nodes.

**Live example confirmed in a real answer (query `2f57489d`, 2026-08-13):** The dark-store answer cited BOTH `case-law-405-wis-2d-616` (CourtListener URL) and `case-law-379-wis-2d-141` (legis URL) as if they were two separate Lowe's holdings. They are the **same Supreme Court opinion** — both carry docket `2019AP1987`, both open "Lowe's lost the case. The Wisconsin Supreme Court held…", identical internal citation sets. `379 Wis. 2d 141` is a **misattributed citation** for the 2023 WI 8 opinion. This is the cross-host case the #14 source_url dedup structurally cannot catch (one node CourtListener-sourced, one Scholar/legis-sourced → different URLs), and it produced exactly the user-visible fragmentation: two citation cards for one case, and prose that reads as if there's independent corroboration. **This validates that the dedup key MUST be the docket number, not source_url.**

**Proposed:**
1. **Pick a stable dedup key that survives the Scholar path.** The Wisconsin **docket / appeal number** (`2022AP289`) is one-per-case and appears verbatim in the opinion text. Parse it from the raw `.txt` (`raw/case-law/{reporter}/{slug}.txt`). Fall back to a normalized parsed caption where no docket is present (older opinions).
2. **Extend `ops/dedup_case_law.py`** (or add a sibling pass) to group by docket, keep the reporter-priority winner, re-point `Statute-[:CITES]->loser` edges, `DETACH DELETE` losers + their chunks, and purge loser doc_ids from `extracted/`/`embedded/`. Reuse the merge/verify logic already in that script.
3. **Durable prevention:** the load-time `dedup_case_law_docs()` (added in #14) only dedups by `source_url`. Add a secondary key so it also collapses by docket number (persist the parsed docket as a node/cache property during `extract.py` so `load.py` can group on it without re-reading S3).

**Validation:** Re-run the docket grouping after the pass → expect 0 multi-node docket groups. Spot-check that each surviving winner keeps all inbound `Statute-[:CITES]` edges from the collapsed losers.

**Key files:**
- `tools/ingestion/ops/dedup_case_law.py` — extend with docket-keyed pass
- `tools/ingestion/ingest_case_law.py` — `upload_case` (Scholar fallback), persist docket to metadata
- `tools/ingestion/extract.py` — carry docket through to the embedded record
- `tools/ingestion/load.py` — `dedup_case_law_docs` secondary key

---

### Task 46: Backfill case-law titles from opinion-text captions

**Context:** After #14, **52 case-law nodes still have citation-only titles** (e.g. `"998 N.W.2d 506"` instead of a case name). These are all Scholar-sourced nodes with bare `legis.wisconsin.gov` URLs — no case-name slug to recover offline, and CourtListener's citation index returns 404 for most (recent/unpublished WI App and late N.W.3d opinions CL hasn't ingested). So neither #14 backfill step could name them.

**Finding (2026-08-13):** The case name **is** recoverable — not from the URL or CL, but from the **opinion text itself**, which was already scraped from Scholar and stored in `raw/case-law/{reporter}/{slug}.txt`. A caption parser hit **44 of 52** cleanly (e.g. `998-n-w-2d-506` → "Veritas Village, LLC v. City of Madison"). Breakdown:
- **44 parsed** from the opinion caption (two dominant formats: the `Complete Title of Case:` block, and the inline `NAME, Role, v. NAME` header).
- **5 parse failures** — text exists but caption is atypical (`961-n-w-2d-903`, `693-f-supp-3d-975`, `2021-wi-app-38`, `187-wis-2d-501` starts mid-opinion with no caption, `5-n-w-3d-952`).
- **3 true stubs** — no `.txt` at all (`417-wis-2d-629`, `398-wis-2d-542`, `24-n-w-3d-601`); both CL and Scholar failed to return text, so no chunks and no caption. Only these 3 are genuinely unrecoverable from current data.

**Severity:** Cosmetic only. Confirmed the bad title does **not** affect discoverability — every retrieval path (`caselaw_backfill` via `get_case_chunks_for_statutes_with_embeddings`, `citation_extraction` via `resolve_case_citations`, and the `fetch_case_opinion` tool) keys on chunks, embeddings, or the `citation` property, never `title`. 49 of the 52 have real opinion chunks and are fully discoverable; the title just renders a bare reporter number on the card.

**Proposed:**
1. **Harden the caption parser** — handle the two known formats plus the atypical cases; fix capitalization (opinion captions are ALL-CAPS → need a title-case pass with an acronym allowlist: LLC, U.S., D/B/A, LP, Inc., etc. — the probe produced "Llc", "U.s.", "D/b/a").
2. **Run it as a backfill** — for each citation-only node, derive the S3 key from `doc_id` (`_reporter_for_slug`), read the `.txt`, parse the caption, set `title = "{case_name}, {citation}"`.
3. **Sequence after Task 45** — do the docket dedup FIRST. Several of these 52 are duplicates of each other (the docket groups in Task 45 include them), so backfilling titles first would just name nodes that then get deleted. Title backfill is a natural byproduct of the dedup pass — the survivor gets the parsed caption as its title in the same operation.
4. **Durable prevention:** fix the Scholar fallback in `ingest_case_law.py` to persist the parsed caption as `case_name` (currently it only captures the Scholar page `<h1>`, which came back empty for these 52). Then `extract.py`'s existing title logic produces a named title from the start — no backfill needed on future runs.

**Remaining gap:** the 3 true stubs stay citation-only until CourtListener indexes them (or a manual name is supplied). Acceptable — they have no opinion text to answer from anyway.

**Key files:**
- `tools/ingestion/ops/dedup_case_law.py` — caption parser + title backfill (fold into the Task 45 pass)
- `tools/ingestion/ingest_case_law.py` — `fetch_scholar_opinion` / `upload_case`, persist parsed caption as `case_name`
- `tools/ingestion/extract.py` — title derivation already consumes `case_name`

---

### Task 49: Validate Scholar-fetched opinion matches requested citation — CORRUPT NODES PURGED (2026-08-13), DURABLE FIX TODO

**Context:** Surfaced while investigating Task 45. The Google Scholar fallback in `ingest_case_law.py` (`upload_case` → `fetch_scholar_opinion`) searches Scholar by citation string and stores the first opinion it scrapes. Scholar's citation search is fuzzy, so for some citations it returned and stored the **wrong opinion's text** — a citation→text mis-assignment. The node's citation/title describes one case; its chunks describe another.

**Why it's worse than a dup:** these aren't extra copies — they're single nodes carrying the WRONG opinion. Because the chunks are wrong AND the node keeps inbound `Statute-[:CITES]->` edges, retrieval is corrupted: a statute citing the mis-assigned citation leads the agent to a different case's holdings.

**Corpus scan (2026-08-13):** Detected via title-case-name vs opinion-body-case-name with zero shared party token, hand-verified against raw text. **2 genuine cases**, both Scholar/legis-sourced (confirming CL fetch-by-`opinion_id` is reliable — all CL-sourced scan hits were false positives: consolidated 7th-Cir opinions, drop-cap formatting, caption-parser noise):
- `case-law-414-wis-2d-633` titled "WI State Legislature v. Kaul" but text is **Birge v. Simplicity Credit Union** (docket 2024AP567) — 20 statute CITES
- `case-law-395-wis-2d-351` titled "Adams Outdoor Advertising" but text is **City of Waukesha v. Board of Review** (docket 2019AP1479) — 18 statute CITES

**One-time cleanup DONE:** `ops/purge_corrupt_case_law.py` deleted both nodes + their chunks (graph 1148→1146, 0 orphan chunks) and purged their work-bucket caches. Edges dropped (not re-pointed — the citations genuinely belong to Kaul/Adams, for which no correct node exists; the correct Birge/Waukesha nodes survive independently). Dropped citations logged for targeted re-ingest: Kaul `414 Wis. 2d 633`, Adams `395 Wis. 2d 351`.

**Coverage caveat:** title-vs-body detection only works when the title has a real name (not a bare citation) and the caption parses (~136 nodes comparable). A citation-vs-body-reporter check was tried but is too noisy (opinion headers often show only the neutral cite, not the reporter parallel cite the node carries) — deferred. The genuine risk is confined to the ~115 Scholar/legis nodes.

**Durable fix (TODO):** In `fetch_scholar_opinion` / `upload_case`, after scraping, verify the scraped opinion's own citation (or docket, parsed from its caption) matches the requested citation before storing. On mismatch: reject and fall through to a stub rather than storing the wrong opinion. Prevents recurrence.

**Key files:**
- `tools/ingestion/ops/purge_corrupt_case_law.py` — one-time purge (hand-verified list; extend if more surface)
- `tools/ingestion/ingest_case_law.py` — `fetch_scholar_opinion` / `upload_case` citation-match guard

---



