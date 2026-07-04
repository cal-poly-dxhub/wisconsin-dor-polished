# Neighbor-Doc Citation Discovery

## Problem

Vector search retrieves document chunks relevant to a query, and auto-enrichment fetches graph neighbors of the top parent docs. But case law that's only mentioned in a *neighbor doc's chunks* (not in the directly-retrieved chunks) is invisible to the agent.

Example: for "Does land qualify for agricultural classification without Schedule F?", vector search returns WPAM ag chunks. Auto-enrichment discovers the Ag Assessment Guide as a neighbor. The Ag Assessment Guide's chunks contain the citation "2019 WI 23" (Peter Ogden v. DOR) — but nobody reads those chunks, so the case is never surfaced.

Citation resolution (`resolve_case_citations`) only scans text from directly-retrieved chunks. The agent would need to proactively call `find_case_law` for the case — but it doesn't know the case exists.

## Why Pure Graph Traversal Doesn't Work

The initial approach tried to discover cases purely via graph edges: query chunks → shared statutes → case law (LIMIT 5). This fails because high-degree statute nodes like WIS-STAT-70.32 have 1500+ CITES edges to CaseLaw. No structural graph signal can distinguish Peter Ogden from the 1499 other cases that also cite 70.32. The only place that distinction exists is in **text** — the ag assessment guide literally writes "2019 WI 23" in the context of ag classification.

## Preceding Pipeline Steps

Before neighbor citation discovery runs, the vector_search tool execution has already completed several steps:

1. **Vector search** — returns `top_k` chunks (default 10, capped at 20). The agent can request a different top_k but it's usually 10.

2. **WPAM dedup** (`dedupe_wpam_chunks`) — if a `target_wpam_year` was set by query refinement, keeps only that edition's chunks. Otherwise keeps only the most recent edition year per doc. E.g., 5 WPAM-2024 chunks + 3 WPAM-2022 chunks collapses to just the 2024 ones.

3. **Top 3 parent docs** — iterates through post-dedup chunks in *score order*, collects distinct `doc_id` values, stops at 3. These are the 3 highest-scoring unique parent docs.

4. **Auto-enrichment** — calls `get_neighbors(doc_id)` on each of those 3 docs. Returns up to 50 neighbors per doc (the default limit) with metadata only (id, title, framework_id, labels, etc.). Chunk-labeled nodes are filtered out. Results stored in `graph_context[doc_id] = neighbors`.

5. **Direct citation resolution** — joins all *retrieved* chunk text, regex-extracts citations, resolves them to CaseLaw nodes. This catches cases mentioned in the chunks you already have.

## Solution: Neighbor-Doc Text Scanning

After the above steps complete, neighbor citation discovery runs:

1. **Filter** neighbor docs from `graph_context` to non-WPAM, non-meta nodes (exclude Statute, Framework, Topic, Chunk, CaseLaw labels)
2. **Get statutes from query chunks** — follows CITES edges from the *retrieved chunks* to Statute nodes in the graph. This is the "topic signal" — e.g., if the WPAM ag chunks cite WIS-STAT-70.32, that's what you get.
3. **Rank** those filtered neighbors by how many of those same statutes their own chunks also cite (top 3)
4. **Fetch** chunk text for those 3 ranked docs from Neptune (~30-50 chunks)
5. **Regex-extract** case citations from the text (no LLM call, runs entirely in Lambda)
6. **Resolve** extracted citations to CaseLaw nodes in the graph
7. **Deduplicate** against cases already found via direct citation resolution

The key insight: graph structure tells us *which* neighbor docs are topically relevant (shared statutes), and text tells us *which* cases matter in that topic (literal citations).

## Implementation (Cypher Queries)

### Step 1: Filter neighbor docs (in-Lambda, no query)

Flattens all neighbors across the 3 docs in `graph_context` and keeps only those where:
- `framework_id != "FW-WPAM"` (non-WPAM)
- Labels don't include CaseLaw, Statute, Framework, Topic, or Chunk (document-type nodes only)

This typically yields ~20-30 neighbor doc IDs from the ~150 total neighbors (50 per parent doc × 3 parent docs, minus Chunk-filtered ones).

### Step 2: Get statutes cited by query chunks

```cypher
UNWIND $chunk_ids AS cid
MATCH (c:Chunk {id: cid})-[:CITES]->(s:Statute)
RETURN DISTINCT s.id AS statute_id
```

`$chunk_ids` comes from the vector search results — the chunks you already retrieved. This follows their CITES edges to discover which statutes the query is "about."

### Step 3: Rank neighbor docs by shared statute overlap

```cypher
UNWIND $doc_ids AS did
MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: did})
MATCH (c)-[:CITES]->(s:Statute)
WHERE s.id IN $statute_ids
RETURN d.id AS doc_id, count(DISTINCT s) AS shared_statutes
ORDER BY shared_statutes DESC
LIMIT 3
```

For each of the ~26 filtered neighbor docs, counts how many of the query chunk's statutes their own chunks also cite. The ag assessment guide ranks #1 because its chunks also cite WIS-STAT-70.32. This is the "discoverability gate" — it ensures we only read text from topically relevant neighbors.

### Step 4: Fetch chunk text for ranked docs

```cypher
UNWIND $doc_ids AS did
MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: did})
RETURN c.text AS text
```

Returns all chunk text for the top 3 ranked docs (~30-50 chunks total, ~50KB of text). This text stays in the Lambda — it never enters Claude's context window.

### Step 5: Regex citation extraction (in Lambda, not Neptune)

The `extract_citations` function in `tools.py` uses regex patterns for Wisconsin case citation formats (e.g., `2019 WI 23`, `45 Wis. 2d 683`, `173 N.W.2d 627`). Runs entirely in the Lambda on the ~50KB text blob — no LLM call.

### Step 6: Resolve citations to CaseLaw nodes

```cypher
MATCH (n:CaseLaw)
WHERE n.citation IN $citations
RETURN n.id AS id, n.title AS title, n.citation AS citation,
       n.doc_type AS doc_type, n.authority_level AS authority_level,
       n.source_url AS source_url, labels(n) AS labels
```

Only new citations (not already found in step 5 of the preceding pipeline) are resolved. Results are also deduplicated by node ID against any cases already in `related_case_law`.

## When It Fires

Inside the `vector_search` tool execution, as the final step after all preceding pipeline steps (vector search → WPAM dedup → auto-enrichment → direct citation resolution). It's gated on:
- `graph_context` being non-empty (at least one parent doc had neighbors)
- At least one filtered neighbor doc existing (non-WPAM, non-meta)
- Query chunks citing at least one statute (otherwise there's no topic signal to rank by)
- At least one ranked doc having shared statutes

If any gate fails, the discovery exits early with no error — it's purely additive.

## Cost

- **Neptune queries:** 3 additional (get_chunk_statute_ids + rank_neighbors_by_shared_statutes + get_chunks_text_for_docs)
- **Text fetched:** chunk text for ~3 neighbor docs (~30-50 chunks total)
- **Regex cost:** negligible — simple pattern matching on ~50KB of text
- **Token impact:** ~300 tokens per resolved case added to Claude's context (only the CaseLaw metadata, not the chunk text)
- **Latency:** ~100–150ms for all three queries combined (Neptune in-region)
- **Failure mode:** best-effort; entire block is wrapped in try/except, never blocks the agent loop

## Example: Peter Ogden Discovery

1. Query: "Does land qualify for agricultural classification without Schedule F?"
2. Vector search → WPAM chunks about ag classification (cite WIS-STAT-70.32)
3. Auto-enrichment → neighbor docs including `gov_publications-2026-agricultural-assessment-guide`
4. Direct citation resolution → citations from WPAM chunks resolved
5. **Neighbor citation discovery:**
   - Filter neighbors: 26 non-WPAM docs from graph_context
   - Rank by shared statutes with query chunks (WIS-STAT-70.32): ag guide ranks #1 (2 shared statutes)
   - Fetch chunk text for top 3 ranked docs (~40 chunks)
   - Regex extracts citations including "2019 WI 23"
   - Resolve: `case-law-2019-wi-23` (Peter Ogden v. DOR)
   - Deduplicate: not already in related_case_law → add it
6. Agent sees Peter Ogden in `related_case_law` and can cite it

## Diagram

```
                    ┌─────────────────────┐
                    │   Vector Search     │
                    │   (top-k chunks)    │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                 │
              ▼                ▼                 ▼
    ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐
    │ WPAM Chunk 1 │  │ WPAM Chunk 2 │  │ Auto-Enrichment │
    │              │  │              │  │ (get_neighbors)  │
    └──────┬───────┘  └──────┬───────┘  └────────┬────────┘
           │                 │                    │
           │ CITES           │ CITES              │ neighbor metadata
           ▼                 ▼                    ▼
    ┌──────────────────────────────┐   ┌──────────────────────┐
    │      WIS-STAT-70.32          │   │  26 neighbor docs    │
    │   (chunk_statute_ids)        │   │  (graph_context)     │
    └──────────────────────────────┘   └──────────┬───────────┘
                    │                              │
                    │              ┌───────────────┘
                    │              │ Filter: non-WPAM, non-meta
                    │              │ Rank: shared statutes
                    │              ▼
                    │     ┌────────────────────┐
                    │     │ Top 3 ranked docs  │
                    │     │ (ag guide = #1)    │
                    │     └────────┬───────────┘
                    │              │ get_chunks_text_for_docs
                    │              ▼
                    │     ┌────────────────────┐
                    │     │ Ag Guide chunks    │
                    │     │ "...2019 WI 23..." │
                    │     └────────┬───────────┘
                    │              │ regex extract_citations
                    │              ▼
                    │     ┌────────────────────┐
                    │     │ ["2019 WI 23"]     │
                    │     └────────┬───────────┘
                    │              │ resolve_case_citations
                    │              ▼
                    │     ┌────────────────────┐
                    └────▶│  Peter Ogden       │
                          │  case-law-2019-wi-23│
                          └────────────────────┘
```

The shared-statute ranking ensures we read chunks from topically relevant neighbors (not random docs). The regex extraction finds the exact citations without needing an LLM call. The resolution confirms those citations exist as CaseLaw nodes in the graph.

## Downstream: Opinion Backfill

Neighbor-doc citation discovery is the **upstream** mechanism — it surfaces case law to the agent. But discovered cases arrive as metadata stubs (title, citation, source_url) with no chunk text or summary. The agent can synthesize a rich answer using these stubs alongside WPAM context, but its answer text is **not** passed downstream. ResponseStreaming regenerates the final answer from the `RAGDocuments` list, and a stub with an empty `content` field means the holding can't be incorporated into the streamed answer.

The **opinion backfill** step closes this gap. After the agent calls `answer`, a deterministic post-answer step checks `cited_doc_ids` for case-law stubs that weren't already fetched via `fetch_case_opinion`. For up to 3 such stubs, it resolves the citation from Neptune and fetches the full opinion text from S3 (the same `.txt` files that `fetch_case_opinion` reads). This runs before `_build_rag_documents`, so ResponseStreaming receives substantive content for cited case law.

**The complete flow for a discovered case like Peter Ogden:**

1. Neighbor citation discovery surfaces `case-law-2019-wi-23` → added to `related_case_law`
2. Agent sees it, incorporates the holding into its answer, and includes the node ID in `cited_doc_ids`
3. Opinion backfill detects that `case-law-2019-wi-23` is in `cited_doc_ids` but was never fetched
4. Backfill resolves the citation from Neptune, derives the S3 key, fetches the `.txt`
5. `_build_rag_documents` produces a `RAGDocument` with the full opinion text as `content`
6. ResponseStreaming regenerates the answer with the holding available

Without this companion mechanism, the agent knows about the case (step 2) but ResponseStreaming doesn't (step 5 would produce an empty card). The backfill only fires for cases in `cited_doc_ids` — uncited graph-neighbor noise is never fetched. Capped at 3 to bound latency; best-effort (failures logged and skipped). Discovery tag: `opinion-backfill`.

## Files

- `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py` — `get_chunk_statute_ids`, `rank_neighbors_by_shared_statutes`, `get_chunks_text_for_docs`
- `packages/graphrag/lambdas/agentic_retrieval/tools.py` — neighbor citation discovery block (in vector_search execution, after auto-enrichment)
- `packages/graphrag/lambdas/test/test_neptune_client.py` — unit tests for the three new Neptune methods
- `packages/graphrag/lambdas/test/test_tools.py` — integration tests for the discovery flow
