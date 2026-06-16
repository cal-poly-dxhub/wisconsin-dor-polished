# Fix: Exclude EXTRACTED_FROM from get_neighbors

## Problem

The agentic retrieval Lambda (`packages/graphrag/lambdas/agentic_retrieval/`) is blowing Bedrock's 1M token input limit. On June 16, a query hit 1,517,015 tokens and failed with `ValidationException: prompt is too long`.

### Root cause

`NeptuneClient.get_neighbors()` uses bidirectional traversal (`MATCH (d {id: $id})-[r]-(n)`) which returns Chunk nodes linked via incoming EXTRACTED_FROM edges. After the May 11–14 chunker improvements tripled chunk density, each WPAM document now has 750–940 chunks. When auto-enrichment or the model calls `get_neighbors` on a WPAM doc, it gets ~900 useless Chunk records (null title, null summary — just an id and page number) serialized into the LLM context.

Two code paths trigger this:

1. **Auto-enrichment** (`tools.py` ~line 549): After `vector_search` returns chunks, it calls `neptune.get_neighbors(doc_id)` for the top 3 parent documents with no edge filter. Three WPAM docs → 2,668 Chunk neighbors → ~653K tokens per vector_search call.

2. **`get_neighbors` tool** (`tools.py` ~line 624): When the model explicitly traverses a hub node like `WIS-STAT-70.32`, it gets 1,241 Chunk nodes that cite that statute alongside 284 actual documents. Another ~328K tokens of noise.

### Why Chunk results are useless here

- They have no title, no summary — just `id`, `heading` (often null), `source_url` (just `#page=N`), and `edition_year`
- The agent already gets relevant chunks from `vector_search` with full text
- EXTRACTED_FROM is an internal join edge (used by the vector_search Cypher, `find_stub_promotion`, and `get_chunks_for_doc`) — never intended as user-facing graph structure
- The `get_neighbors` tool description doesn't even mention EXTRACTED_FROM

### Impact before the fix

Typical requests since late May run at 55–72% of the 1M limit from a single vector_search call. The system only survived because the model usually answered on turn 2 without stacking additional large tool results. Any multi-turn investigation that hits WPAM docs risks the same failure.

## Fix

### Option A: Exclude at the Cypher level (recommended — single point of fix)

In `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py`, method `get_neighbors()` (~line 260):

Add `WHERE type(r) <> 'EXTRACTED_FROM'` to the Cypher query. This ensures no code path through `get_neighbors` ever returns Chunk nodes via this edge.

```python
def get_neighbors(
    self,
    node_id: str,
    edge_types: list[str] | None = None,
    direction: str = "both",
    limit: int = 50,
) -> list[dict]:
```

When `edge_types` is provided, the existing type filter (`-[r:CITES|IMPLEMENTS]-`) already excludes EXTRACTED_FROM, so no change needed there. When `edge_types` is None (the unfiltered case used by auto-enrichment), the query should add `WHERE type(r) <> 'EXTRACTED_FROM'`.

Also append `LIMIT {int(limit)}` to the query as a safety cap.

### Option B: Filter Chunk labels in the tool handler (defense in depth)

In `tools.py`, in both the auto-enrichment section and the `get_neighbors` tool handler, filter results:

```python
neighbors = [n for n in neighbors if "Chunk" not in (n.get("labels") or [])]
```

This catches any other edge type that might return Chunks (e.g., chunk-level CITES edges point Chunk→Statute — a bidirectional traversal of a Statute would pick those up too).

### Recommendation: do both

The Cypher-level fix (Option A) handles the EXTRACTED_FROM case cleanly. The label filter (Option B) is defense-in-depth against chunk-level CITES edges on Statute nodes. Together they reduce today's failed request from 1,517K → ~186K tokens (89% reduction).

## Files to modify

### `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py`

**Method: `get_neighbors()` (~line 260)**

- Add `limit: int = 50` parameter
- When `edge_types` is None (unfiltered traversal), add `WHERE type(r) <> 'EXTRACTED_FROM'` to the Cypher
- Append `LIMIT {int(limit)}` to all query variants
- The existing logic for when `edge_types` IS provided stays the same (those queries already exclude EXTRACTED_FROM by virtue of only matching specified types)

Current unfiltered patterns:
```python
pattern = "MATCH (d {id: $id})-[r]->(n)"   # outgoing
pattern = "MATCH (d {id: $id})<-[r]-(n)"   # incoming  
pattern = "MATCH (d {id: $id})-[r]-(n)"    # both
```

Add WHERE clause for the unfiltered case only:
```python
# After the pattern, before OPTIONAL MATCH
where_clause = " WHERE type(r) <> 'EXTRACTED_FROM'" if not edge_types else ""
```

### `packages/graphrag/lambdas/agentic_retrieval/tools.py`

**Auto-enrichment section (~line 546–566)**

No change needed if using Option A (the `get_neighbors` call will already exclude EXTRACTED_FROM). But optionally add the Chunk label filter for defense-in-depth.

**`get_neighbors` tool handler (~line 624–645)**

After the `neptune.get_neighbors()` call and after `dedupe_wpam_chunks`, filter out Chunk-labeled results:

```python
pre_filter_count = len(neighbors)
neighbors = [n for n in neighbors if "Chunk" not in (n.get("labels") or [])]
```

Log the filter count:
```python
_log_tool_event(
    "get_neighbors_complete",
    ...
    neighbor_count=len(neighbors),
    filtered_chunk_count=pre_filter_count - len(neighbors),
    ...
)
```

### `packages/graphrag/lambdas/test/test_neptune_client.py`

Add test: `get_neighbors` with no edge_types includes `EXTRACTED_FROM` exclusion in the Cypher and includes LIMIT.

### `packages/graphrag/lambdas/test/test_tools.py`

Add test: `get_neighbors` tool handler filters out Chunk-labeled results from the return value.

Add test: auto-enrichment in vector_search does not include Chunk-labeled neighbors in `graph_context`.

## Verification

1. Run tests: `uv run pytest packages/graphrag/lambdas/test/ -v`
2. Deploy to us-east-1: `cd packages/infra && AWS_PROFILE=widor AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never`
3. Test the failing question: "If a subject property recently sold along with other neighboring properties, what sale must an assessor first consider when determining the assessed value of the subject property?"
4. Check CloudWatch logs:
   - `vector_search_auto_enrichment_complete` → `neighbor_count` should be ~40 per doc (not 900)
   - `vector_search_complete` → `graph_context_neighbor_count` should be ~120 total (not 2,668)
   - `get_neighbors_complete` → `neighbor_count` should be ≤50, `filtered_chunk_count` should show how many were dropped
   - `agent_turn_model_response` → `input_tokens` should be ~40K–80K on turn 2 (not 650K+)
5. Verify the answer still cites relevant statutes, WPAM sections, and case law

## Context: what NOT to change

- Do NOT remove EXTRACTED_FROM edges from the graph — they're used by:
  - `vector_search` Cypher (line 188-199 in neptune_client.py): `OPTIONAL MATCH (node)-[:EXTRACTED_FROM]->(parent)` for chunk→doc resolution
  - `find_stub_promotion` (line 237): traverses chunk→stub→parent for citation cards
  - `get_chunks_for_doc` (line 323): `MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id})` for full doc retrieval
- These are all dedicated, targeted queries — they don't go through `get_neighbors`
