# Direction 1 — Step 0 Findings (Auto-Enrichment Baseline)

**Date:** 2026-07-17
**Status:** Complete (retrospective validation, from CloudWatch logs only)
**Related:** `docs/direction-1-auto-enrichment-spec.md`

## Context

Step 0 was specified as a *pre-implementation gate*: measure enrichment
cite-rate per relationship type → choose Option A vs B. By the time Step 0 ran,
the implementer had already applied **Option A** locally (not yet deployed).
Step 0 therefore serves as **retrospective validation** that Option A is
supported by the data, and as the **"before" baseline** for the forward
measurement once the change deploys. It is not a gate.

All measurements are **read-only CloudWatch Logs Insights** over historical
traffic. No live retrieval queries were run and no Neptune state was touched
(the live path was reserved for the Phase-9 smoke-test agent).

## What Step 0 could NOT measure (honest limitations)

1. **Per-relationship cite-rate table — NOT recoverable.** The spec (§5) assumed
   `vector_search_auto_enrichment_complete` logged relationship types. It does
   not — it logs only `doc_id` and `neighbor_count` (`executor.py:470-476`).
   Relationship types appear only on the *explicit* `get_neighbors_complete`
   event, not on auto-enrichment. So "CITES 40% vs COVERS_TOPIC 2%" cannot be
   built from logs.

2. **Cited-doc → discovery-source attribution — NOT recoverable.**
   `agent_loop_complete` logs `discovery_summary(discovery)`, which is a
   **source→count summary** (e.g. `{"vector-search":12,"graph-neighbor":43}`),
   NOT the per-doc `{doc_id: source}` map. The per-doc map lives only in loop
   memory and is never emitted. An initial analysis attempt that tried to join
   `cited_doc_ids` against this summary map was **invalid** (it compared doc IDs
   to source-name strings) and its output (an apparent "0% of citations depend
   on graph-neighbor") is **retracted** — it was a measurement artifact, not a
   finding. Cited-source attribution is only obtainable going forward via the
   smoke-test harness (which captures full before/after cited sets per query),
   not from historical logs.

Because of (1) and (2), Step 0 cannot *prove* enrichment citations are near-zero
from logs alone. It establishes the **magnitude** case (below), which is the
solid, log-derived ground; the citation-dependency proof is deferred to the
forward smoke-test comparison.

## What Step 0 DID establish (validated fields only)

### Enrichment magnitude and cite-rate (discovery source→count is reliably logged)

| Window | n | avg discovered/query | avg cited/query | cite-rate | graph-neighbor share |
|---|--:|--:|--:|--:|--:|
| Organic Jun 22 – Jul 4 (old code, real users, RELATED_TO still live) | 344 | 102.8 | 4.5 | **4.4%** | **86.6%** |
| Current Jul 5 – 17 (old code; Jul 16-17 = smoke-test synthetic) | 25 | 55.8 | 6.3 | 11.3% | 82.0% |

- **The dominant discovery source is graph-neighbor fan-out (82–87%),** while the
  overall cite-rate sits at **4–11%**. Even accounting for the fact that
  `graph-neighbor` conflates auto-enrichment with explicit `get_neighbors`, the
  vast majority of discovered documents are never cited. This is the core
  motivation for Option A and is unaffected by the attribution limitations
  above.

### Enrichment fan-out has been shrinking (cap tightenings + Phase 9 removal)

`vector_search_complete.graph_context_neighbor_count`, binned by 3 days:

| Period | avg neighbors surfaced / query | per-enrichment-call p50 | per-call max |
|---|--:|--:|--:|
| Jun 1-3 | 1,967 | 761 | 983 |
| Jun 16 | 298 | 43 | 983 |
| Jun 22-25 | ~115 | 50 | 50 |
| Jul 1-4 | ~45 | 14 | 20 |
| Jul 13-16 | ~20-36 | 5-13 | 12-19 |

The firehose was already being throttled via env-var cap changes over the
window; the ~60-stub figure in the original spec reflected a mid-window state,
and the earlier era (up to ~2,000 neighbors/query) inflates the 45-day averages.
Option A removes the *model-facing* surfacing entirely regardless of cap.

### The value paths that Option A preserves DID fire (and still will)

Case-law discovery events over Jun 1 – Jul 16 (these read `graph_context`
internally, which Option A **keeps computing**):

| Path | Fires |
|---|--:|
| `vector_search_neighbor_citation_discovery` | 456 |
| `vector_search_case_law_resolved` | 320 |
| `vector_search_subsection_case_discovery` | 206 |

Code review confirms the invariant held in the local implementation:
`executor.py:619` returns a result WITHOUT `graph_context`, but the internal
`graph_context` dict is still built (`:430`) and still consumed by the three
case-law blocks (`:563` reads `graph_context.values()`). `related_case_law` is
still returned to the model (`:629-630`). So Option A removes only the low-cite
neighbor flood, not the case-law machinery.

## Conclusion

- **Option A is consistent with the data.** Enrichment dominates discovery
  volume (82–87%) at a 4–11% overall cite-rate, and the fan-out was already
  being capped down; removing the model-facing surface is well-motivated.
- **The strong claim ("~0% of citations depend on enrichment") is NOT proven by
  logs** and must not be asserted as such. It is testable only via the forward
  smoke-test comparison.
- **No decision is blocked.** The change is implemented locally and reversible;
  Step 0 provides the "before" baseline, not a gate.

## Forward measurement plan (the real validation — run after deploy)

Once Option A is deployed and organic (non-smoke-test) traffic accumulates on
the post-Phase-9 graph (~1–2 weeks):

1. **Discovery magnitude delta.** Re-run the discovery source→count aggregation.
   Expected: `graph-neighbor` share collapses (enrichment no longer tags docs;
   only explicit `get_neighbors` remains), avg discovered/query drops from ~100
   toward ~15-25, cite-rate rises as the denominator shrinks.
2. **Cited-set retention (the citation-dependency proof deferred from Step 0).**
   Use the Phase-9 smoke-test harness (`run_graph_regression.py`,
   `graph_regression_queries.yaml`) to compare before/after cited-doc sets per
   query. Pass criterion: strata B/D/E retain 100% cited-doc + key-fact;
   stratum A may drop only supplementary docs; zero hallucinated case citations.
3. **Case-law path fire counts.** Confirm `case_law_resolved`,
   `subsection_case_discovery`, `neighbor_citation_discovery` still fire at
   comparable rates — this directly checks the preserved invariant in production.
4. **Latency.** Compare Phase A per-turn input tokens and turn count before/after
   (turn count is chiefly Direction 2's lever, but reduced context may help).

### Optional: recover the attribution Step 0 couldn't

If per-doc citation attribution is wanted long-term, add one log field:
emit the per-doc `discovery` map (or at minimum the discovery source of each
`cited_doc_id`) at `agent_loop_complete`. Cheap, and it would make future
"which source produced the citation" analysis a direct log query instead of
requiring the smoke-test harness.
