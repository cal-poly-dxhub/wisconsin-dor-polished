# Task List

_Finished history (the PR #28-#33 sprint, Tasks 21/45/46/49/50/52/58/61/64/65) moved to
[docs/archive/tasks-2026-09.md](archive/tasks-2026-09.md) on 2026-09-19. This file is the
live roadmap and the open tasks, plus Tasks 69-76 as recent context._

## Roadmap as of 2026-09-19

The bot is done as a research problem: every tester-reported retrieval issue has a shipped,
harness-guarded fix, the Assessment Manual is fully indexed (Task 72), and the repo has been
through a three-PR cleanup (Task 76). What remains:

1. **Security pass — the only thing blocking a public move.** Handoff plan §3: a WebSocket
   `$connect` authorizer (there is none today), session-ownership checks on
   `send_message` / `get_session_history` / `feedback`, closing self-signup, and adopting
   the console-managed `Admins` Cognito group into CDK via `cdk import`. ~1 day.
2. **Handoff** (Task 66): SSO with Amy/Brad, cost sheet, runbook walkthrough. The cost
   sheet's standing facts: Neptune 32 m-NCU is the floor (16 rejected again on a **fresh**
   graph 2026-09-18, so it is corpus size, not reload bloat); the lever to try for 16 is
   dropping the 15 non-current WPAM editions, which costs nothing in answer quality because
   retrieval already filters to the current edition.
3. **DOR-dependent items.** Still with DOR: the BOR interpreter reference (Task 60c —
   `gq-bor-interpreter-waiver` stays red by design) and mobile homes vs §70.11(49)
   (Task 59). Closed by DOR: Prime Leather, "clear height", the plain-language glossary,
   manufacturing-appeal filing.
4. **`gq-full-value-annual` is a ranking miss, not a content gap** (Task 73). The
   maintenance-vs-revaluation material IS in the graph (WPAM Ch. 4, pp. 59-61) but vector
   search prefers the §70.05(5) compliance chunks in Ch. 6. Manuals are not alias-enriched
   by design, so the gold-query lever does not apply. Parked unless DOR retests and objects.
   It is also the one stable delta between the recent harness runs.
5. **Phase 3 hierarchy evaluation.** `PART_OF` is written on every load and queried at
   retrieval time, but nothing measures whether statute hierarchy traversal actually
   improves an answer. Worth an evaluation before anyone invests further in graph shape.
6. **Dead extraction fields.** `extract.py` still writes `topics` and `implements_refs`
   into every extracted JSON and `load.py` still carries them in `DOC_METADATA_KEYS`, but
   no load phase reads them into the graph and there are no `Topic` nodes or `COVERS_TOPIC`
   / `IMPLEMENTS` edges. Decide: drop the fields, or wire them up.
7. **Prompt compression** (Tasks 43, 44) — still not started, still optional.

**Done and no longer on the list:**

- **Adequacy judge (Task 71) is ON in prod** since 2026-09-18, with `SCOPE_GATE_ENABLED=false`.
  It owns scope and clarification. Rollback is flipping the two CDK flags.
- **The pre-loop query classifier is legacy and OFF.** `ENABLE_DISAMBIGUATION` and
  `ENABLE_TOPIC_SHIFT` default false. `disambiguation.py`, the `disambiguationClassifier`
  prompt and `PROPERTY_TYPE_CHOICES` are retained only for that rollback path; delete them
  once the judge has a longer run of production traffic.
- **The old CDK-owned graph `g-ndvl4j73v4` was deleted on 2026-09-19.** `g-svphgiu4k6` is
  live, pinned by the `neptuneGraphId` context in `infra/cdk.json`, and is not a CDK
  resource at all (`infra/README.md` has the blue/green procedure).
- **Repo cleanup PR A, PR B and PR C are merged** (Task 76), and the documentation pass
  that followed them is done.

**Standing decisions:** Sonnet 5 rejected (stops researching early); the vocab-swap map
retired, replaced by document expansion; a DOR-authored plain-language glossary is NOT
being pursued (document expansion already does it at ingest with no DOR maintenance).

## Open tasks

| # | Task | Status |
|---|------|--------|
| 43 | Prompt rewrite: compress FRAMEWORK APPLICABILITY section | Not started — still a verbose 9-tier listing |
| 44 | Prompt rewrite: compress CITATION RULES section | Not started — still ~18 rules |
| 47 | Route case-law / flat-structure docs straight to `search_document` | Not started — tool descriptions still steer to `list_sections` / `get_section` first |
| 48 | Investigate the WPAM `get_section` gap — agents re-search doc-globally after `get_section` on the same chapter | Open investigation; no findings recorded |
| 51 | Classifier accuracy + follow-up logic | **Effectively moot.** The classifier it tunes is legacy and off; the judge owns scope now. Keep only as the record of what the gate did, in case it is ever restored |
| 59 | Mobile-home §70.11(49) reliability + the legal-applicability question | Open — the legal half is an SME question for DOR |
| 60 | DOR/SME content questions | Open — (a) and (b) resolved by Tasks 72/69; (c) still with DOR; (d) dropped |
| 62 | Reliability — client-side answer truncation + citation-link integrity | Open. Truncation is a frontend stream/render issue (the server sends the full answer). Link fabrication needs a resolves-to-retrieved-doc guard. Note: statute *page* links were fixed separately in Task 75 (`link_repair`) |
| 63 | Confidentiality — the history-purge decision | Open. HEAD is clean; the decision is whether to rewrite the repo's history before it moves. Folded into Task 66 |
| 66 | Handoff engineering — move to DOR's AWS account | Plan written: `docs/handoff-plan.md`. Security items in item 1 of the roadmap must land first |
| — | Phase 3 hierarchy evaluation | Not started (roadmap item 5) |
| — | Drop or wire up `topics` / `implements_refs` | Not started (roadmap item 6) |

## Done

Full write-ups for the 2026-09 closures are in
[docs/archive/tasks-2026-09.md](archive/tasks-2026-09.md). Recent work with its detail still
in this file: Tasks 69-76 below, plus the Sonnet 5 decision.

| # | Task |
|---|------|
| 1 | Disambiguate generic queries before full retrieval (superseded by Task 71) |
| 2 | Fixing linking issues |
| 3 | Tune model tone — reduce overconfident statements |
| 4 | Replace Step Function with direct Lambda invoke |
| 5 | Replace LLM classification with structural parsers |
| 6 | Reduce PDF chunk size for consistency and precision |
| 7 | Refactor agentic retrieval Lambda |
| 8 | Add boilerplate stripping before chunking |
| 9 | Reduce topic clustering batch size |
| 10 | Externalize prompts from Lambda code |
| 11 | Add managed compute for ingestion (Fargate) |
| 12 | Fix WebSocket streaming hang on background tabs |
| 13 | Harden authority hierarchy enforcement (authority-aware re-ranking) |
| 14 | Improve case law discovery in the vector_search backfill arms |
| 15 | Add settings modal with detailed trace toggle |
| 16 | Support URL-based session routing and preserve "new chat" state on reload |
| 17 | Handle multipart queries (split or unified answering strategy) |
| 18 | Show traversed sources in UI during agentic retrieval |
| 19 | Fix train-of-thought flicker on sidebar session hover |
| 20 | Add user persona setting (government worker vs. citizen) |
| 21 | z-score normalization on `search_document` — investigated, declined (archived) |
| 22 | Apply over-fetch multiplier when target_wpam_year is set |
| 23 | Strip WPAM running headers from chunk text |
| 24 | Multi-citation source cards (aggregate inline citations per parent doc) |
| 25 | Fix WPAM 2025 garbled table chunks and heading metadata |
| 26 | Admin ingestion page — ingest documents via URL from the UI |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags |
| 28 | WPAM 2019 heading loss — boilerplate stripper kept the TOC copy |
| 29 | Enable prompt caching for agentic retrieval (switch to invoke_model) |
| 30 | `get_section` chunk grid visualization in the trace UI |
| 32 | Show trimmed section page index in the answer-synthesis trace card |
| 35 | Graph wiring overhaul — stubs as routing nodes, not dead ends |
| 36 | Full corpus refresh — scrape, ingest missing docs, reingest stale content |
| 38 | Restructure `tools/` — consolidate the ingestion pipeline |
| 39 | Discover and ingest 2026 news pages |
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing |
| 41 | Fix statute citation page numbers, card titles, chunker page-header pollution |
| 42 | Markarian hierarchy query fails to ground `statutes-70` (turn-budget exhaustion) |
| 45 | Docket-keyed case-law dedup — one-time pass applied (archived) |
| 46 | Case-law title backfill (archived; durable fix landed in Task 64) |
| 49 | Scholar citation-to-text mis-assignment purge (archived) |
| 50 | Rich feedback phase 2 — render `richFeedback` in the admin activity dashboard (archived) |
| 52 | Subsection auto-backfill (C1) — tabled, then superseded (archived) |
| 58 | Vocabulary bridge (vocab-swap) — retired 2026-09-14 (archived) |
| 61 | Content-gap ingestion — treaty pub + Innovation Grant FAQ (archived) |
| 64 | Reconcile the stale-embedding backlog (archived) |
| 65 | Document expansion — aliases in the embed input (archived; now standing behavior) |
| 67 | Annual-refresh runbook for DOR — `docs/annual-refresh-runbook.md` |
| 68 | DOR meeting 09-17 agenda (client material, not in repo) |
| 69 | WPAM Volume 2 + Lottery Credit forms page ingests |
| 71 | Post-retrieval adequacy judge replaces the pre-loop scope gate |
| 72 | WPAM Volume 1 was 89% un-indexed — chunker TOC fix + full re-index |
| 74 | Admin pages — access audit, Chunks page overhaul, Canvas refresh |
| 75 | Answer UX — clarification block, source-card grouping, admin page gate |
| 76 | Repo cleanup — PR A / PR B / PR C, and the documentation pass |

---

## Task Details

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

### Task 47: Route case-law / flat-structure docs straight to search_document

**Context (from the Task 21 tool-usage audit, 2026-08-13 — full write-up in [docs/archive/tasks-2026-09.md](archive/tasks-2026-09.md#task-21)):** `search_document` is highly effective in practice — the target doc ended up **cited in 21 of 22 calls (95%)**, so it is NOT the "hail-mary" it reads like. But agents waste turns reaching it for flat-structured docs.

**Finding:** Case-law docs have **`distinct_headings == chunk_count`** (verified in graph: `case-law-405-wis-2d-616` = 17 chunks / 17 headings; `case-law-2025-wi-app-43` = 7/7). Every chunk is its own "heading," so `list_sections`/`get_section` are structurally useless there — there is no chapter hierarchy to navigate. Short gov-pubs are similar. Yet the tool descriptions steer the agent toward `list_sections` → `get_section` first ("consider list_sections + get_section for multi-chapter documents"), so on case law the agent burns a `get_document` or `list_sections` hop before correctly falling back to `search_document`.

**Proposed:** In `agent_tools/definitions.py`, tell the agent to go **straight to `search_document`** (or `fetch_case_opinion` when the full opinion is needed) for case-law docs and other flat/short documents — skip `list_sections`/`get_section`, which only pay off for genuinely multi-chapter docs (WPAM, large guides, multi-section statutes). Optionally detect flat structure server-side (heading count ≈ chunk count) and hint it back in the tool result.

**Validation:** Re-run the dark-store / Lowe's case-law queries; confirm the agent reaches case-law content in one hop with no wasted `list_sections`/`get_document` call, and turns-used drops.

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools/definitions.py` — `search_document` / `list_sections` / `get_section` descriptions
- `config/model_configs.toml` — `agenticRetrieval` prompt, if routing guidance lives there too

---

### Task 48: Investigate WPAM get_section gap — agents re-search doc-globally after get_section

**Context (from the Task 21 tool-usage audit, 2026-08-13 — full write-up in [docs/archive/tasks-2026-09.md](archive/tasks-2026-09.md#task-21)):** The single biggest `search_document` usage pattern — **11 of 22 calls** — was the agent running `search_document` on a WPAM doc where it had *already* pulled a section via `get_section` on the same chapter in a prior turn.

**Finding:** The dominant example is the "dark store / assessor should avoid distressed sales" query family: agent does `get_section(WPAM, "Chapter 13 Commercial Valuation")`, then next turn fires `search_document(WPAM, "assessor should avoid ... vacant dark distressed ...")`. The doc gets cited either way, so it *works* — but it means heading-based navigation didn't surface the specific passage on the first hop, forcing a doc-global re-search that costs an extra turn and re-runs the full 800-chunk over-fetch.

**Hypothesis:** the target passage spans a heading boundary, or lives under a subheading that `get_section`'s ranking didn't surface. Same territory as Task 27 (sparse WPAM subheadings) and Task 30 (`get_section` z-score ranking) — worth checking whether the "assessor should avoid distressed sales" text is chunked under the chapter heading the agent picked, or under a sibling/subheading.

**Proposed:** Trace one dark-store query end-to-end; inspect which WPAM chunk carries the "avoid distressed sales" language and under what `heading`/`subheading`; determine why `get_section` on Chapter 13 didn't return it. Fix may be chunk-heading metadata (Task 27 family) or `get_section` ranking, not a `search_document` change.

**Key files:**
- `backend/lambdas/agentic_retrieval/agent_tools/executor.py` — `get_section` (~line 498), `_rank_chunks_by_relevance`
- `backend/lambdas/agentic_retrieval/graph/neptune_client.py` — `get_section_chunks_with_embeddings`, `list_document_sections`
- `tools/ingestion/chunking/` — WPAM chunk heading/subheading assignment

---

### Task 51: Disambiguation follow-up logic + classifier accuracy — SUPERSEDED (2026-09-18)

> **Read this as a record, not a plan.** The pre-loop classifier this task tunes is legacy
> and OFF in production: `ENABLE_DISAMBIGUATION` and `ENABLE_TOPIC_SHIFT` default false and
> `SCOPE_GATE_ENABLED=false`. Scope and clarification are owned by the post-retrieval
> adequacy judge (Task 71), which sees the corpus before it decides. Everything below
> describes the gate as it behaved when it was on, and is the spec to restore if the flags
> are ever flipped back. The DOR-validated clarify list, if it ever arrives, is now input
> to the judge prompt's step 3, not to `disambiguationClassifier`.

**Status (historical):** Core follow-up/topic-shift logic SHIPPED and deployed (us-east-1). Classifier accuracy tuning was paused pending a validated list of clarify-worthy questions back from Wisconsin DOR.

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

### Task 59: Mobile-home §70.11(49) reliability + legal-applicability question

**Status:** Open. Camping-trailer / RV phrasing is reliably fixed (originally by the since-retired vocab-swap map, now by document expansion — Task 65), but **mobile-home** phrasing still does not reliably surface §70.11(49); `gq-mobile-home-exemptions` omitted it again on 2026-09-18. Two compounding causes: (1) mobile-home has more legitimately-competing statutes (§66.0435 permit fees, §70.17(3) real-property reclassification, RMH §70.111(19)(b)) that crowd the narrow arm, so the promotion doesn't stick; (2) an **SME question for DOR** — is §70.11(49) ("recreational prefabricated home") actually the controlling exemption for a *residential* mobile/manufactured home, or is it mainly for RVs/park models? The model's resistance may be partly correct. Needs DOR input; a stronger promotion may be warranted only if §70.11(49) is confirmed applicable.

### Task 60: DOR/SME content questions (client-side, not retrieval bugs)

**Status:** Updated 2026-09-18 after the DOR meeting — (a) retest on the re-indexed graph (Task 72 may have surfaced it); (b) SUPERSEDED: the AA–E quality grades with cost factors (p. 27) and "Selecting the proper quality grade" (p. 58) ARE in WPAM Volume 2 (Task 69); only the per-grade example photographs are unserved (raster images, no image pipeline) — retest the text; (c) still open with DOR; (d) dropped — DOR guide issue, not a bot concern. Original notes: (a) **full-value annual assessment** — the "maintenance assessment vs revaluation" minimum-requirement framing isn't cleanly in the ingested WPAM; need a DOR source to cite; (b) **residential grade scale** (Excellent→Poor) + example images — not in the corpus (proprietary cost-manual material); DOR to supply text + define the image use-case; (c) **Board-of-Review interpreter** reference — §70.47(8m) is a *hearing* waiver, not an interpreter provision, and §70.47 has no interpreter requirement, so the original answer was essentially correct; confirm the intended reference with DOR; (d) **manufacturing-appeal filing** — the 2026 guide's "paper only" statement is stale (My Tax Account now accepts electronic filing); DOR guide update.

### Task 62: Reliability — client-side answer truncation + citation-link integrity

**Status:** Open. A response reported as "cut off" was verified against server logs to have streamed and persisted in full — so it's a **frontend** stream/render issue, not the Lambda: add a delivered-fully signal + client reconcile. Separately, intermittent citation-link bugs (a statute citation link resolving to the wrong chapter doc; a rendered `doc:` link to a document that wasn't actually retrieved) — add a citation-resolves-to-retrieved-doc validation pass in the answer path.

**Concrete case — #25 (`2a4a9aed`), "who is the assessor for Town of Otsego in columbia county wi":** the bot fabricated a dead `revenue.wi.gov/Pages/SLF/assessors.aspx` link **and** a Columbia County phone number, neither of which appears in any retrieved chunk (model-prior hallucination — the phone is the real Portage courthouse number from training data). DOR asked "where is it grabbing that contact info?" — a corpus grep to confirm it's sourced from *no* ingested doc, plus the link-resolution guard above, is the fix. Ingesting `assrlist.pdf` was considered as a "content" fix and rejected (see Task 61) — it wouldn't stop the model from inventing a URL it was never handed.

### Task 63: Confidentiality follow-up — residual production queryIds

**Status:** Open (reduced). PR #32 replaced production queryIds and feedback framing in the two test YAMLs; `docs/tasks.md` has now also been scrubbed of its legacy production queryIds (replaced with neutral descriptors). Both scrubs are HEAD-only. Remaining decision: whether a git-history purge (`git filter-repo` + force-push) is warranted to remove the originals from history in this public repo.

---

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
5. **DOR content:** RESOLVED 09-18 — Prime Leather and "clear height": DOR did not recognize either; treated as pass, DOR follows up with the testers. SL-405 dates: not a source conflict but a stale corpus copy of the instructions PDF — see Task 73.
6. Style only, no action: "modular offices" (correct but hedged), "does it matter if sustained" (verbose follow-up), text-rewording request (one clean rewrite would have served better).

### Task 71: Post-retrieval adequacy judge replaces the pre-loop scope gate (2026-09-18)

**Status update 2026-09-18 evening — ON in prod.** Added a required `question_omits_the_fact` schema field with a CLARIFY→ANSWER code guard (mirrors the DECLINE guard; never fired in the gate run — the judge reads any unstated sub-fact as omitted, which Isaac decided is fine: asking beats over-confidence). Step 3 split into 3a (does the question state the fact) / 3b (do the sources fork). The 'do not clarify when a flowchart is in play' rule was replaced with the opposite: the chart lays out the facts, the clarification collects them. Gate run `logs-2026-09-14/full63_judgeon_guard_2026-09-18.json`: classic 41/45 (baseline 40/45), scope 14/17 (10/17), ig-line8 pass, verdicts ANSWER 40 / CLARIFY 18 / DECLINE 5. Deployed 22:16 PT with `ADEQUACY_JUDGE_ENABLED=true`, `SCOPE_GATE_ENABLED=false`.

**Status:** DEPLOYED 2026-09-18 (flags `ADEQUACY_JUDGE_ENABLED=true`, `SCOPE_GATE_ENABLED=false` in `graphrag-messages-stack.ts`). Two-way door: flip both and redeploy to restore the classifier gate byte-for-byte.

**Why:** the pre-loop classifier refused questions that name a court case ("What is the Markarian case?", "Summarize Sausen v Town of Black Creek" — 6 refusals on 2026-09-16) because a case name carries no topic words and the classifier never sees the corpus. Decision with Scott/Darren: every query runs the research loop; a judge decides afterwards with the evidence in front of it. Time is lost only on genuinely out-of-scope questions.

**Mechanism:** `adequacy_judge.judge_answer_plan` (Haiku 4.5, ~5 s) reads the question, history, the agent's answer plan, and ALL retrieved chunks (cited ones labelled and first) and returns a `Finding` — verdict ANSWER / CLARIFY / DECLINE, what the evidence supports, what the plan claims without support, and for CLARIFY a clarification derived from the sources (axis, one question, 2–5 options + escape). The finding is injected into the Phase B context (`## RETRIEVAL FINDING`) and Phase B ALWAYS streams — one prose path; the judge never writes user text. Fallback answers (model refused/clarified in its own words, turn budget) become the plan under a finding so a DECLINE is two plain sentences. Clarification options are sent as the existing `choices` chips (no frontend change); the pending clarification is stored on the chat-history item and `resolve_pending_clarification` folds a chip click or short reply back into the original question deterministically. Prompts: `[adequacyJudge]` + a Retrieval Finding section in `[answerStream]`; `tools/upload_model_configs.py` now parses with `tomllib`.

**Judge rules that mattered in tuning:** DECLINE needs zero relevant evidence and the SUBJECT must be outside property tax (a named case/term/form that isn't in the corpus is an honest-no ANSWER); clarify is tested against the QUESTION, not the plan (a well-supported plan can still have silently picked one branch); a seeded decision flowchart resolves the fork (never clarify on a flowchart question); the form of a request (picture/diagram) is never grounds to decline.

**Evidence:** scope set (`--tags scope,guard`, 17 cases): judge-off 10/17 → judge-on 13/17, verdict match 15/17. Classic 42: 34 baseline → ~36 with the judge (flowchart questions back to ANSWER after the flowchart rule). Cost of the tuning loop ≈ $25 of Bedrock. Known flaky: `gq-scope-prime-leather-absent` (judge says DECLINE; the answer is still the correct honest-no), `gq-clarify-how-assessed` (borderline CLARIFY/ANSWER), `gq-boa-increase-named-body`.

**Follow-ups:** watch the CLARIFY rate in prod traces (`adequacy_judged` event) — 9/45 in the harness before the flowchart rule, ~5/45 after; if testers find chips intrusive, raise the bar in the judge prompt's step 3. The old `disambiguationClassifier` prompt and `PROPERTY_TYPE_CHOICES` remain for the gate-on path; delete once the judge has a week of prod traffic.

### Task 72: WPAM Volume 1 was 89% un-indexed — chunker TOC heuristic fix + full re-index (2026-09-18)

**Status:** PROMOTED 2026-09-18 19:39 CT. Re-indexed caches loaded into a fresh graph `g-svphgiu4k6` (Fargate, 128 m-NCU, Phase 10 clean, 42,093 chunks; WPAM 2026 = 1,308 chunks, Sausen on p. 822). Harness on the new graph, judge off: **37/42 vs 34/42** on the old graph, 0 regressions, 3 gains (incl. `gq-full-value-annual` — the maintenance-vs-revaluation DOR ask now answers from WPAM Ch. 4). WPAM cited in 30/45 cases vs 22. The Lambda points at `g-svphgiu4k6` via the `neptuneGraphId` CDK context in `infra/cdk.json`, which is now the single pin for the whole stack — the graph construct was removed from `graphrag-stack.ts`, and the former rollback graph `g-ndvl4j73v4` was deleted on 2026-09-19. Rollback caches remain at `rollback-20260918/`; the blue/green and rollback procedure is in `infra/README.md`.

**Root cause:** `chunk_document_wpam` prepends "Chapter N …" to every chunk, and its `is_probably_toc` had the branch "starts with Chapter N AND contains any digit-dash-digit token → TOC". Nearly every Manual page carries a footer like "7-40" that survives into the chunk, so ordinary prose chunks were discarded; the bare "appendix"/"glossary" keyword branch dropped more. Verified four ways: 194/952 pages of the 2026 PDF touched by any chunk; 9/40 random-page sentences findable anywhere in the graph; 2011 edition 1,088 chunks vs 204 for 2012–2026; local re-run of the production extractor reproduced 2.55M → 368K chars. This is why Sausen (p. 823), the Native American material (pp. 692–694, 803–813), Markarian's Ch. 22 discussion, and all of Ch. 21 were missing on 2026-09-16. An earlier "80% missing" claim had been retracted because graph and extracted-cache chunk counts matched — they did; the loss was upstream of both.

**Fix:** the TOC test runs on body lines only, the Chapter+footer branch is gone, and a chunk is a TOC only on structural evidence (≥3 leader-dot lines, or a high share of short bare page-number lines). Unit tests in `tools/ingestion/tests/`. Fargate image must be rebuilt (done with this PR).

### Task 73: Thread dispositions from the 2026-09-17 DOR meeting (Innovation Grant, alias gate, retests)

**Status:** Open (2026-09-18). Dispositions from Isaac's meeting with Scott and Darren, plus the follow-up investigation.

**Innovation Grant (query `8b37e3bd-8a15-4dc8-8065-3493b6aa264f`, 09-17, thumbs-down; DOR confirmed the answer was incomplete).** The tester asked whether an application submitted December 31, 2026 can carry a 2027 transfer date on Section A line 8. Correct answer: **no** — the FAQ (`faq_pages-slf-ig`) says "if the transfer effective date for your innovation plan is during a future calendar year, you must wait until that calendar year to apply," so a 2027 transfer is filed in Phase 2 (Jan 1 – Mar 31, 2027), not Phase 1 (Oct 1 – Dec 31, 2026); line 8 auto-fills the preceding calendar year and every cost baseline is that year's actuals, which are not complete on Dec 31, 2026. A transfer date after June 30, 2027 also forfeits the FY2027 payment. The bot said yes (bounded by June 30, 2027), missed the wait-until-that-year rule that was in the FAQ chunk it cited, and then warned about a "March 31, 2026 filing deadline." That deadline is round one: our copy of the Form SL-405 instructions (`form_instructions-ig-inst`, https://www.revenue.wi.gov/DORForms/ig-inst.pdf) was scraped before DOR republished it; the live PDF now states the Phase 1 / Phase 2 windows and matches the FAQ. The 2025-07-02 COTVC news page ("through March 31, 2026") is round-one history and is fine as dated. Round one closed and DOR paid the first awards June 26, 2026 (press release `https://www.revenue.wi.gov/Pages/News/2026/First-Round-Innovation-Grant-Awards.pdf`, not in the manifest).
- Fix: force-refresh `form_instructions-ig-inst` via `scrape_documents.py --only … --force`, then extract/embed/single-doc load (safe in business hours). Add harness case `ig-line8-future-year` (must cite the FAQ + instructions; must say wait for 2027 / Phase 2; must NOT mention a March 31, 2026 deadline). Optional: add the June 26, 2026 press release to the manifest. If the case still fails with current sources, the miss is Phase B attention on a cited chunk → prompt tweak, decided after the run.

**Plain-language glossary: NOT pursuing.** Document expansion (Task 65) already does this at ingest with no DOR maintenance. Measured from the alias cache on 09-18 (Nova 2 Lite, $0.30/M in, $2.50/M out): a full-corpus pass generated aliases for 22,662 chunks, 21.5M input / 1.55M output tokens ≈ $10.30, ~2h15m wall clock locally. Only statutes + admin rules + news (6,283 chunks, ≈ $2.70) are ever used by the embed (`include_doc_types`); the other 72% (9k case-law chunks, 4.9k WPAM, etc.) is generated and discarded. Content-hash caching means the annual refresh only pays for changed chunks.
- Fix: apply the same `include_doc_types` allowlist at extract time in `enrich_chunks_with_aliases` (or its caller) so a full pass costs ≈ $2.70 / ~40 min. Config-only behavior change; unit test.
- Done (2026-09-18): the gate is implemented — `EnrichPolicy` moved to `tools/ingestion/lib/aliases.py` and is now applied at extract as well as embed, so excluded chunks are never sent to Bedrock (counted as `skipped_policy` in the per-doc and run-end "Alias enrichment totals" logs); `--aliases-only` honors it too and leaves excluded docs unwritten.

**Results 09-18 (evening).** Innovation Grant: `form_instructions-ig-inst` force-refreshed (live PDF, Phase 1/2 windows), press release added as `news_pages-innovation-grant-first-round-awards-2026-06-26`, both single-doc loaded onto `g-svphgiu4k6` (Phase 10 clean). Harness case `ig-line8-future-year` PASSES judge-off: the bot now answers "No — wait until 2027 / Phase 2" citing the FAQ + instructions. No prompt change needed. Retest slice, judge off, live graph (`logs-2026-09-14/retest_judgeoff_2026-09-18.json`): 12/15. PASS: Sausen, Markarian, Native American (both cases), residential grade scale, Grade D example, LC-667, IG contract requirements, IG private-entity count, depreciation, highest-and-best-use, actual view. FAIL: (1) `gq-cape-cod-grades` only because its must_cite still named Volume 1 — fixed to Volume 2 (the answer cited Volume 2); (2) `gq-mobile-home-exemptions` — §70.11(49) omitted again (the Task 59 open question; flaky); (3) `gq-full-value-annual` — the bot answers the §70.05(5) five-year compliance window and cites WPAM Ch. 6 (p. 93); the maintenance-vs-revaluation material IS now in the graph (Ch. 4 pp. 59–61: Table 4-1 "annual maintenance responsibilities", "Annual Review/Maintenance" column) but vector search prefers the Ch. 6 compliance chunks. So Task 60(a) is no longer a content gap; it is a WPAM ranking miss on a manual chunk (manuals are not alias-enriched by design, so the gold-query lever does not apply). Park unless DOR retests and objects.

**Retests on the re-indexed graph (Task 72)** — run in the harness, judge off and on, and have DOR retest in the app to see the real chips: Sausen, Markarian, Native American property, maintenance-vs-revaluation (Task 60a), residential grade scale text (Task 60b — the AA–E grades and "Selecting the proper quality grade" are in Volume 2 pp. 27/58; example photos remain unserved), other Manual-heavy questions.

**Still with DOR:** BOR interpreter reference (Task 60c; `gq-bor-interpreter-waiver` stays red by design); mobile homes vs §70.11(49) (Task 59 — revisit, do not mark pass). **Closed:** Prime Leather and "clear height" (DOR did not recognize them; testers to be asked why they rated down), manufacturing-appeal filing (DOR guide update).

**Handoff notes:** Darren needs the graph sizing (8.0 GB on a fresh load = 42% of a 32 m-NCU graph; 16 rejected on a fresh graph, so 32 is the floor; dropping the 15 non-current WPAM editions is the lever). Git-history purge decision (Task 63) before the repo moves.

### Task 74: Admin pages — access audit, Chunks page overhaul, Canvas brought up to the current pipeline (2026-09-18)

**Access (audit; the client-side half was fixed in Task 75).** At the time of the audit, any signed-in Cognito user could open every `/admin/*` page (the layout checks only for a session; no group check, no middleware, self-signup on). The admin HTTP APIs are properly gated (`require_admin()` → `Admins` Cognito group, which exists only in the console, not in CDK). So a tester sees the admin chrome with empty lists. The Canvas live-query mode works for any signed-in user because the WebSocket has no authorizer — same gap as the pre-public security item.

**Since fixed:** Task 75 added the client-side group check (`hasAdminGroup` + `<ProtectedRoute requireAdmin>`), so `/admin/*` pages now require the `Admins` group on both sides. **Still open, both in the security pass:** the WebSocket `$connect` authorizer, and adopting the console-managed `Admins` group into CDK via `cdk import`.

**Chunks page (shipped):** reads `extracted/` from S3 (pre-embedding artifact, never Neptune). Was one multi-MB, double-JSON-encoded response with every chunk's text, swallowed errors, a fake `manifest.json` document, and a live DOM node per chunk. Now: `GET /admin/chunks/{docId}/index?offset&limit` (metadata only) + `GET /admin/chunks/{docId}/text?offset&limit` (text on demand), single-encoded Powertools responses, manifest filtered, windowed grid, debounced search, real 403/404/network error states, page deep-links into `source_url#page=N`, WPAM/statute heading grouping. The unauthenticated `api/local-chunks` Next routes (path-join on user input) were deleted. Chat API Lambda still parses the whole extracted JSON per request (256 MB); bump memory rather than caching if it OOMs on WPAM.

**Canvas (shipped):** panes added for get_flowchart / list_flowcharts (the router's turn-0 seed now renders), find_case_law, list_worksheets / get_worksheet, auto_refine, the scope-gate verdict, the adequacy judge (verdict, latency, chips from the `choices` message), a request/history header, and Phase B (streamed answer, resources, errors). `buildTurns` now materializes a tool_result with no matching tool_call (this, not "Coming soon", was why the flowchart seed and auto_refine vanished). Stale `clarify` / `refine_query` / `cite_documents` removed, dead corpus-manifest stub and unwired fixtures deleted, demo fixture regenerated by hand to cover the flowchart seed, judge CLARIFY with chips, a worksheet call and Phase B. `backend-tools.ts` mirrors the tool registry (14 tools after PR A removed `get_authority_chain` and `list_framework_docs`, plus `auto_refine` as a synthetic pane) with a test that fails when a tool has no pane. Backend follow-ups (not done): `tracing/emitter.py` `ALLOWED_METADATA_KEYS` lacks the worksheet keys, and `tracing/summaries.py` has no `find_case_law` / `list_flowcharts` branch; the judge Finding's prose (supported/unsupported/rationale) is not sent over the socket. The frontend test failures noted here (`initial-vector-search-pane.test.tsx`, `use-websocket-chat.test.tsx`) were fixed — `bun test src` is green as of 2026-09-19. Repo-root `eslint.config.ts` still does not load under ESLint 8; lint from `frontend/`.

### Task 75: Answer UX — clarification block, chip order, source-card grouping, no em dashes; admin page gate (2026-09-19)

Shipped with the judge ON after Isaac reviewed the live bot.
- **Clarification block.** The judge's question and chips now render as one lifted section (label = the clarification axis, e.g. "Property classification"; question; chips) between the answer and the sources, instead of the question as the last markdown line and chips below the source cards. Wire: `choices` message gained optional `question`, `axis`, `kind` (`clarification` | `disambiguation`); backend models + Zod + store updated together. The answerStream CLARIFY rule no longer asks the writer to restate the question. Not restored on session reload (choices never were); follow-up if wanted: return `clarification` from the history endpoint.
- **Source cards.** Tinted by document kind with a left accent and a small-caps header label (statute blue, case law violet, admin rule teal, WPAM green, gov pub / forms amber, IAAO/USPAP rose, FAQ/news neutral); provenance pills ("Semantic match", "Source", "Court opinion") only in dev-trace mode; grouped by authority (Statutes & Constitution → Case Law → Admin Rules → WPAM → Gov Pubs & Forms → FAQs & News → Other) with collapsible headers, all open at ≤6 cards else first two groups; uniform height, two-line titles, two citation rows + "+N more"; markdown emphasis stripped from case-law citation labels. Files: `components/documents/document-card/source-taxonomy.ts`, `source-group-grid.tsx`.
- **No em dashes.** 145 em dashes scrubbed from every prompt in `config/model_configs.toml`, a "no dashes as punctuation" writer rule added, and all four `_prompt_fallback.py` mirrors regenerated byte-identical from the TOML (they had drifted for months; `test_prompt.py` was pinning the stale mirror and was updated). Style slice, 12 cases judge-on: 81 → 2 em dashes in answers, pass results unchanged (9/12 both), verdicts unchanged on 11/12.
- **Admin page gate.** `/admin/*` layout requires the `Admins` Cognito group from the ID token (`hasAdminGroup` in auth-context, `<ProtectedRoute requireAdmin>`), showing an "Admin access required" panel otherwise. The group stays console-managed (2 members); adopt via `cdk import` in the security pass. Still open: WebSocket `$connect` authorizer + session ownership.
- **Statute inline links (09-19, after Isaac's review of query 8265d1af):** `[§ 74.37](doc:statutes-74)` with no page opened page 1 of the chapter PDF although Phase B had been shown "§ 74.37 -> page 9". Fixed three ways: `phase_b.statute_section_pages` (cached, covers cited chapters plus any known corpus chapter referenced in chunks or the plan; the old `>= 70` filter dropped ch. 19/60/61), `link_repair` fills `#page=N` deterministically (`kind="paged"`, never invents), and the frontend sends a page-less statute link that names a section to the legislature's per-section page (`document/statutes/74.37`). Note: docs.legis returns intermittent 503s under bursts; "sometimes it works" is that server, not our URLs.

### Task 76: Repo cleanup — PR A / PR B / PR C (2026-09-19)

**Status:** DONE. Three merged PRs plus a documentation pass, all held to the same gate:
the 63-case graph-regression harness must not lose ground.

**PR A — retire dead retrieval surface.** Removed the `get_authority_chain` and
`list_framework_docs` tools, the `clarify` pseudo-tool (executor + loop handling for a tool
that was never in `TOOL_DEFINITIONS`), and the `auto_enrichment` pipeline stage with its
`ENRICH_CAP_PER_DOC` / `ENRICH_CAP_PER_TYPE` knobs — it fetched neighbours into a context
field nothing read, costing up to 3 Neptune round-trips per search. Turned the pre-loop
classifier off by default, deleted dead loader and config paths, fixed the admin ingest doc
ids, removed the mock-chat and orphan frontend components, made the Textract fallback
fail-closed when `TEXTRACT_STAGING_BUCKET` is unset, and fixed the harness grader to grade
the clarification block the way the UI shows it (plus `--regrade` honoring `--out`).

**PR B — the graph pin.** `neptuneGraphId` in `infra/cdk.json` became the single pin for
the whole stack, the Neptune construct left `graphrag-stack.ts`, and synth now fails
outright if the pin is missing (no fallback). The old CDK-owned graph `g-ndvl4j73v4` was
deleted on 2026-09-19. Loader cleanup landed alongside: Phase 4 no longer writes
`HAS_SUBSECTION` (the `_parent_id` key it read was never set, so the edge list was always
empty) and Phase 9 no longer GCs orphan `Topic` nodes (nothing creates them). Also deleted:
`ops/delete_semantic_edges.py` and the unwired neighbor-doc citation-discovery helpers.

**PR C — this documentation pass.** Every markdown file re-grounded against the code:
the engineering guide rewritten as the single architecture reference (14 tools, the judge,
the flowchart router, the real edge list, the real retry numbers), finished history moved
to `docs/archive/tasks-2026-09.md`, and the stale names (`refine_query`,
`get_authority_chain`, `list_framework_docs`, `cite_documents`, `vocab_swap`,
`auto_enrichment`, `useGraphRAG`, `g-ndvl4j73v4`, Topic nodes, `IMPLEMENTS` /
`HAS_SUBSECTION` / `COVERS_TOPIC`, "9 sub-phases") removed from every live doc.

**The gate.** Two clean 63-case runs after the cleanup, both at **classic 39/45**, against
a Thursday pre-cleanup baseline of **41/45**. The only stable delta is
`gq-full-value-annual` (roadmap item 4 — a WPAM ranking miss, not a cleanup regression);
the rest of the gap is the known flakiness in `gq-prior-year-roll` and judge noise. Scope
slice: **15/17 and 13/17**. `gq-bor-interpreter-waiver` stays red by design — it is a DOR
content question, not a retrieval bug.

**Known follow-ups left behind, all code changes:** the frontend trace-metadata mirror
(`frontend/src/components/messages/trace-metadata.ts`) has 24 keys to the backend's 73, and
still lists `autoEnrichedCount` and `chainLength` from the two things PR A removed, under a
header comment pointing at the long-gone `packages/graphrag/.../main.py`;
`tracing/emitter.py` lacks the worksheet keys and `tracing/summaries.py` has no
`find_case_law` / `list_flowcharts` branch (Task 74); `extract.py` still writes `topics` and
`implements_refs` that nothing loads (roadmap item 6).

### Task 77: Post-meeting follow-ups 2026-09-24 (assessor-practices wording, Innovation Grant fair market compensation guide)

- **Assessment practices complaint (683fc136, thumbs-down 09-23).** The bot answered the § 70.85 value review instead of the § 73.09(7) Request for Review of Assessor's Practices (WPAM Ch. 2 pp. 42-43). Two sibling questions in the same hour were answered correctly. Client decision at the 09-24 meeting: treat as a query-wording issue (the bot hooked on "assessment" rather than "assessor"), not a retrieval change. No code change; a harness case is optional.
- **Innovation Grant: fair market compensation guide ingested.** 14 Innovation Grant questions from one DOR tester since 09-20; 3 thumbs-down. Two (fff17348 fringe benefits, 88e732b9 Adams County EMT wage) needed `https://www.revenue.wi.gov/DOR%20Publications/ig-fmc-factsheet.pdf`, which was not in the corpus. Added to the manifest as `gov_publications-ig-fmc-factsheet` (14 chunks; the 2024/2025 BLS county wage tables survive chunking with their year labels), single-doc loaded onto the live graph 09-24 13:38 PT. New harness cases `ig-fmc-fringe-benefits` and `ig-fmc-adams-emt-wage` both PASS: the bot now gives the highest-of-three rule with the 0.44 multiplier, and $22.45 from Table 3 (2025) at page 7. The third thumbs-down (3b36219a, one application covering both fire protection and EMS) is DOR policy not stated in any source: the tester's feedback says a single application is acceptable when the contract references more than one category even though the form's Line 4 takes one selection. Content ask for DOR: add that to the Innovation Grant FAQ so the bot can cite it.
