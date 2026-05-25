# WPAM Edition Recency Design

**Date:** 2026-05-24
**Status:** Draft
**Scope:** GraphRAG path only (legacy path is deprecated)

## Problem

The Wisconsin Property Assessment Manual (WPAM) is republished every December for use during the following calendar year. The S3 raw bucket holds 15 separate prefixes — `wpam-wisconsin-property-assessment-manual-2011` through `-2025` — each ingested as its own `Doc` node under framework `FW-WPAM`. When a user asks a question that doesn't reference a specific year, vector search returns near-duplicate chunks from many editions, eating context budget and risking the agent answering from a stale edition.

Today the system has none of the constructs needed to handle this:
- No `edition_year` property on Doc or Chunk nodes (verified by grep across `scripts/graphrag/`, `pdf_chunking/`, `packages/graphrag/lambdas/`).
- No `SUPERSEDES` edges between editions (the string appears in `prompt.py:87` as an instruction to the agent but is never emitted by the loader).
- No recency filter or boost in retrieval.
- No detection of explicit year mentions in user queries.

The prompt at `prompt.py:87-88` already tells the agent to "Note when guidance has been SUPERSEDED" and to "prefer the one with the most recent `effective_date`" for Advisory nodes — but those signals don't exist on WPAM nodes.

## Goal

When a user asks a WPAM-flavored question without specifying a year, the agent should see and reason over chunks from the most recent edition only. When the user asks about a specific year (e.g., "the 2018 WPAM"), the agent should see that edition. Older editions remain queryable so historical questions still work; they just don't pollute generic queries.

This change applies to **WPAM only**. Other frameworks are out of scope.

## Architecture

Three pipeline boundaries, all changes additive:

1. **Ingestion** — phase 4 load (`scripts/graphrag/load.py`) writes `edition_year: int` onto every WPAM Doc node and (denormalized) onto every Chunk extracted from a WPAM Doc. Year is parsed from the S3 prefix via regex `wpam-.*-(\d{4})` (last 4-digit group); on miss, falls back to scanning the first ~3 pages of PDF text for a 4-digit year in `[2010, current_year+1]`. Both methods failing = property absent + warning logged.

2. **Retrieval (intent capture)** — `refine_query` tool (`packages/graphrag/lambdas/agentic_retrieval/tools.py`) gains an optional `target_wpam_year: int | null` field in its response. The tool's prompt is extended: populate when the user explicitly mentions a 4-digit year AND the question is plausibly about WPAM / assessment-manual content. Null otherwise.

3. **Retrieval (dedup)** — a new helper module `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py` exposes `dedupe_wpam_chunks(chunks, target_year)`. It is called by `vector_search` and `get_neighbors` after Neptune returns results. It applies one pass over the WPAM subset of the chunk list (non-WPAM chunks pass through unchanged):
   - **Heading dedup:** group by `(framework_id, normalized_section_path)`. If `target_year` is set, prefer chunks from that year; otherwise prefer max(`edition_year`). Keep one per group.

   **Older-edition-only content survives.** Chunks unique to a single edition (no group peer in another year) form a group of one and are kept as-is. This is the key property that addresses the case where an older WPAM contains content the latest edition dropped: dedup only collapses *duplicates*, not *singletons*.

   **Cosine-based dedup is deferred.** Initially scoped, then dropped because Neptune's `vector_search` and `get_neighbors` Cypher do not currently return raw chunk embeddings — only similarity scores. Adding embedding retrieval would require a second algo call per query and ~10-20 KB extra payload. Heading dedup alone addresses the dominant user-visible failure mode (15 near-identical chunks → 1). If we observe renamed-heading edge cases in production (same content, different section heading across editions), we can layer cosine dedup in as a follow-up.

The two tools that surface WPAM content to the agent — `vector_search` and `get_neighbors` — both call the helper. `get_authority_chain`, `get_document`, `faq_search`, and `fetch_case_opinion` do not need it (single-doc returns or non-WPAM content).

**Agent flow for `target_wpam_year`:** the agent calls `refine_query`, reads `target_wpam_year` from the response, and passes it as an argument when subsequently calling `vector_search` or `get_neighbors`. The prompt update tells the agent to do this.

A new bullet in `prompt.py` under CITATION RULES tells the agent that WPAM tool results are deduplicated to the latest edition unless the user asked about a specific year, and that `edition_year` on each chunk is the ground truth for which manual it came from.

## Data Shapes

### Graph node properties (Neptune, schemaless — additive)

- `Doc` where `framework_id == "FW-WPAM"`: add `edition_year: int`
- `Chunk` whose parent Doc is in `FW-WPAM`: add `edition_year: int` (denormalized — see Rationale below)
- All other nodes: property absent

### Pydantic shared types (`packages/shared/lambda_layers/step_function_types/models.py`)

- `RAGDocument`: add `edition_year: Optional[int] = None`
- `SourceDocument` Pydantic mirror + Zod schema in `packages/messages/types/message-types.ts`: add `edition_year: Optional[int]` (wired through but not displayed in v1)

### Tool I/O (`packages/graphrag/lambdas/agentic_retrieval/tools.py`)

`refine_query` response — extend schema:
```
{
  "refined_query": str,
  "target_wpam_year": int | null   # NEW
}
```

`vector_search` input — add optional `target_wpam_year: int | null` (default null). Output shape unchanged; chunks now carry `edition_year`.

`get_neighbors` input — add optional `target_wpam_year: int | null` (default null). Same dedup applied to its WPAM result subset.

### Helper module (`packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py`, new ~50 lines)

```python
def dedupe_wpam_chunks(
    chunks: list[dict],
    target_year: int | None = None,
) -> list[dict]:
    """Pure function. Splits chunks into WPAM and non-WPAM,
    applies heading dedup to WPAM only, returns a list with
    non-WPAM chunks preserved in original position. WPAM chunks
    missing edition_year are held aside (passed through, never
    deduped) and logged."""
```

No new dependencies — pure Python, no numpy needed.

### Rationale: denormalizing `edition_year` onto Chunks

Dedup runs on chunk lists. Joining back to the parent Doc inside the lambda means an extra Neptune query per result. Storing `edition_year` on the chunk is a few hundred KB of duplication for the whole WPAM corpus and removes that round trip.

## Backward Compatibility

All changes are additive:

- Old chunks without `edition_year` survive: helper treats `None` as "ineligible for dedup, pass through unchanged."
- Old `refine_query` output without `target_wpam_year`: vector_search treats absence as null.
- Old chat history sessions: no migration; they replay fine. (Per established convention from the citation-refresh project, no backfill of old rows.)

## Re-ingestion

The loader must rewrite phase 2 (Doc nodes) and phase 8 (Chunk nodes) for WPAM so the new `edition_year` property lands on existing nodes. The CLI already supports `--source-filter wpam-` (`load.py:1104-1112`), which scopes the doc list to WPAM-only while leaving graph-wide phases (scaffold, hierarchy, stub resolution) MERGE-idempotent. We use that flag to re-run phases 2 and 8 against the existing graph without a full wipe.

The full-reingest convention (memory: `feedback_edge_changes_full_reingest.md`) applies to edge-logic changes; this is a property-only mutation on existing nodes, so a scoped re-run is appropriate.

Note: `vector_search` and `get_neighbors` Cypher in `neptune_client.py` currently do not return `edition_year` — those `RETURN` clauses need a one-line addition for `edition_year` to surface in tool results.

## Testing

### Unit tests for `dedupe_wpam_chunks` (load-bearing piece)

- Heading dedup: 5 chunks with same `(framework, section_path)`, different `edition_year` → returns 1 chunk (max year).
- Heading dedup with `target_year`: same input, `target_year=2018` → returns the 2018 chunk, not the 2025.
- Heading dedup, target year not present: `target_year=2017` but only 2018-2025 in input → falls back to max(year), no error.
- Singleton survival: a chunk unique to 2018 (no peer in any other year) passes through unchanged regardless of `target_year`.
- Mixed list: WPAM + non-WPAM → only WPAM dedup'd, non-WPAM passes through unchanged.
- Missing `edition_year` on a WPAM chunk → that chunk passes through, warning logged.
- Empty input → empty output, no errors.

### Year-extraction tests (`scripts/graphrag/test_wpam_year.py` or similar)

- `wpam-wisconsin-property-assessment-manual-2025` → 2025
- `wpam-wisconsin-property-assessment-manual-vol-1-2011` → 2011 (last 4-digit group)
- Prefix without year → falls back to PDF text scan
- PDF text "2024 Wisconsin Property Assessment Manual" → 2024
- PDF text with no plausible year → returns None, doc loads with `edition_year` absent + warning

### refine_query tests (mocked LLM)

- Query "what does the 2018 WPAM say about agricultural land?" → `target_wpam_year=2018`
- Query "what does WPAM say about agricultural land?" → `target_wpam_year=null`
- Query "what's the 2018 deadline for property tax appeals?" → `target_wpam_year=null` (year mentioned but not WPAM-y)

### Integration smoke (manual, post-deploy on `WisconsinBotGraphRAG` in us-east-1)

- Generic WPAM query — confirm result chunks all share `edition_year` (the latest), no near-duplicates.
- "What did the 2018 WPAM say about ..." — confirm 2018 chunks come back.
- Query that traverses statute → WPAM via `get_neighbors` — confirm no edition duplicates among neighbors.

### Out of scope for testing

- Cross-edition answer-quality eval. Needs an eval set; deferred.
- Frontend rendering of `edition_year`. Wire-only in v1 — see YAGNI cuts.

## Risks

- **`section_path` consistency across editions.** Heading dedup relies on stable, normalized section identifiers across years. Reworded headings (even cosmetic) let near-duplicate chunks survive; without cosine pass B as a fallback, this means context budget gets eaten by a few near-dupes. Failure mode is mild (some noise, not a wrong answer). Worth a sanity check during implementation: pull 5-10 chunks that should be the same section across editions and verify `section_path` values match. If the pattern is bad enough to matter in practice, follow up with cosine dedup.
- **Re-ingest cost.** Decide between scoped FW-WPAM re-run and full reload before writing the property-mutation code.
- **Year-mention false positives in refine_query.** "What's the 2024 deadline for appeals?" might be wrongly tagged as `target_wpam_year=2024`. Prompt must draw the line: 4-digit year + WPAM-y topic. Accept some false positives; failure mode is recoverable (user gets 2024 WPAM instead of latest).

## YAGNI cuts

- Cosine-based dedup pass. Deferred — heading dedup alone handles the dominant failure mode. Layer in if production traces show renamed-heading edge cases.
- Frontend display of `edition_year` (wire-only in v1).
- `SUPERSEDES` edges between editions. Edition recency handled at retrieval layer, not graph layer.
- Generalizing `edition_year` beyond WPAM. Plumbed as `Optional[int]` so future frameworks can opt in, but only WPAM gets the dedup helper.
- Retrieval quality eval suite. Separate work.

## Open questions (non-blocking)

1. **CloudWatch dedup decision logging** for the first week post-deploy — log dropped chunk IDs and reasons (heading vs cosine). ~1 extra log line per query. Recommend: yes, useful for diagnosis. Confirm during implementation.
2. **Vol-1-2011 oddball** — `wpam-wisconsin-property-assessment-manual-vol-1-2011` is structurally different. Verify whether vol-2-2011 exists; if so, both share `edition_year=2011` but different section content (heading dedup handles this naturally). Quick `aws s3 ls` check during implementation.

## Components and dependencies

| Component | What it does | Depends on |
|---|---|---|
| `wpam_dedup.py` (new) | Pure dedup function over chunk lists | none |
| `tools.py` (`vector_search`) | Calls dedup helper after Neptune query | `wpam_dedup` |
| `tools.py` (`get_neighbors`) | Calls dedup helper after Neptune query | `wpam_dedup` |
| `tools.py` (`refine_query`) | Surfaces `target_wpam_year` in response | LLM prompt update |
| `load.py` (phase 4) | Writes `edition_year` to WPAM Doc + Chunk nodes | year-parser helper |
| Year-parser helper (new) | Prefix regex + PDF-text fallback | PyMuPDF (existing) |
| Pydantic shared types | Adds `edition_year: Optional[int]` to RAGDocument, SourceDocument | none |
| Zod schema | Mirror Pydantic addition | none |
| `prompt.py` | New CITATION RULES bullet | none |

Each component has one clear responsibility, communicates through narrow interfaces, and is independently testable. The dedup helper is the only piece with non-trivial logic; everything else is plumbing.
