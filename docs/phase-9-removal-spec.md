# Spec: Remove Phase 9 (LLM Semantic Edges) from the GraphRAG Load Pipeline

**Status:** Proposed
**Author:** (drafted with Claude Code)
**Date:** 2026-07-16

## 1. Summary

Remove `phase_9_semantic_edges` — the LLM-classified document-to-document edge
layer (`RELATED_TO`, `SUPPLEMENTS`, `SUPERSEDES`, `CONFLICTS_WITH`) — from the
Neptune load pipeline. Delete the edges it produced from the live graph. Do
**not** replace it with anything.

This is a subtractive change. It removes the single largest recurring LLM cost
in ingestion and ~17.8% of graph edges, with no measurable impact on answer
quality per 90 days of production trace analysis.

## 2. Motivation (evidence-backed)

A 90-day analysis (2026-04-16 → 2026-07-16; 688 queries, 663 stitched agent
traces, 97 rated) produced the following findings:

- **Not traversed.** Over 90 days the agent explicitly requested a semantic
  edge type in only **28 queries**. In the most recent 30 days it requested them
  **zero** times — behavior converged to `CITES`/`IMPLEMENTS` only.
- **Not load-bearing.** A blast-radius trace (see §7, Step 0) found **no rated
  query** whose cited-document set depended on a semantic edge. The only query
  with any citation contact was an **unrated demo query** ("Are pipelines state
  or locally assessed?"), whose substantive answer came from vector-searched
  statutes (`statutes-document-76` / § 76.01); the semantic-edge docs were
  supplementary news pages that did not affect correctness.
- **Net-negative in retrieval.** The `RELATED_TO` layer (13,745 edges) drives
  high-degree fan-out on statute/WPAM nodes; auto-enrichment pulls 100+ neighbor
  pools of which a median of 0 are cited. It is the dominant context-noise
  source.
- **Incorrect.** ~1,800 LLM `SUPERSEDES` edges are frequently directionally
  wrong (e.g. WPAM 2011 "supersedes" 2024) and `edition_year` is only populated
  on 16 WPAM docs, so the classifier had no reliable version signal for the rest.
- **Redundant.** `SUPERSEDES`'s intended benefit (version-awareness) is already
  delivered by the existing WPAM dedup + prompting plumbing in the retrieval
  layer, which has kept WPAM editioning correct for several weeks.
- **Expensive.** Phase 9 is ~100% of the per-run LLM ingestion cost: **~$45 per
  full reingest** (1,251 Sonnet calls over 18,757 candidate pairs). All other
  phases are $0 (deterministic Neptune queries).

### What is explicitly NOT removed (the graph's actual value)

- `CITES` (24,014), including `Statute → CaseLaw` (4,192) — the only path by
  which case law becomes reachable (case nodes are metadata stubs, invisible to
  vector search). This is the highest-satisfaction slice (84% positive on the
  case-law path vs 58% baseline).
- `DEFINED_BY` (25,436) — resolves statute stubs to real chunks during the
  case-law hop.
- `EXTRACTED_FROM`, `PART_OF`, `BELONGS_TO`, `DERIVED_FROM` — structural,
  deterministic, free.
- The three deterministic case-law discovery paths in `vector_search`
  (text-citation, subsection two-hop, neighbor-citation) — untouched.

## 3. Scope

### In scope
- `tools/ingestion/load.py` — remove Phase 9 function + helpers, dispatch entry,
  renumber Phase 10 → Phase 9.
- One-shot Neptune cleanup script to delete existing semantic edges.
- Agent tool definitions + prompt — stop advertising the removed edge types.
- Auto-enrichment ranking — remove dead `_EDGE_PRIORITY` entries.
- Docs (`CLAUDE.md`, `docs/graphrag-engineering-guide.md`).
- Regression smoke-test harness (§7).

### Out of scope (future, needs its own investigation)
- Removing `Chunk → Statute` CITES (18,242 edges). Flagged as likely-redundant
  with vector search, but retained pending dedicated analysis — it is the
  scaffolding that makes statute nodes routable to case law.
- Removing dead `HAS_SUBSECTION` (0 edges) / `IMPLEMENTS` (0 edges) wiring.
- Any change to the retrieval-layer WPAM dedup logic.

## 4. Detailed changes

### 4.1 `tools/ingestion/load.py`
Delete:
- `phase_9_semantic_edges()`
- `_llm_classify_semantic_batch()`
- `_flush_semantic_edges()`
- `_extract_json_array()` (only used by the above — verify no other callers)
- `cosine_similarity()` (only used by the above — verify)
- Constants `PHASE_9_LLM_WORKERS`, `PHASE_9_ALLOWED_TYPES`
- Config reads `semantic_similarity_threshold`, `semantic_batch_size` usage

Dispatch table: remove the `(9, "Semantic Edges", ...)` entry; renumber
`(10, "Orphan Cleanup", ...)` → `(9, ...)` and rename `phase_10_cleanup` →
`phase_9_cleanup` (log string included) so the sequence stays contiguous 1–9.

Verify `bedrock` client import is still used elsewhere in `load.py`; if Phase 9
was the only consumer, remove the client + its config.

### 4.2 Neptune cleanup script
New `tools/ingestion/ops/delete_semantic_edges.py`. Deletes edges by type in
capped batches (mirror the `phase_9_cleanup` DETACH DELETE / LIMIT pattern to
avoid Neptune query-memory limits):

```
MATCH ()-[r:RELATED_TO|SUPPLEMENTS|SUPERSEDES|CONFLICTS_WITH]->()
WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted
```

Loop until `deleted == 0`. Dry-run flag that reports counts without deleting.
Expected removal: RELATED_TO 13,745 + SUPPLEMENTS 2,272 + SUPERSEDES 1,800 =
**17,817 edges**.

### 4.3 Agent tool definitions + prompt
- `backend/lambdas/agentic_retrieval/agent_tools/definitions.py`: in
  `get_neighbors` `edge_types` description, drop `SUPPLEMENTS, SUPERSEDES,
  CONFLICTS_WITH, RELATED_TO` and `HAS_SUBSECTION` from the advertised options;
  reword the tool description that mentions "SUPPLEMENTS, SUPERSEDES, is
  RELATED_TO ... newer guidance."
- `backend/lambdas/agentic_retrieval/_prompt_fallback.py` and
  `config/model_configs.toml` (`agenticRetrieval` prompt): remove references to
  following SUPERSEDES edges / semantic relationships. **Any TOML prompt change
  must be pushed via `tools/upload_model_configs.py --only agenticRetrieval`.**

### 4.4 Auto-enrichment
`backend/lambdas/agentic_retrieval/agent_tools/executor.py`: remove
`RELATED_TO/SUPPLEMENTS/SUPERSEDES/CONFLICTS_WITH` entries from `_EDGE_PRIORITY`.
Unfiltered `get_neighbors` simply stops returning them once deleted; no logic
change needed beyond cleanup. Confirm no ranking path assumes their presence.

### 4.5 Docs
- `CLAUDE.md`: update the GraphRAG Data Model edge list — remove the "Semantic
  (LLM-classified, phase 11)" line; correct the phase count to 9.
- `docs/graphrag-engineering-guide.md`: same.

## 5. Rollout sequence

1. Land `load.py` + tool/prompt/doc edits (leave working tree unstaged for
   review per repo convention).
2. Capture regression baseline against **current production** (edges still live)
   — see §7. Baseline must be frozen before any graph mutation.
3. Deploy backend (`bun run bundle` + `cdk deploy`) and push the prompt TOML.
4. Run `delete_semantic_edges.py` against the live graph (`g-ndvl4j73v4`).
   This is the point of no return for the current graph; edges are rebuilt-free
   on the next reingest regardless.
5. Re-run the regression set; compare to baseline (§7 grading).
6. On green, done. Next scheduled reingest naturally omits Phase 9.

**Note:** Step 4 mutates the production graph directly. It is reversible only by
re-running a full load (which would re-create the edges only if Phase 9 code were
restored). Keep the removal PR revert-able through step 3; step 4 is gated on
green smoke tests.

## 6. Rollback
- Before step 4: revert the code PR; nothing in the graph changed.
- After step 4: to restore edges, re-add `phase_9_semantic_edges` and run a load
  (~$45, ~8 min). Given the evidence they are net-negative, rollback is unlikely.

## 7. Regression smoke tests (question-accuracy guardrail)

### Step 0 — Static blast-radius proof (DONE, pre-implementation)
Already executed against 90-day traces: of 28 semantic-edge-requesting queries,
zero rated queries had a cited-doc set dependent on a semantic edge. Only the
unrated pipeline demo query showed any citation contact, and its answer was
statute-grounded via vector search. Conclusion: cited-doc set is invariant under
removal for all rated traffic. This is the primary evidence; live tests below
are confirmation.

### Golden set (~18 queries, stratified by risk)
Stored as `tools/ingestion/tests/graph_regression_queries.yaml`. Each entry:
`{queryId, query, stratum, must_cite: [doc_id...], must_contain: [fact...],
notes}`.

| Stratum | Intent | queryIds |
|---|---|---|
| **A. Semantic-edge-dependent** (highest risk) | prove removal doesn't drop answers | `b861447d` (pipelines state/local — THE stress case), `42085b1d` (2026 ag guide), `77c2bab3` (zoning for ag classification), `3e648cf8` (TID tax increment) |
| **B. Case-law two-hop** (control — must be identical) | protect the value path | `8d5f49aa` (Markarian hierarchy), `a00bdbcc` (Goldberg v. BZA), `b4a29aca` (Markarian v. Cudahy), `a15bb8a3` 👍 (big-box valuation), `57b19285` 👍 (Native American exempt) |
| **C. Cross-doc synthesis** | WEAK-tier, relies on WPAM prose + enrichment | `c64709dc` (multi-turn imagery/reference comparison) |
| **D. Bread-and-butter** (collateral) | the 83% — must be stable | `41eee328` 👍 (levy limits), `9de278ac` 👎 (net new construction), `ef9e54de` 👍 (BOR 48-hr objection) |
| **E. WPAM version-sensitive** | confirm editioning plumbing (untouched) still fires | `df92f36e` (WPAM sales-comparison for ag land) + one substitute ag query (replace unavailable `32cd5c47`) |

### Grading (substance, not text-diff)
Answers are non-deterministic; grade on:
1. **Cited-doc overlap** — key authoritative docs in `must_cite` still present
   (exact, structured). Primary gate.
2. **Key-fact presence** — `must_contain` facts still asserted (e.g. "Lowe's"
   for big-box, "arm's-length" for 70.32, "Ch. 76" for pipelines). Regex.
3. **No hallucinated citations** — every cited `case-law-*` node must exist in
   the graph.
4. **Blind LLM-judge** — before/after answer pairs, order randomized, rated for
   semantic equivalence + correctness. Establish a variance floor first by
   running each query 2–3× on baseline; only flag deltas exceeding that floor.

### Pass criterion
- Strata B, D, E: **100%** cited-doc + key-fact retention (these do not depend on
  Phase 9; any change is a real regression to investigate).
- Stratum A: cited-doc set may shift only by dropping supplementary news-page
  docs; `must_contain` correctness facts must be **fully retained**. The pipeline
  query (`b861447d`) must still ground its answer in Ch. 76 / statutes-76.
- Stratum C: LLM-judge equivalence within variance floor.
- Zero hallucinated case citations across the whole set.

### Harness
New `tools/ingestion/ops/run_graph_regression.py`: reads the YAML, invokes the
retrieval path per query (baseline run writes `baseline.json`; post-change run
writes `after.json`), emits a comparison report (cited-doc diff, fact-presence,
judge verdicts). Reusable beyond this change as a general graph-regression gate.

## 8. Expected outcome
- Ingestion LLM cost per reingest: **~$45 → $0**.
- Graph edges: **99,898 → 82,081** (−17.8%).
- Retrieval: reduced context noise from high-degree semantic fan-out; case-law
  path and WPAM editioning unchanged.
- Answer quality: no regression on rated traffic (per Step 0); confirmed by §7.
