# Task List

## TODO

| # | Task | Status (audit 2026-09-12) | Related Responses |
|---|------|---------------------------|-------------------|
| 43 | Prompt rewrite: compress FRAMEWORK APPLICABILITY section | Not started — still verbose 9-tier listing | — |
| 44 | Prompt rewrite: compress CITATION RULES section | Not started — still ~18 rules | — |
| 45 | Scholar-sourced case-law dedup pass (docket-number keyed) | One-time pass done; durable prevention (docket in extract.py + load.py secondary key) still needed | dark-store/Lowe's dup |
| 46 | Backfill case-law titles from opinion-text captions | Prod fixed 2026-09-11 (Phase 2 reload rewrote 1139 doubled titles). Root cause was NOT the caption parser: `embed.py` never refreshed metadata from `extracted/`, so every full load re-applied the July-4 cache. Durable fix tracked in 64. 115 Scholar-path bare-citation titles remain (original scope). | — |
| 47 | Route case-law / flat-structure docs straight to search_document (skip list_sections/get_section) | Not started — tool descriptions still steer to list_sections/get_section first | — |
| 48 | Investigate WPAM get_section gap — agents re-search doc-globally after get_section on same chapter | Open investigation — no findings recorded; get_section still z-score ranks | — |
| 49 | Validate Scholar-fetched opinion matches requested citation (prevent citation→text mis-assignment) | One-time purge done; durable citation-match guard still needed | dark-store/Lowe's |
| 51 | Disambiguation follow-up logic + classifier accuracy (BLOCKED — awaiting Wisconsin validation) | Core logic shipped; chip replies fixed 2026-09-11 (PR #38: chip label resolves to the original question, no re-classification). Accuracy tuning still blocked on DOR's validated list | TID net-new-construction query |
| 58 | Vocabulary-bridge (vocab-swap) for citizen-term → statute-term gaps | RETIRED 2026-09-14 — `vocab_swap` / `vocab_injection` stages deleted after the post-reload harness with the map OFF scored 33/42 (baseline 32/42) and all three mobile-home / camping-trailer cases passed, camping trailer citing the 4/29/26 advisory. Document expansion (Task 65) replaced it | §70.11(49) family |
| 59 | Mobile-home §70.11(49) reliability + legal-applicability question | End-to-end PASSES on the Task 65 staging graph (§70.11(49) presented as its own exemption section). Raw vector rank for the bare phrase stays low because grounded aliases describe (49) as camper/RV/park-model — consistent with Valeah's 09-10 statement. The legal question (does (49) cover residential mobile homes?) still goes to DOR | §70.11(49) / §66.0435 / §70.17(3) |
| 60 | DOR/SME content questions (client-side, not retrieval bugs) | Open — carry to DOR: maintenance-vs-revaluation source, residential grade scale + images, BOR interpreter reference, stale mfg-appeal guide | — |
| 61 | Content-gap ingestion — treaty pub + Innovation Grant FAQ | DONE (2026-09-10) — napt-treaty-update.pdf + slf-ig FAQ ingested & verified (2/2 target queries PASS). assrlist dropped: it's a link-fabrication issue → Task 62 | — |
| 62 | Reliability — client-side answer truncation + citation-link integrity | Open — raised in priority: tester-visible. Truncation is frontend stream/render (server sends full answer). Assessor-directory URL fabrication reproduced by the judge again 09-11; add link-resolves-to-retrieved-doc validation | — |
| 63 | Confidentiality follow-up — git-history purge decision | Open — decision needed BEFORE the repo moves to DOR's account (handoff = their eyes on history). Folded into Task 66 | — |
| 64 | Reconcile stale-embedding backlog (~1166 docs) | BUILT 2026-09-14 (branch fix/ingestion-metadata-integrity): extracted/ overlays doc metadata at load, embed --smart does metadata-only refresh, Phase 2 case-law title guard, Phase 10 integrity assertion, ops/purge_dedup_losers.py + losers.json skip. Purge APPLIED to S3 (63 losers: 54 docket dups, 6 parallel-cite dups, 2 Task-49 corrupt nodes, 3 ghost statutes-document-*). DONE 2026-09-14: full reload ran 19:06–19:17 PT at 128 m-NCU, Phase 10 clean (0 doubled titles, 0 orphans), CaseLaw 1202→1146, corrupt + ghost ids gone, graph back at 32 | — |
| 65 | Document expansion — generated plain-language aliases + gold tester queries prepended to chunk embed input; statute subsection-per-chunk split; staging-graph evaluation | PROMOTED 2026-09-12. Prod harness 33→34 (with link repair), mobile-home passes 3/3 runs; raw recall doc MRR 0.52→0.59, statute-expected recall@10 79%→93%. Vocab-swap-OFF harness 2026-09-14: 34/42, all recreational-home cases still pass (camping trailer loses the 4/29/26 advisory citation but still states §70.11(49)); turns −16 | camping trailer / mobile home §70.11(49) Post-reload 2026-09-14 (advisories enriched): swap ON 32/42, swap OFF 33/42, camping trailer cites the 4/29/26 advisory with the map off → map retired |
| 66 | Handoff engineering — move to DOR's AWS account as-is (117-user division access) | Plan written: docs/handoff-plan.md (inventory, SSO, security checklist, cost model, repo transfer, 12-step sequence). Top security items to fix pre-rollout: WebSocket $connect has no authorizer; session routes don't check ownership; self-signup open on the public URL | — |
| 67 | Annual-refresh runbook for DOR (data-only maintenance, ~20–25% FTE) | DONE 2026-09-14: docs/annual-refresh-runbook.md (17-step data-only refresh, mid-year single-doc cycle, backup/rollback, costs, when to call an engineer) | — |
| 68 | DOR meeting 09-17 agenda | Agenda drafted: ~/Work/DxHub/wisdor/2026-09-17-dor-meeting-agenda.md (not in repo — client material) | — |

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
| 52 | Subsection auto-backfill (C1) — superseded by the §70.11(49) nudge (#30), vocab-swap (#33), and document expansion (Task 65); closed 2026-09-12 |
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing (answerStream "Use Source Language in Link Text" section) |
| 50 | Rich feedback phase 2 — render richFeedback in the admin activity dashboard (RichFeedbackDisplay in activity-detail.tsx) |

## Done — Feedback remediation sprint (PRs #28–#33, 2026-09)

Shipped from the mixed/negative rich-feedback root-cause analysis. All merged to `main` and deployed (prompts via `upload_model_configs.py`; code via bundle + `cdk deploy`).

| PR | Shipped |
|----|---------|
| #28 | Classifier scope broadening + rephrase out-of-scope message (fixes false refusals on in-scope questions) |
| #29 | answerStream answer-accuracy guardrails + LLM-judge grader / Phase-B replay harness |
| #30 | §70.11(49) subsection nudge + answerStream structure/conditional rules |
| #31 | Trigger-gated vocab-injection search arm (later demoted to dormant fallback by #33) |
| #32 | Scrub client-feedback provenance from public test YAMLs (HEAD-only) |
| #33 | Promote vocab-swap to primary vocabulary mechanism (the recreational-home fix) |

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

**Live example confirmed in a real answer (the dark-store/Lowe's answer, 2026-08-13):** The dark-store answer cited BOTH `case-law-405-wis-2d-616` (CourtListener URL) and `case-law-379-wis-2d-141` (legis URL) as if they were two separate Lowe's holdings. They are the **same Supreme Court opinion** — both carry docket `2019AP1987`, both open "Lowe's lost the case. The Wisconsin Supreme Court held…", identical internal citation sets. `379 Wis. 2d 141` is a **misattributed citation** for the 2023 WI 8 opinion. This is the cross-host case the #14 source_url dedup structurally cannot catch (one node CourtListener-sourced, one Scholar/legis-sourced → different URLs), and it produced exactly the user-visible fragmentation: two citation cards for one case, and prose that reads as if there's independent corroboration. **This validates that the dedup key MUST be the docket number, not source_url.**

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

### Task 50: Rich feedback phase 2 — render richFeedback in the admin activity dashboard

**Context:** Phase 1 (shipped, deployed 2026-08-13, PR #17) replaced thumbs up/down with a structured feedback modal + annotation mode. Submit now POSTs to `POST /session/{id}/feedback` writing the scalar `thumbUp` (derived from rating: up→true, mid/down→false) **plus** a nested `richFeedback` map + `feedbackSubmittedAt` onto the ChatHistoryTable row. Confirmed live: a real submission stored the full nested structure (rating, per-question yes/no + comments, source notes, broken-link picker, annotations with offsets, speed).

**What's already wired (no work needed):**
- Write path — `update_query_feedback` (`backend/lambdas/chat_api/main.py`) conditionally writes `richFeedback` (via `TypeSerializer`) + `feedbackSubmittedAt`.
- Read path — `activity_detail_handler` (`GET /admin/activity/<id>`) already **returns** `richFeedback` + `feedbackSubmittedAt` (auto-deserialized). The detail API is done.
- Shared contract — backend `RichFeedback` Pydantic model (`backend/layers/step_function_types/models.py`) mirrors the frontend Zod schema (`frontend/src/api/chat-api.ts`) and the store shape (`frontend/src/stores/feedback-store.ts`).
- The admin GSI/list filters (up/down/rated/unrated) are unchanged and still work — they run on `thumbUp` only. **Do not** move the up/down signal out of the scalar `thumbUp` or the list filters + stat tiles break (would force a GSI rebuild).

**What's left (frontend-only, no backend/infra):** the admin activity **detail drawer** receives `richFeedback` in the API response but ignores it. Render the structured breakdown when present, degrading gracefully for legacy rows that only have `thumbUp`/`feedback`.
1. Extend the activity types in `frontend/src/hooks/use-activity-data.ts` (`ActivityItem`) with an optional `richFeedback` + `feedbackSubmittedAt`, mirroring the `RichFeedback` Zod shape.
2. Render it in `frontend/src/app/admin/activity/_components/activity-detail.tsx` — overall rating, the three Response yes/nos + comments, source notes (which source, cited-fully, missed detail), broken links + reason, annotations (quote + comment, ideally anchored/quoted against the answer), speed. Fall back to the existing `thumbUp`/`feedback` display when `richFeedback` is absent.
3. Optional list-view nicety: the list only projects `thumbUp`/`feedback` via the GSI, so a per-row rich summary needs either a `get_item` per row or a purpose-built projected summary attribute — defer that decision to this task; the minimum is the detail drawer.

**Validation:** open `/admin/activity`, find the seeded rich-feedback submission, confirm the detail drawer renders rating=mid, sourcesOk=no with the source note, the annotation, speed=timely, etc.; confirm an old thumbs-only row still renders without error.

**Key files:**
- `frontend/src/hooks/use-activity-data.ts` — activity types
- `frontend/src/app/admin/activity/_components/activity-detail.tsx` — detail rendering
- `frontend/src/api/chat-api.ts` — reuse the `RichFeedback` Zod schema for the activity response
- (reference) `backend/lambdas/chat_api/main.py` `activity_detail_handler` — already returns the field

---

### Task 51: Disambiguation follow-up logic + classifier accuracy — BLOCKED (awaiting Wisconsin validation, 2026-08-27)

**Status:** Core follow-up/topic-shift logic SHIPPED and deployed (us-east-1). Classifier accuracy tuning is ongoing and paused pending a validated list of clarify-worthy questions back from Wisconsin DOR. Revisit when they respond.

**What shipped (merged #24, prompt-only follow-ups pushed via `upload_model_configs.py`):**
- Follow-ups are now classified. Removed the blanket `if chat_history: return PROCEED` guard in `disambiguation.py`, so a generic new topic raised mid-session is still disambiguated while a drill-down on an established property type proceeds. `classify_query` takes chat history (truncated prior turns).
- Deterministic history compaction — `sanitize_answer_for_history` flattens replayed-answer markdown before it re-enters classifier/loop context (full-sanitized, lossless; caching is on).
- `TOPIC_SHIFT` verdict (flag `ENABLE_TOPIC_SHIFT`) — a follow-up opening an unrelated subject short-circuits with a soft, dismissible "start a new chat?" suggestion (Start new chat / Continue here). `suppress_topic_shift` gates ONLY that verdict (renamed from the original `force_proceed`), so Continue-here still honors OUT_OF_SCOPE and DISAMBIGUATE. Dismiss arms a one-shot client flag so the nudge fires at most once. Decision order is `SCOPE → TOPIC → DISAMBIGUATE` (TOPIC_SHIFT outranks DISAMBIGUATE).

**Accuracy work in flight:**
- Fixed a real miss (the TID net-new-construction query): "What is the new TID net new construction?" classified as DISAMBIGUATE. TID net new construction is a **district-level aggregate** — no per-property-type fork — so it should PROCEED. Fix was to sharpen the DISAMBIGUATE definition to (1) apply only to an INDIVIDUAL property AND (2) require the answer to actually differ by classification, plus an explicit carve-out that aggregate/jurisdiction-level calculations (TIF/TID, levy limits, equalized values, apportionment, shared revenue) are always PROCEED. Also tightened decision-order step 4 ("about an individual property AND needs a property type"). Pushed to DynamoDB `disambiguationClassifier`.
- **Known residual miss:** the exact wording "What is the **new** TID net new construction?" STILL disambiguates — the redundant "new" ("the new TID ... net new construction") pushes the model toward a newly-built-parcel reading and overrides the explicit rule. Every other phrasing ("What is TID net new construction?", "How is TID net new construction calculated?") correctly PROCEEDs. A prompt rule shifts the boundary but doesn't build a wall on adversarial surface tokens. Guaranteed fix if needed: add `"net new construction"` (+ `"tid"`, `"levy limit"`, `"equalized value"`) to the deterministic keyword short-circuit in `disambiguation.py` that PROCEEDs before the LLM runs — but that's a code change (bundle + `cdk deploy`), not a prompt push, and brittle to unlisted phrasings. Left as-is per decision on 2026-08-26.
- **Other flagged candidates (not yet actioned):** "What is open book?" → DISAMBIGUATE (open book is a type-independent procedure; likely should be PROCEED). "How much will I owe?" → OUT_OF_SCOPE (arguably a property-tax question). "What information is used to determine my assessment?" (57× in history, the most common disambiguated query) borders on legitimate — worth pressure-testing.

**Blocking dependency — Wisconsin DOR review:** Sent a docx (`/Users/sac/Work/DxHub/wisdor/Chatbot Clarifying Questions - Property Type.docx`) listing what the assistant currently clarifies, built from REAL tester queries (filtered from the ChatHistoryTable to rows that received the clarification prompt, then re-classified against the live prompt to keep only those that still disambiguate). Asked DOR to (1) validate the list and (2) modify/add. Their response defines the target labels for any further tuning — do not tune blind before it lands.

**When revisited:**
1. Fold DOR's validated list into a labeled regression set; run against the live classifier (harness pattern: pull live prompt from the ModelConfig DynamoDB table, `converse` each query with Haiku temp 0.0, bucket by verdict).
2. Decide the keyword short-circuit question for the TID "new" residual and any other adversarial phrasings DOR flags.
3. Action the "open book" / "how much will I owe" candidates if DOR agrees.
4. TOPIC_SHIFT is single-turn-untestable — validate with query pairs (prior topic + unrelated follow-up), not the single-query harness.

**Key files:**
- `backend/lambdas/agentic_retrieval/disambiguation.py` — `classify_query`, keyword short-circuit, verdict parse/gating
- `backend/lambdas/agentic_retrieval/handler.py` — pre-loop classification block, `suppress_topic_shift` / `ENABLE_TOPIC_SHIFT` gating
- `config/model_configs.toml` + `backend/lambdas/agentic_retrieval/_prompt_fallback.py` — `disambiguationClassifier` prompt (keep byte-identical; push via `tools/upload_model_configs.py --only disambiguationClassifier`)
- `frontend/src/components/messages/topic-shift-suggestion.tsx`, `frontend/src/hooks/use-new-chat.ts` — suggestion UI + new-chat/prefill

---

### Task 52: Subsection auto-backfill (C1) — guarantee dense-statute subsections reach the answer

**Status update (2026-09-10):** Largely SUPERSEDED. The §70.11(49) recreational-home problem this task targeted was solved for the **camping-trailer / RV / recreational-vehicle family** by the **vocab-swap** approach (Task 58, PR #33) — which promotes the statute vocabulary into the narrow vector-search arm so the controlling subsection surfaces at the top of retrieval, rather than auto-attaching it after the fact. The always-on C1 auto-backfill stage is **no longer the primary plan**. The subsection nudge (#30) and vocab-swap (#33) together cover the camping/RV case. Residual: **mobile-home** phrasing still doesn't reliably surface §70.11(49) — tracked as Task 59. The original tabled analysis is preserved below for reference.

**Status:** TABLED (2026-08-27). Option A shipped (#26); this is the follow-on that makes the fix reliable. Deferred pending a decision on the cheaper prompt-nudge alternative vs. the always-on stage.

**Context — why this exists:** The mobile-home exemptions query ("What exemptions can apply to a mobile home?") rated "mid" because the answer name-dropped **§ 70.11(49)** as plain text with no citation. § 70.11 is a dense enumerated section (~50 subsections packed multi-per-chunk by the chunker), and `get_section`'s semantic ranking silently drops a low-scoring subsection.

**What already shipped (Option A, PR #26):** `get_section` gained a `subsection` param that fetches the `(N)` chunk verbatim, bypassing ranking. Regression-clean (0 regressions, turns net −1). **But a direct post-deploy test proved A is insufficient alone:** the agent loop is non-deterministic — on one run it went `vector_search → search_document → prepare_answer` (2 turns) and **never called `get_section` at all**, so the `subsection` param never fired and 70.11(49) would again be uncited. A only helps when the agent *chooses* to drill in.

**Proposed (C1) — auto-backfill, independent of agent choice:** A backend stage (mirroring `statute_backfill`) that scans the top-N already-retrieved chunks for statute-subsection references (`§?\s*\d+\.\d+\(\d+[a-z]*\)`) resolving to a doc already in play, and attaches the matching subsection chunk — reusing the `_find_subsection_chunks` helper that landed in #26. Fires during retrieval assembly, so it adds **no agent turns / no tool calls** (the turn-bloat concern was Option A's, already cleared; C1 is turn-neutral by construction).

**The real risk — context bloat, and how it's bounded:** C1 auto-attaches chunks the agent didn't ask for, diluting answer-context + costing tokens. Bound it with the same three levers the existing `statute_backfill` uses without blowing up:
- **Cap** — `SUBSECTION_BACKFILL_CAP` (~2): at most N subsection chunks per query.
- **Gate** — only trigger from subsection refs in the top-K retrieved chunks (not every `(N)` mentioned anywhere), to avoid firing on incidental cross-references like "…not exempt under 70.11(49)" in an unrelated answer.
- **Dedup** — skip if the chunk is already in context (`already_have` set); `DIVERSITY_CAP_PER_DOC=3` clips downstream regardless.
Higher blast radius than A: C1 is **always-on** (every query), not opt-in, so a loose gate affects all traffic. Needs the regression harness to confirm the gate isn't over-firing.

**Cheaper alternative to evaluate FIRST (prompt nudge):** Add one line to `agenticRetrieval`: *"When a statute cross-references a specific subsection you'll cite (e.g. 70.11(49)), fetch it with get_section's `subsection` param before answering."* This raises how often the (already-safe) Option A fires, with near-zero risk and no always-on machinery. It doesn't *guarantee* firing like C1, but may close most of the gap. **Recommendation: try the nudge, measure with the harness (add the mobile-home query to the golden set as a direct guard), and only build C1 if the nudge proves unreliable.**

**Content half already fixed:** the 2026-04-29 "prefabricated structures" advisory (§ 70.11(49)) was ingested (Task 39 follow-on), giving a directly linkable source for that exemption independent of A/C1.

**Validation:** Add the mobile-home exemptions query ("What exemptions can apply to a mobile home?") to `graph_regression_queries.yaml` with `must_contain: ["70\\.11\\(49\\)|recreational prefabricated"]`; baseline → change → after-compare, watching the turns-delta guardrail and cited-doc drift.

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools/executor.py` — `_find_subsection_chunks` (landed in #26), `get_section` handler
- `backend/lambdas/agentic_retrieval/agent_tools/stages/statute_backfill.py` — template for the new stage
- `config/model_configs.toml` + `_prompt_fallback.py` — `agenticRetrieval`, if doing the prompt-nudge alternative
- `tools/ingestion/tests/graph_regression_queries.yaml` — add the mobile-home guard query

---

### Feedback remediation sprint — shipped detail (PRs #28–#33)

**PR #28 — Classifier scope broadening + rephrase out-of-scope message.** Broadened the pre-loop query classifier's in-scope topics (valuation/depreciation, transfer-fee ch.77/§77.25/RETR, assessor view / new construction, property-type definitions) so legitimate property-tax questions stop getting refused, and rewrote the out-of-scope message to invite the user to rephrase. Gated by a new prod-parity classifier regression harness (`tools/ingestion/ops/run_classifier_regression.py` + `classifier_regression_queries.yaml`). Prompt pushed via `upload_model_configs.py`.

**PR #29 — answerStream accuracy guardrails + LLM-judge harness.** Added an "Answering Accurately" ruleset to the Phase-B answer prompt: actor/timeframe qualifiers (who can do what, when), numbering consistency (don't claim N steps then present M), no case-law for administrative mechanics, and exact approved wording for specific procedures. Also built the durable test tooling: an LLM-judge grader (grades each answer against a rubric, replacing brittle keyword checks) and a Phase-B replay path in `run_graph_regression.py` (`--candidate-answerstream` / `--phase-b-only`) that regenerates the answer against saved retrieval context for cheap prompt iteration.

**PR #30 — §70.11(49) subsection nudge + answer structure/conditional rules.** Prompt rule directing the agent to fetch a cross-referenced numbered subsection verbatim via `get_section(subsection=…)` before answering, plus answerStream rules for themed section headings and restating a user's conditional in the conclusion.

**PR #31 — Trigger-gated vocab-injection search arm (superseded).** An additive `vector_search` stage that, when a mapped trigger term appears, runs a vocabulary-augmented query and keeps only docs the main/broad arms missed. Strict no-op without a trigger term. It reliably *surfaced* the target advisory into context but the agent didn't always *cite* it; **demoted to a dormant fallback by #33**.

**PR #32 — Scrub client-feedback provenance from public test YAMLs.** Replaced production queryIds with synthetic slugs and removed feedback-provenance framing (tester-complaint text, "expected failing" markers) from `graph_regression_queries.yaml` and `classifier_regression_queries.yaml`. HEAD-only — git history still contains the originals (see Task 63).

**PR #33 — Promote vocab-swap to primary vocabulary mechanism.** New `vocab_swap` pipeline stage: after `auto_refine`, deterministically append the mapped statute vocabulary to the **narrow** arm's query (refine-then-swap, so the swap has the last word over the LLM refine) while the **broad** arm keeps the original query. Net effect: the controlling statute/advisory is promoted into the narrow top-k while the general framing is preserved — so the answer cites both. Validated on the camping-trailer/RV family (reliably cites §70.11(49) + the advisory + §70.111); anchors 7/7, no regressions; also reduced case-law-backfill context bloat. The #31 additive arm is retained dormant as a fallback.

---

### Task 58: Vocabulary-bridge (vocab-swap) for citizen-term → statute-term gaps

**Status:** RETIRED 2026-09-14. The `vocab_swap` and `vocab_injection` stages, their tests, and the `vocab_discovery` plumbing in `pipeline.py` / `phase_a.py` were deleted (commit f09e876) after document expansion (Task 65) made them redundant: post-reload harness with the map OFF = 33/42 vs 32/42 baseline, all recreational-home cases pass, camping trailer cites the 2026-04-29 advisory. The durable direction below (a DOR-authored glossary ingested as corpus content) still stands if new vocabulary gaps appear — that is the Sep 17 meeting ask.

*Original write-up (historical):*

Property tax is full of terms-of-art where everyday phrasing (e.g. "camping trailer", "mobile home") never embeds close to the controlling statute language ("recreational prefabricated home", §70.11(49)), so plain vector search lands on an adjacent-but-wrong section. `vocab_swap` bridges that gap with a small synonym map. The map is deliberately tiny (only the validated recreational-vehicle/manufactured-home rule today) because a *wrong* mapping steers the agent to a wrong answer. Growth should be **evidence-driven** — mine query logs for colloquial terms that missed content already in the corpus — and **legally validated per entry**. Candidate durable direction: a DOR-authored plain-language→statute glossary ingested as corpus content (DOR owns the correct mappings), rather than an ever-growing code table.

**Key files:** `backend/lambdas/agentic_retrieval/agent_tools/stages/vocab_swap.py` (stage + `VOCAB_RULES` map shared with the dormant `vocab_injection.py`), `agent_tools/pipeline.py`.

### Task 59: Mobile-home §70.11(49) reliability + legal-applicability question

**Status:** Open. The vocab-swap (#33) reliably fixes camping-trailer/RV phrasing but **mobile-home** phrasing still does not surface §70.11(49) (0/3 in a modal check). Two compounding causes: (1) mobile-home has more legitimately-competing statutes (§66.0435 permit fees, §70.17(3) real-property reclassification, RMH §70.111(19)(b)) that crowd the narrow arm, so the promotion doesn't stick; (2) an **SME question for DOR** — is §70.11(49) ("recreational prefabricated home") actually the controlling exemption for a *residential* mobile/manufactured home, or is it mainly for RVs/park models? The model's resistance may be partly correct. Needs DOR input; a stronger promotion may be warranted only if §70.11(49) is confirmed applicable.

### Task 60: DOR/SME content questions (client-side, not retrieval bugs)

**Status:** Open — carry to DOR. Several feedback items turned out to be content/SME rather than retrieval defects: (a) **full-value annual assessment** — the "maintenance assessment vs revaluation" minimum-requirement framing isn't cleanly in the ingested WPAM; need a DOR source to cite; (b) **residential grade scale** (Excellent→Poor) + example images — not in the corpus (proprietary cost-manual material); DOR to supply text + define the image use-case; (c) **Board-of-Review interpreter** reference — §70.47(8m) is a *hearing* waiver, not an interpreter provision, and §70.47 has no interpreter requirement, so the original answer was essentially correct; confirm the intended reference with DOR; (d) **manufacturing-appeal filing** — the 2026 guide's "paper only" statement is stale (My Tax Account now accepts electronic filing); DOR guide update.

### Task 61: Content-gap ingestion — treaty pub, Innovation Grant FAQ, assessor directory

**Status:** DONE (2026-09-10) for the two content docs; assrlist deliberately dropped. Added `gov_publications-napt-treaty-update` + `faq_pages-slf-ig` to `document_manifest.yaml`, then ran a scoped incremental cycle: category-filtered scrape → `extract --smart` → `embed --smart` → two `--source-filter` loads (one per doc). Verified live against the production graph — both previously-failing ingestion-gap queries now PASS (must_cite + LLM-judge rubric): the treaty answer cites the 1854 pub, the Innovation Grant answer draws the fuller requirement set from the FAQ instead of the statute's shorter list.

**assrlist.pdf NOT ingested (deliberate):** the assessor directory is a large municipality→contact table that embeds poorly as graph chunks, and the #25 defect (`2a4a9aed`, "who is the assessor for Town of Otsego…") was link *fabrication* — the bot invented a dead `revenue.wi.gov/Pages/SLF/assessors.aspx` URL and a courthouse phone number from its training prior, not from any retrieved chunk. That's a citation-link-integrity problem (Task 62), not a content gap; ingesting the directory would not reliably fix it.

**Note — scope expanded at scrape time:** category-scoping the scrape to `faq_pages` + `gov_publications` also detected 13 existing docs whose upstream content had drifted (uploaded as a side effect). Those were extracted + embedded (S3) but, per the "keep it small" decision, NOT loaded into the graph — only the 2 target docs were loaded. The 13 remain a latent extracted/embedded-ahead-of-graph state (same class as Task 64).

### Task 62: Reliability — client-side answer truncation + citation-link integrity

**Status:** Open. A response reported as "cut off" was verified against server logs to have streamed and persisted in full — so it's a **frontend** stream/render issue, not the Lambda: add a delivered-fully signal + client reconcile. Separately, intermittent citation-link bugs (a statute citation link resolving to the wrong chapter doc; a rendered `doc:` link to a document that wasn't actually retrieved) — add a citation-resolves-to-retrieved-doc validation pass in the answer path.

**Concrete case — #25 (`2a4a9aed`), "who is the assessor for Town of Otsego in columbia county wi":** the bot fabricated a dead `revenue.wi.gov/Pages/SLF/assessors.aspx` link **and** a Columbia County phone number, neither of which appears in any retrieved chunk (model-prior hallucination — the phone is the real Portage courthouse number from training data). DOR asked "where is it grabbing that contact info?" — a corpus grep to confirm it's sourced from *no* ingested doc, plus the link-resolution guard above, is the fix. Ingesting `assrlist.pdf` was considered as a "content" fix and rejected (see Task 61) — it wouldn't stop the model from inventing a URL it was never handed.

### Task 63: Confidentiality follow-up — residual production queryIds

**Status:** Open (reduced). PR #32 replaced production queryIds and feedback framing in the two test YAMLs; `docs/tasks.md` has now also been scrubbed of its legacy production queryIds (replaced with neutral descriptors). Both scrubs are HEAD-only. Remaining decision: whether a git-history purge (`git filter-repo` + force-push) is warranted to remove the originals from history in this public repo.

---

### Task 64: Reconcile stale-embedding backlog (~1166 docs)

**Status:** DONE 2026-09-14. Loader fixes shipped on `fix/ingestion-metadata-integrity` (extracted/ metadata overlay, embed `--smart` metadata-only refresh, Phase 2 case-law title guard, Phase 10 integrity assertion, `ops/purge_dedup_losers.py` + `dedup/losers.json` skip). Purge applied to S3 (63 losers), then a full after-hours reload 2026-09-14 19:06–19:17 PT: Phase 10 clean (doubled titles 0, orphan chunks 0), CaseLaw 1202→1146, the 2 corrupt and 3 ghost ids gone, statutes-70 at 382 chunks. Post-reload harness 32–33/42 vs 32/42 baseline; recall probe doc MRR 0.52→0.63 vs the pre-expansion baseline.

*Original write-up (historical):* surfaced 2026-09-10 during the Task 61 ingest. `embed --smart` reported `Smart mode: 1166/2364 documents have stale embeddings` — i.e. ~1166 docs whose `extracted/{doc_id}.json` is newer than their `embedded/{doc_id}.json`. A prior extract run (likely the corpus refresh) updated extractions that were never re-embedded, so the graph vectors/chunks for those docs lag the latest extraction.

**Not harmful** — retrieval is internally consistent on the older vectors — but it means extraction-side improvements haven't propagated to ~half the corpus. The Task 61 embed run wrote fresh S3 `embedded/` for the stale docs it processed, but only the 2 target docs were **loaded** into the graph, so the graph side of the backlog is unchanged.

**Fix:** a deliberate `embed --smart` (flush all stale to S3) followed by a **full load** (re-upserts ~9.6k vectors + reloads changed chunks, ~30–45 min, broad graph churn). Schedule it as its own pass with a before/after graph-regression baseline — not a drive-by during a small ingest. Worth first spot-checking a sample of the 1166 to confirm the extraction deltas are meaningful (real chunking/heading improvements) vs. volatile no-ops before paying for the full reload.

---

---

### Task 65: Document expansion (aliases in the embed input) + statute subsection split

**Why:** Retrieval misses happen when a user's everyday words ("camping trailer", "mobile home") never embed near the controlling legal term ("recreational prefabricated structure", § 70.11(49)). Measured 2026-09-11 with a direct Neptune rank probe: for "What exemptions can apply to a camping trailer?" the 2026-04-29 advisory was not in the top 60 and the § 70.11(49) statute chunk was not in the top 60 for ANY phrasing, including one that literally named the subsection. Two causes: (1) vocabulary gap, (2) the statute chunker packs subsections (45)–(49) into one 3,165-char chunk so no single-topic vector exists. Re-chunking alone barely moves the distance (1.196 → 1.176); prepending four plain-language questions moved the statute chunk from outside the top 60 to tied with the best competitor (1.224 → 0.852). The vocab-swap map (Task 58) is a query-side patch with a precision problem (fires on the term, not the intent: 4 of 15 matching production queries were not exemption questions) and requires an engineer to maintain; this is the durable replacement.

**Mechanism:** at extract time, one Nova 2 Lite call per chunk produces 2–3 plain-language questions the chunk answers plus everyday synonyms for its terms of art, cached per chunk-content hash under `aliases/`. Gold tester queries (rated up, or negative feedback naming the missing source) are attached to the chunk they should hit. At embed time the input string is `title > heading > subheading`, the gold queries, the generated questions, the synonyms, then the chunk text. Stored chunk text and answer context are unchanged; only the vector moves. Mechanical guard: an alias is dropped unless its legal term appears verbatim in the passage. Statute sections with ≥2 top-level numbered subsections are split one subsection per chunk (merged up to ~1,200 chars) so each vector is about one thing.

**Evaluation:** `tools/ingestion/ops/build_recall_eval.py` mines gold rows into `tests/recall_eval_queries.yaml`; `run_recall_probe.py` measures rank of the expected doc/chunk in raw vector search against any graph id (recall@10/30, MRR), baseline vs after. End-to-end: `run_graph_regression.py` with `NEPTUNE_GRAPH_ID` pointed at a staging graph, plus the LLM judge and the anchor set. Staging: every pipeline phase takes `--cache-prefix staging/` so production `extracted/` / `embedded/` are untouched; a separate Neptune graph is loaded from the staging caches; promotion is flipping the Lambda's graph id (blue/green), rollback is flipping it back.

**Not doing:** chunk attributes for retrieval (Neptune has no lexical index; attribute matching would recreate the term-list problem). Aliases are not stored on graph nodes in this pass.

**Key files:** `tools/ingestion/lib/aliases.py`, `extract.py` (`--aliases`, `--aliases-only`, `--cache-prefix`), `embed.py` (`--embed-input enriched`), `load.py` (`--cache-prefix`), `chunking/pdfChunker.py` (subsection split), `ops/attach_gold_queries.py`, `ops/build_recall_eval.py`, `ops/run_recall_probe.py`.


### Decision: agent model stays Sonnet 4.6 — Sonnet 5 evaluated and rejected (2026-09-14)

**Test:** full 42-case regression harness (`ops/run_graph_regression.py`) on the production graph, judge unchanged, `AGENTIC_MODEL_ID=us.anthropic.claude-sonnet-5` at `AGENTIC_EFFORT=medium`. Baseline = production Sonnet 4.6 the same day.

**Result:** 28/42 vs 32/42. Sonnet 5 used 257 research turns to 4.6's 442 and answered ~25% faster, but the savings came from stopping early: it lost § 70.11(49) on all three mobile-home / camping-trailer cases (the exact regression Task 65 fixed), plus § 70.46, § 70.56, § 70.995 and the three-factor test on other cases. It gained 5 cases (appeal-assessed-value, clerk-correct-roll-error, appeal-land-classification, fc-fp-churches, hire-assessor) — those are prompt-weakness hints for 4.6, not model wins. Phase-A token spend was only ~15% lower because most input is cache reads.

**Why not pursue it:** agent LLM cost is ~$0.17/query, negligible against the fixed ~$3k/month infra; the bot's value is thoroughness; testing closes 2026-09-24 and a model swap mid-window would force a prompt re-tune and harness re-baseline. Revisit only with a high-effort run and a prompt pass, and only if per-token price is materially lower.

**Kept:** model-aware sampling in `streaming/bedrock.py` (Sonnet 5 / Opus 5 reject `temperature`; effort via `output_config`), so a future swap is an env-var change. Artifacts: `~/Work/DxHub/wisdor/feedback-analysis/logs-2026-09-14/graph_regression_after_sonnet5.json` and `harness_sonnet5_compare.log`.

### Task 69: Content-gap ingests 2026-09-15 — WPAM Volume 2 + Lottery Credit forms page

**Status:** DONE 2026-09-15 (branch `feat/wpam-vol2-lottery-ingest`).

**What:** (1) **WPAM Volume 2 – Residential, Apartments and Agricultural (2026, DOR's redacted public edition, `wpamvol2.pdf`)** — 348 pages / 412 chunks. Carries the residential quality-grade scale (AA–E with factors and per-grade specifications), example dwelling photos with captions, agricultural building specs (prices redacted), and appraisal/architectural term glossaries. Closes Task 60(b) "residential grade scale" which we had assumed was proprietary. (2) **Lottery and Gaming Credit forms page** (`Pages/Form/lottery-home.aspx`: LC-100, LC-400, LC-667 questionnaire, online portal) — tester 2026-09-14 "What is the LC-667" had no source.

**How Volume 2 is registered:** `wpam` category with an explicit year-less doc_id `wpam-volume-2-residential-apartments-agricultural` → WPAM framework / authority 5 / WPAM chunker, but `edition_year` is null so the per-heading edition dedup (`wpam_dedup.py`) and `wpam_latest_only` never treat it as a competing edition of Volume 1. Manifest entries now accept `title:` (Volume 2's opening pages don't name it; the LLM title was "Full Market Value and Traditional Approaches to Value"). WPAM chunker: heading = chapter **or section** — Volume 2 has no "Chapter N" lines and would otherwise collapse into one "Untitled" section (breaks list_sections / get_section).

**Verification (prod graph, 46-case harness incl. 4 new cases):** grade scale, Grade-D example (deep-links p.58), and the clear-height negative control all PASS; Volume 2 was not retrieved in any regressed case (5 flips re-ran as noise, 4/6 pass; `fc-fp-churches` was a rubric bug — the §70.11(4)(a) 10-acre limit is correct — fixed). LC-667 failed at first: the page is a forms table and Titan ranked it outside the top 60 for the bare phrasing; attached the tester's query with `ops/attach_gold_queries.py` (Task 65 mechanism) → rank 1 → PASS. Photos: not extracted; captions ("Bungalow, Grade D") merge into the adjacent chunk and citation cards deep-link to the page, which is how DOR's "image use-case" question is answered.

**Also shipped:** `scrape_documents.py --only DOC_ID` (single-document refresh with `--force`); `load.py` Phase 7 restricted to the loaded documents on `--source-filter` (single-doc load 15 min → 2.5 min from a laptop); ECR image rebuilt.

**Not in corpus / for DOR:** "clear height" (likely Volume 3 commercial, unpublished); *Prime Leather Finishing* (not on CourtListener as a Wisconsin case — probably a Tax Appeals Commission decision; need the cite).

### Task 70: Loose ends from the 2026-09-10 → 09-15 feedback review

**Status:** Open (small). Source: feedback investigation 2026-09-16 (22 rated: 15 up, 7 down/mid; no hallucinations, no wrong-doc citations) cross-checked against what shipped 09-11 → 09-15.

Already fixed and re-verified in the harness but **not yet re-rated by testers**: LC-667 (Task 69), residential grade scale (Task 69, Vol 2), mobile home / camping trailer (Task 65), assessment-date and hire-an-assessor classifier refusals (09-13), Native American property (Task 61), case-law card titles (Task 64). Ask testers to re-rate on Thursday.

Remaining, in priority order:
1. **Image requests should still deliver the text.** `gq-cape-cod-grades` ("pictures examples of cape cod with the various grades") still refuses as image-incapable and surfaces no grade material even though Volume 2 now has it and citation cards deep-link the photo pages. Prompt tweak in `agenticRetrieval`: on an image/picture request, say it can't display images, then answer the underlying content question and link the WPAM Volume 2 example pages.
2. **Name-a-body scoping.** "Can the BOA increase an assessment…" got a BOR-first dual-track answer. Prompt tweak: when the user names a specific body (BOA / BOR / TAC), lead with that body; mention the other only as a contrast.
3. **Classifier miss:** "What is the maximum number of Innovation Grant applications that a private entity can submit?" (09-15) was refused as out of scope although 8 sibling IG questions passed. Add a "private entity / transferee" example to the classifier's in-scope list in `config/model_configs.toml` and push with `tools/upload_model_configs.py`.
4. **Assessment-date golden case.** The 09-13 classifier fix for "what time of day on January 1…" (5 thumbs-down on 09-09) has no harness case; add one (stratum A) so it can't silently regress.
5. **DOR content:** *Prime Leather Finishing* citation (not a Wisconsin appellate case on CourtListener; likely Tax Appeals Commission); SL-405 window date conflict (news 2025-07-02 "through March 31, 2026" vs FAQ Phase 2 "March 31, 2027"); "clear height" (Volume 3, unpublished). All on the Sep 17 agenda.
6. Style only, no action: "modular offices" (correct but hedged), "does it matter if sustained" (verbose follow-up), text-rewording request (one clean rewrite would have served better).

### Task 71: Post-retrieval adequacy judge replaces the pre-loop scope gate (2026-09-18)

**Status:** DEPLOYED 2026-09-18 (flags `ADEQUACY_JUDGE_ENABLED=true`, `SCOPE_GATE_ENABLED=false` in `graphrag-messages-stack.ts`). Two-way door: flip both and redeploy to restore the classifier gate byte-for-byte.

**Why:** the pre-loop classifier refused questions that name a court case ("What is the Markarian case?", "Summarize Sausen v Town of Black Creek" — 6 refusals on 2026-09-16) because a case name carries no topic words and the classifier never sees the corpus. Decision with Scott/Darren: every query runs the research loop; a judge decides afterwards with the evidence in front of it. Time is lost only on genuinely out-of-scope questions.

**Mechanism:** `adequacy_judge.judge_answer_plan` (Haiku 4.5, ~5 s) reads the question, history, the agent's answer plan, and ALL retrieved chunks (cited ones labelled and first) and returns a `Finding` — verdict ANSWER / CLARIFY / DECLINE, what the evidence supports, what the plan claims without support, and for CLARIFY a clarification derived from the sources (axis, one question, 2–5 options + escape). The finding is injected into the Phase B context (`## RETRIEVAL FINDING`) and Phase B ALWAYS streams — one prose path; the judge never writes user text. Fallback answers (model refused/clarified in its own words, turn budget) become the plan under a finding so a DECLINE is two plain sentences. Clarification options are sent as the existing `choices` chips (no frontend change); the pending clarification is stored on the chat-history item and `resolve_pending_clarification` folds a chip click or short reply back into the original question deterministically. Prompts: `[adequacyJudge]` + a Retrieval Finding section in `[answerStream]`; `tools/upload_model_configs.py` now parses with `tomllib`.

**Judge rules that mattered in tuning:** DECLINE needs zero relevant evidence and the SUBJECT must be outside property tax (a named case/term/form that isn't in the corpus is an honest-no ANSWER); clarify is tested against the QUESTION, not the plan (a well-supported plan can still have silently picked one branch); a seeded decision flowchart resolves the fork (never clarify on a flowchart question); the form of a request (picture/diagram) is never grounds to decline.

**Evidence:** scope set (`--tags scope,guard`, 17 cases): judge-off 10/17 → judge-on 13/17, verdict match 15/17. Classic 42: 34 baseline → ~36 with the judge (flowchart questions back to ANSWER after the flowchart rule). Cost of the tuning loop ≈ $25 of Bedrock. Known flaky: `gq-scope-prime-leather-absent` (judge says DECLINE; the answer is still the correct honest-no), `gq-clarify-how-assessed` (borderline CLARIFY/ANSWER), `gq-boa-increase-named-body`.

**Follow-ups:** watch the CLARIFY rate in prod traces (`adequacy_judged` event) — 9/45 in the harness before the flowchart rule, ~5/45 after; if testers find chips intrusive, raise the bar in the judge prompt's step 3. The old `disambiguationClassifier` prompt and `PROPERTY_TYPE_CHOICES` remain for the gate-on path; delete once the judge has a week of prod traffic.

### Task 72: WPAM Volume 1 was 89% un-indexed — chunker TOC heuristic fix + full re-index (2026-09-18)

**Status:** PROMOTED 2026-09-18 19:39 CT. Re-indexed caches loaded into a fresh graph `g-svphgiu4k6` (Fargate, 128 m-NCU, Phase 10 clean, 42,093 chunks; WPAM 2026 = 1,308 chunks, Sausen on p. 822). Harness on the new graph, judge off: **37/42 vs 34/42** on the old graph, 0 regressions, 3 gains (incl. `gq-full-value-annual` — the maintenance-vs-revaluation DOR ask now answers from WPAM Ch. 4). WPAM cited in 30/45 cases vs 22. The Lambda now points at `g-svphgiu4k6` via the `neptuneGraphIdOverride` CDK context, pinned in `infra/cdk.json`; the CDK-owned graph `g-ndvl4j73v4` is the rollback (deploy with the context removed) — delete it after a week and re-point the CDK construct. Rollback caches at `rollback-20260918/`.

**Root cause:** `chunk_document_wpam` prepends "Chapter N …" to every chunk, and its `is_probably_toc` had the branch "starts with Chapter N AND contains any digit-dash-digit token → TOC". Nearly every Manual page carries a footer like "7-40" that survives into the chunk, so ordinary prose chunks were discarded; the bare "appendix"/"glossary" keyword branch dropped more. Verified four ways: 194/952 pages of the 2026 PDF touched by any chunk; 9/40 random-page sentences findable anywhere in the graph; 2011 edition 1,088 chunks vs 204 for 2012–2026; local re-run of the production extractor reproduced 2.55M → 368K chars. This is why Sausen (p. 823), the Native American material (pp. 692–694, 803–813), Markarian's Ch. 22 discussion, and all of Ch. 21 were missing on 2026-09-16. An earlier "80% missing" claim had been retracted because graph and extracted-cache chunk counts matched — they did; the loss was upstream of both.

**Fix:** the TOC test runs on body lines only, the Chapter+footer branch is gone, and a chunk is a TOC only on structural evidence (≥3 leader-dot lines, or a high share of short bare page-number lines). Unit tests in `tools/ingestion/tests/`. Fargate image must be rebuilt (done with this PR).
