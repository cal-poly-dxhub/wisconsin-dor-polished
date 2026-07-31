# Auto-Backfill

`vector_search` does not just return the top-k chunks nearest the query. Before
handing results to the agent, it runs a pipeline of **backfill stages** that
automatically pull in related authority the query is "about" — the statute text
behind a WPAM passage, the case law that cites a controlling statute, docs a
keyword-heavy refined query would miss — without the agent having to spend a turn
asking for any of it. A final backfill runs *after* the agent commits to an
answer, fetching full opinion text for cited case-law stubs.

This doc describes those automatic enrichment steps: where they run, what they
fetch, and how they're gated.

## Why backfill at all

The agent could, in principle, discover all of this itself: call `get_section`
on the underlying statute, `get_neighbors` on a case, `find_case_law` scoped to a
chapter. But each is a Bedrock round-trip, and the agent doesn't know what it
doesn't know — case law that a *retrieved* chunk merely mentions in passing, or a
statute subsection cited by a WPAM chunk, is invisible until something reads that
text. Backfill does the deterministic, high-value part in-Lambda so the agent's
turns go to reasoning, not fetching.

Every backfill stage is **best-effort**: each is wrapped in try/except and logs a
warning on failure without blocking the pipeline. Backfill results are returned as
separate keys in the tool result (`statute_backfill`, `caselaw_backfill`,
`related_case_law`, `broad_discovery`), not merged into `chunks`.

## The `vector_search` pipeline

`vector_search` executes an 11-stage pipeline defined in
`agent_tools/pipeline.py` (`VECTOR_SEARCH_STAGES`). Each stage is a module under
`agent_tools/stages/` exposing `run(ctx: StageContext) -> StageResult`; stages
mutate a shared `StageContext` in place. In order:

| # | Stage | Role |
|--:|-------|------|
| 1 | `auto_refine` | Rewrite the query (+ extract `target_wpam_year`) before embedding |
| 2 | `neptune_search` | Embed refined query; Neptune vector search with **6× over-fetch** (`top_k * 6`) |
| 3 | `wpam_dedup` | Two-pass WPAM cross-edition dedup (see engineering guide §9) |
| 4 | `diversity_cap` | Cap chunks per doc (`DIVERSITY_CAP_PER_DOC`, default **3**) |
| 5 | `authority_quota` | Reserve top_k slots for primary sources before filling the rest |
| 6 | `authority_tiebreak` | Within a score bucket, higher authority wins |
| 7 | `auto_enrichment` | Graph neighbors of the top-3 docs — **internal only** |
| 8 | `citation_extraction` | Regex case citations from chunk text → resolve to CaseLaw |
| 9 | `statute_backfill` | Chunk `CITES` → the cited statute's text |
| 10 | `caselaw_backfill` | Cited statute stubs → chunks of cases that cite them |
| 11 | `broad_discovery` | Second search arm on the user's original query |

Stages 1–6 shape the primary result set; stages 8–11 are the backfill arms.
Stage 7 is a signal-gathering step whose output is not surfaced (see below).

### The primary result set (stages 1–6)

- **6× over-fetch** (`neptune_search`): Neptune's `topKByEmbedding` has no
  pre-filter, and WPAM editions dominate the vector space, so the search
  always over-fetches `top_k * 6` candidates (60 for the default `top_k=10`) to
  give the dedup/diversity/authority stages room to work.
- **Diversity cap** (`diversity_cap`): no single doc may contribute more than
  `DIVERSITY_CAP_PER_DOC` (default **3**) chunks to the candidate pool. *(Note:
  `StageContext.max_per_doc` has a dataclass default of 5, but the stage always
  overwrites it from the env var, so the effective default is 3.)*
- **Authority quota** (`authority_quota`): rather than a naive `chunks[:top_k]`
  slice, this reserves slots for primary sources — `AUTHORITY_QUOTA_STATUTE`
  (default 3, `authority_level ≤ 2`), `AUTHORITY_QUOTA_WPAM` (default 2,
  `level == 5`), `AUTHORITY_QUOTA_GUIDES` (default 2, `level in {6,7}`) — so
  case-law chunks can't crowd authoritative sources out of the visible top_k. It
  also snapshots the top `STATUTE_BACKFILL_SOURCE_GATE` (default 3) chunks as the
  source set for statute backfill.
- **Authority tiebreak** (`authority_tiebreak`): chunks are bucketed by score
  (bucket width `_AUTH_TIE_THRESHOLD = 0.03`); within a bucket, lower
  `authority_level` (higher authority) sorts first; between buckets, relevance
  wins.

## Backfill arm 1 — direct citation resolution

**Stage:** `citation_extraction.py`

Joins the text of all retrieved chunks, regex-extracts Wisconsin case citations,
and resolves them against CaseLaw node `citation` properties. This surfaces cases
a WPAM/guide chunk *names* (e.g., Markarian) that would otherwise be buried among
a controlling statute's 1500+ `CITES` neighbors — the citation is in the text you
already have, so no traversal is needed.

- Extraction: `extract_citations` in `agent_tools/executor.py` (three regex
  patterns for Wisconsin formats — `\d+ Wis. 2d \d+`, `\d+ N.W.2d/3d \d+`,
  `\d{4} WI [App] \d+`, with tolerant whitespace).
- Resolution: `NeptuneClient.resolve_case_citations` —
  `MATCH (n:CaseLaw) WHERE n.citation IN $citations`.
- Output: `ctx.related_case_law` → `related_case_law` in the tool result.

## Backfill arm 2 — statute backfill

**Stage:** `statute_backfill.py`

For the top-N most-relevant retrieved chunks (the `authority_quota` snapshot),
follows their **chunk-level `CITES` edges** to a statute stub, then resolves the
stub to the real statute-text chunk via `DEFINED_BY`. This grounds a WPAM/guide
passage in the underlying statute without a separate `list_sections` +
`get_section` round-trip.

- **Gate:** `STATUTE_BACKFILL_SOURCE_GATE` (default **3**) — only the top-3
  relevance chunks are used as backfill sources. Data-derived: the cited statute
  appears within the top-3 in ~75% of statute-citing queries; top-5 adds no recall.
- **Cap:** `STATUTE_BACKFILL_CAP` (default **3**) statute chunks returned.
- Statute source chunks are skipped (no point backfilling a statute from a
  statute). Candidates are ranked by *consensus* (how many source chunks cite
  the statute) then best source rank.
- **Chunk-level dedup:** a backfilled statute chunk is dropped only if that exact
  chunk is already present — so `statutes-70 §70.47` can be surfaced even when
  `§70.48` from the same chapter is already in the results.
- Neptune method: `get_statute_backfill`. Output: `ctx.statute_backfill` →
  `statute_backfill` in the result; each entry records `cited_by_source_rank` and
  the `cited_stubs` it came from.

## Backfill arm 3 — case-law backfill

**Stage:** `caselaw_backfill.py`

Takes the statute stubs discovered by statute backfill (plus any statute chunks in
the results whose heading parses to a `WIS-STAT-N.M` id) and pulls in chunks of
**cases that directly cite those statutes**, traversing
`(:Statute)<-[:CITES]-(:Chunk)-[:EXTRACTED_FROM]->(:CaseLaw)`.

- Stubs searched: capped at **5** (`sorted(stub_ids)[:5]`).
- **Fetch:** `CASELAW_CHUNK_FETCH_K` (default **200**), clamped to
  `CASELAW_CHUNK_HARD_CAP` (default **300**).
- Candidates are re-ranked by cosine similarity against the query embedding
  (`_rank_chunks_by_relevance`), then diversified: prefer distinct cases, then
  allow one more chunk per case (`CASELAW_CHUNK_MAX_PER_CASE`, default **1**),
  filling up to `CASELAW_BACKFILL_CAP` (default **5**).
- Neptune method: `get_case_chunks_for_statutes_with_embeddings`. Output:
  `ctx.caselaw_backfill` (+ `caselaw_backfill_meta` with saturation/latency).

## Backfill arm 4 — broad discovery

**Stage:** `broad_discovery.py`

The agent's refined query is keyword-heavy and can miss practitioner-oriented docs
(news, guides). Broad discovery runs a **second search arm on the user's original
natural-language query**, through the same dedup + diversity-cap machinery, and
keeps only chunks from docs *not* already in the narrow results.

- Fires only on the **first** `vector_search` call, and only when the original
  query differs from the refined query (compared case-insensitively, ignoring
  trailing `?`/`.`).
- Uses the same `fetch_k`, `wpam_dedup`, `max_per_doc`, and `top_k` as the narrow
  arm. Output: `ctx.broad_discovery` → `broad_discovery`; `broad_skipped` /
  `broad_query` report whether it ran.

## The internal-only signal: auto-enrichment

**Stage:** `auto_enrichment.py`

Fetches graph neighbors (`get_neighbors`) for the top-3 distinct parent doc_ids,
ranked by edge priority then authority and capped per doc-type
(`ENRICH_CAP_PER_DOC` default 5, `ENRICH_CAP_PER_TYPE` default 4). Chunk-labeled
neighbors are filtered out.

**This output (`ctx.graph_context`) is deliberately NOT returned to the model** —
surfacing it floods the tool result with low-cite-rate neighbor stubs. Today it is
consumed only for log counts in `pipeline.py`.

> **Heads-up for maintainers.** The `auto_enrichment.py` docstring claims its
> `graph_context` powers `citation_extraction` and `caselaw_backfill`. That is
> **stale** — those stages read `ctx.chunks` and `ctx.statute_backfill`, not
> `ctx.graph_context`. As wired today, auto-enrichment is effectively a no-op
> beyond logging. (It also references deleted spec docs.)

## Post-answer: opinion backfill

The four `vector_search` arms surface case law as **metadata stubs** (title,
citation, source_url) — no opinion text. The agent can reason with a stub, but
Phase B regenerates the streamed answer from the `RAGDocument` list, and a stub
with empty `content` can't contribute its holding to the streamed prose.

Opinion backfill closes that gap. After the agent calls `prepare_answer`, a
deterministic step in `handler.py` scans `cited_doc_ids` for case-law stubs that
weren't already fetched via `fetch_case_opinion`. For up to
`_OPINION_BACKFILL_CAP` (**3**) such stubs, it resolves the citation, derives the
S3 key, and fetches the full opinion `.txt`. This runs **before**
`build_rag_documents`, so Phase B receives substantive content.

- Only cited stubs are backfilled — uncited graph/neighbor noise is never fetched.
- Discovery tag: `opinion-backfill`.
- Best-effort and capped at 3 to bound latency.

## Not wired: neighbor-doc citation discovery

The Neptune client carries `get_chunk_statute_ids`,
`rank_neighbors_by_shared_statutes`, `get_chunks_text_for_docs`, and
`get_cases_for_subsections` — building blocks for a "rank neighbor docs by shared
statutes, then scan their chunk text for citations" discovery path. **None of
these has a production caller** (only `tests/test_neptune_client.py` references
them). If you're tracing how case law reaches the agent, it's via the four arms
above, not this path. Treat those client methods as dead code until something
wires them into the pipeline.

## Cost summary

| Arm | Extra Neptune queries | Token impact |
|-----|----------------------|--------------|
| Direct citation | 1 (`resolve_case_citations`) | ~metadata per resolved case |
| Statute backfill | 1 (`get_statute_backfill`) | up to 3 statute chunks |
| Case-law backfill | 1 (`get_case_chunks_for_statutes_with_embeddings`) + 1 embed | up to 5 case chunks |
| Broad discovery | 1 vector search + 1 embed | up to `top_k` additive chunks |
| Auto-enrichment | up to 3 (`get_neighbors`) | none (internal-only) |
| Opinion backfill | per-stub resolve + S3 fetch (cap 3) | full opinion text for cited cases |

All arms are additive and best-effort; a failure in any one logs a warning and
leaves the rest of the result intact.

## Files

- `agent_tools/pipeline.py` — `run_vector_search`, `VECTOR_SEARCH_STAGES`
- `agent_tools/stages/{citation_extraction,statute_backfill,caselaw_backfill,broad_discovery,auto_enrichment}.py`
- `agent_tools/stages/base.py` — `StageContext` / `StageResult`
- `agent_tools/executor.py` — `extract_citations`, `_rank_chunks_by_relevance`
- `graph/neptune_client.py` — `resolve_case_citations`, `get_statute_backfill`, `get_case_chunks_for_statutes_with_embeddings`
- `handler.py` — post-answer opinion backfill (`_OPINION_BACKFILL_CAP`)
- `case_law.py` — `fetch_case_opinion`, opinion-card building
- Tests: `tests/test_pipeline.py`, `test_statute_backfill.py`, `test_caselaw_backfill.py`, `test_auto_enrichment.py`, `test_neptune_client.py`
