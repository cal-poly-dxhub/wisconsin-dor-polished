# Spec: Retarget `vector_search` Auto-Enrichment (Direction 1)

**Status:** Proposed
**Author:** (drafted with Claude Code)
**Date:** 2026-07-16
**Related:** `docs/phase-9-removal-spec.md` (same evidence base + smoke-test harness)

## 1. Summary

`vector_search` automatically fires `get_neighbors` on the top-3 parent
documents of every search and dumps up to **60 neighbor stubs** into the
model's tool result (and into the agent's `all_doc_ids` discovery set). Over 45
days this "auto-enrichment" is responsible for **84.8% of all discovered
documents at a ~5% cite rate** — it is the dominant source of context noise and
does not reduce turn count.

This spec **retargets** auto-enrichment so it stops flooding the model with
low-value structural/topical neighbors, while **preserving** the internal
case-law discovery pipeline that legitimately consumes the neighbor set. It is a
scoping/plumbing change, not a graph-schema change. No Neptune mutation.

**This is not a prompt rewrite (Direction 2) or a turn-0 retrieval bundle
(Direction 3).** Those are separate, sequenced follow-ups. This spec only
changes what `vector_search` returns and how enrichment neighbors are tagged.

## 2. Motivation (evidence-backed)

45-day production analysis (2026-06-01 → 2026-07-16, 469 completed queries with
`agent_loop_complete`):

- **Discovery is 85% enrichment fan-out.** Of all documents discovered per
  query, `graph-neighbor` = **19,439 (84.8%)** vs `vector-search` 2,508
  (10.9%). The `graph-neighbor` tag is populated *only* by the enrichment block
  (`phase_a.py:615-620`).
- **~5% cite rate.** Mean **96 docs discovered / query**, **5 cited / query**.
  The enrichment is adding ~90 docs of context that never get cited.
- **The cap is hit exactly.** A sampled `agent_loop_complete` shows
  `{"vector-search":13, "graph-neighbor":60, "fetched":6}` — 60 = the
  `3 docs × ENRICH_CAP_PER_DOC(20)` ceiling, confirming enrichment routinely
  saturates.
- **Enrichment doesn't reduce turns.** Queries where enrichment discovered ≥1
  doc average **4.4 turns**; queries with zero graph-neighbor docs average
  **4.1 turns**. Fan-out generates 88% of discovery volume and moves turn count
  by 0.3.
- **It enriches the wrong node type.** Enrichment calls
  `neptune.get_neighbors(doc_id)` with **no `edge_types` filter** on *document*
  nodes. Per the system prompt (steps 6 & 8), document nodes carry **no CITES
  edges** ("those edges were removed; only statute stubs and chunks have them").
  So the `_EDGE_PRIORITY` ranking that prefers CITES/IMPLEMENTS has nothing to
  prefer — it returns whatever structural/topical edges the doc node happens to
  have. The valuable authority traversal is model-driven and explicit
  (prompt step 8: `get_neighbors` on statute *stubs* with `edge_types=["CITES"]`),
  not this auto-fan-out.

### What is genuinely valuable and MUST be preserved

- The **three deterministic case-law discovery paths** in `vector_search`
  (`executor.py:490-599`): citation-regex resolution, subsection two-hop
  (`get_cases_for_subsections`), and neighbor-citation discovery. These surface
  case-law nodes vector search cannot (case nodes have no embeddings) — 9.6% of
  all citations. **Note the hard dependency:** the neighbor-citation path
  (`executor.py:552-565`) reads `graph_context.values()` to pick candidate
  neighbor docs. Any change to enrichment MUST keep an internal neighbor set
  available to this block.
- Explicit model tools `get_neighbors` (on statute stubs / chunks, with CITES)
  and `get_authority_chain` — untouched.

## 3. The Neptune constraint (why this exists at all)

`neptune.algo.vectors.topKByEmbedding` selects the K globally-nearest nodes
*before* any predicate; a trailing `WHERE` filters the already-chosen K rather
than searching "nearest K matching the predicate." There is no topK-with-filter
primitive. That forces the over-fetch-then-filter-in-Python pattern seen
throughout `executor.py` (`fetch_k = top_k * 6`; `search_document` fetch_k=800).
Auto-enrichment was the "give the model free graph context" workaround layered
on top. The constraint is real; this spec does not try to filter the vector call
— it disciplines what the enrichment *returns*.

## 4. Scope

### In scope
- `backend/lambdas/agentic_retrieval/agent_tools/executor.py` — the
  auto-enrichment block (`421-489`) and `vector_search` result assembly
  (`601-621`).
- `backend/lambdas/agentic_retrieval/loop/phase_a.py` — how enrichment
  neighbors are added to `all_doc_ids` / `discovery` (`615-620`).
- `config/model_configs.toml` (`agenticRetrieval` prompt) — the two lines that
  tell the model to lean on pre-enrichment (workflow steps 3 and 6). Push via
  `tools/upload_model_configs.py --only agenticRetrieval`.

### Out of scope (separate directions)
- Requirement 3's blanket "MUST traverse every query" mandate and the turn
  floor → **Direction 2** (prompt).
- Deterministic turn-0 retrieval bundle → **Direction 3**.
- Removing `Chunk → Statute` CITES → its own investigation (already flagged).
- Any Neptune graph mutation.

## 5. Step 0 — Measure enrichment edge composition (DO FIRST, passive)

Before changing behavior, resolve one open question the code alone cannot
answer: **which relationship types do enrichment neighbors actually come
through, and what is the cite-rate per relationship?** This determines the exact
retarget and must be a passive **CloudWatch Logs read only** (the live retrieval
path is reserved for the smoke-test agent — do not run chatbot queries or
mutate/scan Neptune from a hot path).

Sources already emitted (no code change needed):
- `vector_search_auto_enrichment_complete` → `doc_id`, `neighbor_count`.
- `get_neighbors_complete` → `relationships` (sorted distinct edge types),
  `neighbor_count`, `filtered_chunk_count`.
- `agent_loop_complete` → `discovery` map (source→count).
- `prepare_answer` `agent_tool_call` → `cited_doc_ids`.

Produce: (a) distribution of enrichment neighbor `relationship` types, (b) of
docs discovered via `graph-neighbor`, the fraction that appear in any
`cited_doc_ids`. This confirms the 5% figure at the *relationship* granularity
and tells us whether ANY enrichment relationship type clears a useful cite-rate
bar (hypothesis: CITES/IMPLEMENTS do; BELONGS_TO/COVERS_TOPIC/PART_OF do not).

**Decision gate:**
- If no relationship type exceeds ~15% cite-rate → **Option A** (stop surfacing
  enrichment to the model entirely; keep it internal-only for case-law
  discovery).
- If CITES/IMPLEMENTS (or another type) clears the bar → **Option B** (retarget
  enrichment to that type only, hard-capped).

Default recommendation pending Step 0: **Option A**, because the model already
has explicit, prompt-mandated CITES traversal on statute stubs (step 8), so the
model-facing enrichment is redundant with the good path and duplicative of
vector search's topical recall.

## 6. Detailed changes

### 6.1 Decouple internal enrichment from model-facing context (`executor.py`)

The single most important invariant: **the neighbor-citation discovery block
(`552-565`) must keep receiving a neighbor set.** Split the concept into two
variables:

- `enrichment_neighbors` (internal, full — feeds case-law discovery, exactly as
  `graph_context` does today).
- `model_graph_context` (returned to the model — trimmed or empty).

Concretely:

1. Keep the enrichment loop (`462-488`) computing neighbors into an internal
   dict (rename the internal accumulator or keep `graph_context` as internal but
   stop returning it verbatim). The three case-law discovery blocks continue to
   read from this internal dict unchanged.

2. **Option A (recommended default):** do **not** include `graph_context` in the
   returned `result` (`612-616`). The result carries `chunks`,
   `related_case_law` (the resolved, high-value case nodes), and
   `pre_dedup_count`. Enrichment becomes an internal-only signal that powers
   case-law discovery.

   **Option B (if Step 0 justifies):** retarget the enrichment `get_neighbors`
   call (`465`) to `edge_types=[<types that cleared the bar>]`,
   `direction="outgoing"`, and return that small filtered set as
   `graph_context`. Reduce `ENRICH_CAP_PER_DOC` 20→5 and enrich top-1 parent doc
   instead of top-3 (`429`).

3. Either way, tighten defaults: change the `os.environ.get` fallbacks so that
   even if the env vars are unset, the ceiling is small (`ENRICH_CAP_PER_DOC`
   default 20→5). Log the effective values.

### 6.2 Stop first-classing enrichment neighbors as citable discovery (`phase_a.py`)

In the `vector_search` result handler (`615-620`), enrichment neighbors are
added to `all_doc_ids` with `discovery.setdefault(neighbor_id, "graph-neighbor")`
— which makes them candidate citations. Under Option A there is no
`graph_context` in the result, so this loop naturally goes empty; **remove or
guard it.** Under Option B, keep it but the set is now small and CITES-typed.

Rationale: this enforces the prompt's own Requirement 5 ("Citation means
retrieval") — a doc becomes citable when the model actually retrieves it
(vector chunk, explicit `get_neighbors`, `get_document`, case-law path), not
because it was a structural neighbor of a top parent.

**Risk to verify (smoke test):** some enrichment-discovered docs *are* cited
today (the crux trace showed `gov_publications-chargeback-process-summary` and
news pages among cited-but-not-in-vector-top5). Removing enrichment surfacing
could drop those unless the model re-finds them via vector search or explicit
traversal. This is precisely what §8 must catch. If a stratum regresses on
cited-doc retention, fall back to Option B (retarget, don't remove).

### 6.3 Prompt (`config/model_configs.toml`, `agenticRetrieval`)

Two lines actively steer the model toward the removed context:
- Workflow step 3: *"Vector search results come pre-enriched with graph
  neighbors of the top parent documents — use those connections."*
- Workflow step 6: *"The auto-enrichment in vector_search already surfaces graph
  neighbors of your retrieved chunks."*

Under Option A: remove both sentences and, in their place, reinforce the
explicit path already in step 8 (traverse CITES on statute stubs for authority /
case law when the question turns on a statutory rule). Under Option B: soften to
"a small set of directly-cited authority neighbors may accompany results."

**Do not** touch Requirement 3 (blanket traversal mandate) here — that is
Direction 2. Push the prompt change with
`tools/upload_model_configs.py --only agenticRetrieval` (cdk deploy does not
write prompt content to DynamoDB).

## 7. Rollout sequence

1. Run **Step 0** (passive logs). Record the relationship cite-rate table.
   Choose Option A or B.
2. Capture the regression baseline against **current production** (enrichment
   still live) using the §8 harness — freeze `baseline.json` before any code
   change. **Coordinate with the smoke-test agent** so baseline capture and its
   Phase-9 tests don't interleave on the same queries.
3. Land `executor.py` + `phase_a.py` edits (leave working tree unstaged for
   review per repo convention).
4. `bun run bundle` + `cdk diff` + `cdk deploy` (backend behavior change is code,
   not infra — expect no infra diff; if the diff is non-empty, stop and review).
   Push the prompt TOML.
5. Re-run the regression set → `after.json`; compare per §8 grading.
6. On green: done. On regression in any stratum: fall back Option A→B (or tighten
   B's caps) and re-test.

## 8. Regression smoke tests (reuse the Phase-9 harness)

Reuse `tools/ingestion/tests/graph_regression_queries.yaml` and
`tools/ingestion/ops/run_graph_regression.py`. Grade on the same substance
metrics (cited-doc overlap primary; key-fact presence; no hallucinated
citations; blind LLM-judge within a variance floor).

Enrichment-specific additions:
- **Discovery-count delta (headline metric):** per query, log discovered-doc
  count before vs after. Expected: mean discovered drops from ~96 toward ~15-25;
  cited-doc set unchanged.
- **Stratum weighting for this change:**
  - **B. Case-law two-hop** — highest risk here, because the neighbor-citation
    discovery path touches enrichment internals. MUST retain 100% case-law
    cited-doc + `must_contain`. If any case node drops, the internal decoupling
    (§6.1) broke a discovery path — hard stop.
  - **A. Cross-doc / supplementary** — the queries most likely to have cited an
    enrichment-surfaced doc. Allowed to shift only by dropping non-load-bearing
    supplementary docs; correctness `must_contain` fully retained.
  - **D. Bread-and-butter** — must be stable (this is the 83%).

### Pass criterion
- Strata B, D, E: 100% cited-doc + key-fact retention.
- Stratum A: `must_contain` fully retained; cited set may lose only
  supplementary docs (not authority/statute/case).
- Zero hallucinated case citations.
- Discovery count materially down with no quality regression → success.

## 9. Expected outcome

- **Model-facing context per search:** ~60 neighbor stubs → 0 (Option A) or ≤5
  CITES-typed (Option B).
- **Discovery per query:** ~96 → ~15-25; cite rate rises from ~5% toward the
  useful range as the denominator stops being inflated.
- **Phase A latency:** lower per-turn input tokens (60 stubs × subsequent turns
  removed from history) → faster round-trips; likely fewer turns as the model
  stops sifting fan-out, though turn-count is chiefly Direction 2's lever.
- **Answer quality:** unchanged on rated traffic (per §8). Case-law path,
  authority traversal, and WPAM dedup all preserved.
- **Neptune load:** slightly *lower* (fewer/no enrichment `get_neighbors` under
  Option A) — but Neptune was never the bottleneck (2.4% of clock).

## 10. Rollback
- Any step before deploy: revert the code PR; nothing changed.
- After deploy: revert the PR + re-push the prior prompt TOML. Pure code/prompt
  rollback, no graph state to restore. Enrichment is deterministic — restoring
  the code restores the exact prior behavior.
