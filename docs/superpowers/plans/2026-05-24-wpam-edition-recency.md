# WPAM Edition Recency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture WPAM edition years on graph nodes and dedupe near-duplicate chunks across editions at retrieval time, so the agent sees the most recent edition by default unless the user asks about a specific year.

**Architecture:** Three pipeline boundaries — (1) phase 2/8 of `scripts/graphrag/load.py` writes `edition_year` onto WPAM Doc and Chunk nodes; (2) `refine_query` tool surfaces `target_wpam_year` when the user mentions a year; (3) a new `wpam_dedup.py` helper, called from `vector_search` and `get_neighbors`, collapses near-duplicate WPAM chunks by `(framework_id, normalized_section_path)` and keeps the chunk for the target year (or max year). All changes additive; existing chunks without `edition_year` pass through.

**Tech Stack:** Python 3.13 (lambdas) and Python (load script), Pydantic v2, Neptune Analytics OpenCypher, pytest, Bedrock Converse API. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-24-wpam-edition-recency-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py` | Create | Pure dedup function over chunk lists |
| `packages/graphrag/lambdas/test/test_wpam_dedup.py` | Create | Unit tests for the dedup helper |
| `scripts/graphrag/wpam_year.py` | Create | Year extraction from S3 prefix or PDF text |
| `scripts/graphrag/tests/test_wpam_year.py` | Create | Unit tests for year extractor |
| `scripts/graphrag/load.py` | Modify | Phase 2 writes `edition_year` on Doc nodes; phase 8 writes it on Chunk nodes |
| `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py` | Modify | `vector_search` + `get_neighbors` Cypher returns `edition_year`; `get_neighbors` returns `framework_id` + `heading` for dedup grouping |
| `packages/graphrag/lambdas/agentic_retrieval/tools.py` | Modify | `refine_query` returns `target_wpam_year`; `vector_search`/`get_neighbors` accept and forward it; both call `dedupe_wpam_chunks` |
| `packages/graphrag/lambdas/agentic_retrieval/prompt.py` | Modify | New CITATION RULES bullet about WPAM edition awareness |
| `packages/graphrag/lambdas/test/test_tools.py` | Modify | Tests for `refine_query` target-year extraction and dedup integration |
| `packages/shared/lambda_layers/step_function_types/models.py` | Modify | Add `edition_year: int \| None` to `RAGDocument` |
| `packages/messages/types/message-types.ts` | Modify | Add `editionYear` to Zod `SourceDocumentSchema` |
| `packages/webapp/src/stores/types.ts` | Modify | Add `editionYear?: number` to frontend `Document` (wire-only, not displayed) |

---

## Task 1: Create the year-extraction helper

**Files:**
- Create: `scripts/graphrag/wpam_year.py`
- Create: `scripts/graphrag/tests/test_wpam_year.py`

This helper is called during phase 2 to derive `edition_year` for each WPAM doc. Prefix regex first, PDF text fallback. Both methods failing = returns None and the loader proceeds without the property.

- [ ] **Step 1: Write the failing tests**

Create `scripts/graphrag/tests/test_wpam_year.py`:

```python
"""Tests for WPAM edition_year extraction."""

import pytest

from scripts.graphrag.wpam_year import extract_wpam_year_from_doc_id, extract_wpam_year_from_pdf_text


def test_extract_year_from_simple_prefix():
    assert extract_wpam_year_from_doc_id("wpam-wisconsin-property-assessment-manual-2025") == 2025


def test_extract_year_from_volume_prefix():
    """Vol-1-2011 has multiple digit groups; we want the LAST 4-digit group."""
    assert extract_wpam_year_from_doc_id("wpam-wisconsin-property-assessment-manual-vol-1-2011") == 2011


def test_extract_year_returns_none_on_no_year():
    assert extract_wpam_year_from_doc_id("wpam-wisconsin-property-assessment-manual") is None


def test_extract_year_returns_none_on_non_wpam_prefix():
    assert extract_wpam_year_from_doc_id("statutes-70-32") is None


def test_extract_year_rejects_implausible_year():
    """We only accept years in [2010, current_year+1]. 1999 is too old."""
    assert extract_wpam_year_from_doc_id("wpam-wisconsin-property-assessment-manual-1999") is None


def test_pdf_text_extracts_explicit_year():
    text = "2024 Wisconsin Property Assessment Manual\nPublished by the Wisconsin Department of Revenue"
    assert extract_wpam_year_from_pdf_text(text) == 2024


def test_pdf_text_extracts_year_from_effective_date_phrase():
    text = "Wisconsin Property Assessment Manual\neffective January 2026 for use during the 2026 assessment year"
    assert extract_wpam_year_from_pdf_text(text) == 2026


def test_pdf_text_returns_none_on_no_year():
    text = "Wisconsin Property Assessment Manual\nPublished by the Wisconsin Department of Revenue"
    assert extract_wpam_year_from_pdf_text(text) is None


def test_pdf_text_rejects_implausible_years():
    """A document might mention historical years (e.g. '1879 statute');
    those should not be picked up."""
    text = "Originally enacted in 1879 and amended several times since."
    assert extract_wpam_year_from_pdf_text(text) is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest scripts/graphrag/tests/test_wpam_year.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.graphrag.wpam_year'`.

- [ ] **Step 3: Implement the helper**

Create `scripts/graphrag/wpam_year.py`:

```python
"""WPAM edition_year extraction.

Two strategies, called in order by the loader:
  1. Parse the LAST 4-digit group from the doc_id (S3 prefix). Cheap,
     deterministic, handles `wpam-...-2025` and `wpam-...-vol-1-2011`.
  2. Scan the first ~3 pages of PDF text for a 4-digit year in a
     plausible range. Used when the prefix lacks a year.

Both failing returns None — the loader then writes the doc without
edition_year and logs a warning. Downstream dedup treats absent
edition_year as "ineligible for dedup, pass through."
"""

import re
from datetime import datetime

# Plausible range: WPAM editions exist from 2011 onward; allow next year
# for the December-published edition that's effective the following year.
_MIN_YEAR = 2010
_MAX_YEAR_OFFSET = 1

_DOC_ID_YEAR_RE = re.compile(r"(\d{4})(?!.*\d{4})")
_PDF_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _max_year() -> int:
    return datetime.utcnow().year + _MAX_YEAR_OFFSET


def _is_plausible(year: int) -> bool:
    return _MIN_YEAR <= year <= _max_year()


def extract_wpam_year_from_doc_id(doc_id: str) -> int | None:
    """Extract edition year from the doc_id (S3 prefix). Returns None
    if doc_id is not a WPAM prefix or has no plausible 4-digit year."""
    if not doc_id.startswith("wpam-"):
        return None
    match = _DOC_ID_YEAR_RE.search(doc_id)
    if not match:
        return None
    year = int(match.group(1))
    return year if _is_plausible(year) else None


def extract_wpam_year_from_pdf_text(text: str) -> int | None:
    """Scan PDF text for the most-frequent plausible 4-digit year.
    The cover/first pages of WPAM mention the edition year multiple times;
    we use frequency to disambiguate from incidentally cited years
    (statute enactment dates, historical references)."""
    if not text:
        return None
    counts: dict[int, int] = {}
    for match in _PDF_YEAR_RE.finditer(text):
        year = int(match.group(1))
        if _is_plausible(year):
            counts[year] = counts.get(year, 0) + 1
    if not counts:
        return None
    # Require at least 2 mentions to count as the edition year — guards
    # against single incidental mentions like "originally enacted 1879".
    most_common_year, count = max(counts.items(), key=lambda kv: kv[1])
    if count < 2:
        return None
    return most_common_year
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest scripts/graphrag/tests/test_wpam_year.py -v`

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/graphrag/wpam_year.py scripts/graphrag/tests/test_wpam_year.py
git commit -m "feat(graphrag): add WPAM edition_year extractor

Prefix regex first (last 4-digit group), PDF text fallback
requiring ≥2 plausible-year mentions to filter out incidental
historical years. Returns None when neither strategy succeeds."
```

---

## Task 2: Wire `edition_year` into phase 2 (Doc nodes) of `load.py`

**Files:**
- Modify: `scripts/graphrag/load.py:159-198` (phase_2_document_nodes)

The doc-id-based extractor runs synchronously per doc. PDF text fallback (`extract_wpam_year_from_pdf_text`) is built in Task 1 but **not yet wired in** — all 15 current WPAM prefixes match the regex, so paying the S3-fetch cost on every load would be premature. If a future upload doesn't conform to the naming convention, the warning logged here flags the gap and the fallback can be wired in then. If a WPAM doc lacks a year in its prefix today, we log a warning and write the doc without `edition_year` (downstream dedup handles absent values).

- [ ] **Step 1: Read the existing phase 2 implementation**

Read `scripts/graphrag/load.py:159-198` to understand the current MERGE pattern. Phase 2 writes Doc nodes with: `id, title, source_key, summary, source_url, doc_type, authority_level, citation, effective_date`.

- [ ] **Step 2: Modify phase_2_document_nodes to set edition_year for WPAM docs**

Edit `scripts/graphrag/load.py` — replace the `phase_2_document_nodes` function with:

```python
def phase_2_document_nodes(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 2: Creating document nodes...")

    from wpam_year import extract_wpam_year_from_doc_id

    doc_type_to_label = config.get("doc_types", {})
    count = 0
    wpam_year_misses = 0

    for doc in documents:
        doc_type = doc.get("doc_type", "guide")
        label = doc_type_to_label.get(doc_type, "Guide")

        edition_year = None
        if doc.get("framework_id") == "FW-WPAM":
            edition_year = extract_wpam_year_from_doc_id(doc["doc_id"])
            if edition_year is None:
                wpam_year_misses += 1
                logger.warning(
                    f"Phase 2: WPAM doc '{doc['doc_id']}' has no extractable edition_year; "
                    "loading without the property"
                )

        execute_query(client, graph_id,
            f"MERGE (d:{label} {{id: $id}}) "
            f"SET d.title = $title, d.source_key = $source_key, "
            f"d.summary = $summary, d.source_url = $source_url, "
            f"d.doc_type = $doc_type, d.authority_level = $auth_level, "
            f"d.citation = $citation, d.effective_date = $effective_date, "
            f"d.edition_year = $edition_year",
            {
                "id": doc["doc_id"],
                "title": doc.get("title", doc["doc_id"]),
                "source_key": doc.get("s3_key", ""),
                "summary": doc.get("summary", ""),
                "source_url": doc.get("source_url", ""),
                "doc_type": doc_type,
                "auth_level": doc.get("authority_level", 6),
                "citation": doc.get("citation", ""),
                "effective_date": doc.get("effective_date", ""),
                "edition_year": edition_year,
            },
        )

        fw_id = doc.get("framework_id", "FW-GOV-PUBS")
        execute_query(client, graph_id,
            f"MATCH (d:{label} {{id: $doc_id}}), (f:Framework {{id: $fw_id}}) "
            "MERGE (d)-[:BELONGS_TO]->(f)",
            {"doc_id": doc["doc_id"], "fw_id": fw_id},
        )
        count += 1
        if count % 200 == 0:
            logger.info(f"  Phase 2 progress: {count}/{len(documents)} document nodes")

    if wpam_year_misses:
        logger.warning(
            f"Phase 2: {wpam_year_misses} WPAM docs loaded without edition_year"
        )
    logger.info(f"  Created {count} document nodes")
```

- [ ] **Step 3: Verify the change with a syntax check and a logic sanity check**

Run: `uv run python -c "from scripts.graphrag.load import phase_2_document_nodes; print('imports OK')"`

Expected: `imports OK` (and no SyntaxError).

Also run: `uv run python -c "from scripts.graphrag.wpam_year import extract_wpam_year_from_doc_id; assert extract_wpam_year_from_doc_id('wpam-wisconsin-property-assessment-manual-2025') == 2025; print('OK')"`

Expected: `OK`.

Note on imports: tests use `from scripts.graphrag.wpam_year import ...` (project-rooted), but inside `load.py` (which is invoked as a script via `python3 scripts/graphrag/load.py`), the import must be sibling-style: `from wpam_year import extract_wpam_year_from_doc_id`. Both files live in the same directory, which is on sys.path when `load.py` is invoked directly.

- [ ] **Step 4: Commit**

```bash
git add scripts/graphrag/load.py
git commit -m "feat(graphrag): set edition_year on WPAM Doc nodes in phase 2

Prefix-based extraction; WPAM docs without an extractable year
log a warning and load without the property (downstream dedup
treats absent edition_year as ineligible)."
```

---

## Task 3: Wire `edition_year` into phase 8 (Chunk nodes) of `load.py`

**Files:**
- Modify: `scripts/graphrag/load.py:584-680` (chunk batch flush + phase_8_chunks)

Chunks denormalize `edition_year` from their parent doc so the dedup helper doesn't need a Neptune join. We compute the year once per doc, then attach to every chunk in `_flush_chunk_batch` via the row payload.

- [ ] **Step 1: Read the existing chunk batch flush**

Read `scripts/graphrag/load.py:584-680`. The chunk batch is built per-document by `phase_8_chunks` (calls `_flush_chunk_batch`). Each chunk row carries `chunk_id, text, doc_id, source_url, idx, s3_key, start_page, end_page, heading, subheading`.

- [ ] **Step 2: Modify _flush_chunk_batch to accept and write edition_year**

Edit `scripts/graphrag/load.py` — modify the chunk MERGE in the batch flush. Find the block starting `# 1. Chunk nodes (single UNWIND MERGE, sets all scalar props).` and replace it with:

```python
    # 1. Chunk nodes (single UNWIND MERGE, sets all scalar props).
    execute_query(client, graph_id,
        "UNWIND $rows AS row "
        "MERGE (c:Chunk {id: row.id}) "
        "SET c.text = row.text, c.doc_id = row.doc_id, "
        "c.source_url = row.source_url, c.chunk_index = row.idx, "
        "c.s3_key = row.s3_key, c.start_page = row.start_page, "
        "c.end_page = row.end_page, c.heading = row.heading, "
        "c.subheading = row.subheading, c.edition_year = row.edition_year",
        {"rows": [
            {
                "id": b["chunk_id"],
                "text": b["text"],
                "doc_id": b["doc_id"],
                "source_url": b["source_url"],
                "idx": b["idx"],
                "s3_key": b["s3_key"],
                "start_page": b["start_page"],
                "end_page": b["end_page"],
                "heading": b["heading"],
                "subheading": b["subheading"],
                "edition_year": b.get("edition_year"),
            } for b in batch
        ]},
    )
```

- [ ] **Step 3: Modify phase_8_chunks to compute edition_year per doc and attach to each chunk**

Edit `scripts/graphrag/load.py` — at the top of `phase_8_chunks`, add the import and a helper. Then modify wherever the chunk-row dicts are constructed (look for the `_flush_chunk_batch` callers in the function body) to attach `edition_year`. Apply this pattern at the chunk-row construction site:

```python
def phase_8_chunks(client, graph_id: str, documents: list[dict]):
    from wpam_year import extract_wpam_year_from_doc_id

    logger.info(
        f"Phase 8: Creating chunk nodes with headings + chunk-level CITES edges "
        f"for {len(documents)} documents..."
    )

    # ... existing code ...

    for doc in documents:
        doc_id = doc["doc_id"]
        edition_year = None
        if doc.get("framework_id") == "FW-WPAM":
            edition_year = extract_wpam_year_from_doc_id(doc_id)

        for chunk_idx, chunk in enumerate(doc.get("chunks", [])):
            row = {
                "chunk_id": f"{doc_id}-c{chunk_idx}",
                "text": chunk["text"],
                "doc_id": doc_id,
                "source_url": doc.get("source_url", ""),
                "idx": chunk_idx,
                "s3_key": doc.get("s3_key", ""),
                "start_page": chunk.get("start_page", 0),
                "end_page": chunk.get("end_page", 0),
                "heading": chunk.get("heading", ""),
                "subheading": chunk.get("subheading", ""),
                "statute_refs": chunk.get("statute_refs", []),
                "admin_rule_refs": chunk.get("admin_rule_refs", []),
                "edition_year": edition_year,
            }
            # ... rest of existing logic that appends to batch and flushes ...
```

**Important:** You must read the existing `phase_8_chunks` body and find the actual chunk-row construction sites (likely a list comprehension or similar). Add `"edition_year": edition_year` to every chunk-row dict literal in the function. Compute `edition_year` ONCE per outer doc loop, then reference it for each chunk.

- [ ] **Step 4: Run a syntax check**

Run: `uv run python -c "from scripts.graphrag.load import phase_8_chunks; print('OK')"`

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/graphrag/load.py
git commit -m "feat(graphrag): denormalize edition_year onto WPAM Chunk nodes

Chunks carry edition_year inherited from their parent WPAM Doc so
dedup at retrieval time doesn't need a Neptune join. Non-WPAM
chunks pass through with edition_year=None."
```

---

## Task 4: Add `edition_year` and `framework_id` to Cypher RETURN clauses

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py:176-245` (`vector_search` and `get_neighbors`)

The dedup helper needs `framework_id` (to identify WPAM chunks) and `edition_year` (to choose the survivor). `vector_search` already does an OPTIONAL MATCH on the parent doc; we extend it to also fetch the parent's `framework_id`. `get_neighbors` does not currently match through to the framework, so we add an OPTIONAL MATCH for that case.

- [ ] **Step 1: Modify vector_search Cypher to return framework_id and edition_year**

Edit `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py` — replace the `vector_search` method body with:

```python
    def vector_search(self, embedding: list[float], top_k: int = 10) -> list[dict]:
        """Search for similar chunks using Neptune's vector index.

        Neptune Analytics does not support parameterization inside CALL
        procedures, so the embedding and topK are inlined into the query.
        """
        embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        # OPTIONAL MATCH on the parent doc surfaces effective_date alongside
        # each chunk so the agent can prefer newer Advisory results when two
        # chunks address the same topic. Cheap join — chunk→doc is 1:1.
        # framework_id and edition_year flow through for WPAM dedup.
        results = self.query(
            f"CALL neptune.algo.vectors.topKByEmbedding({embedding_literal}, {{topK: {top_k}}}) "
            "YIELD node, score "
            "OPTIONAL MATCH (node)-[:EXTRACTED_FROM]->(parent) "
            "OPTIONAL MATCH (parent)-[:BELONGS_TO]->(fw:Framework) "
            "RETURN node.id AS chunk_id, node.text AS text, node.doc_id AS doc_id, "
            "node.source_url AS source_url, node.s3_key AS s3_key, "
            "node.start_page AS start_page, node.end_page AS end_page, "
            "node.heading AS heading, node.subheading AS subheading, "
            "node.edition_year AS edition_year, "
            "fw.id AS framework_id, "
            "parent.effective_date AS effective_date, score",
            query_name="vector_search",
        )
        return results
```

- [ ] **Step 2: Modify get_neighbors Cypher to return framework_id and edition_year**

Edit `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py` — replace the `get_neighbors` method body with:

```python
    def get_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """Get neighboring nodes via specified edge types."""
        if edge_types:
            type_filter = "|".join(edge_types)
            if direction == "outgoing":
                pattern = f"MATCH (d {{id: $id}})-[r:{type_filter}]->(n)"
            elif direction == "incoming":
                pattern = f"MATCH (d {{id: $id}})<-[r:{type_filter}]-(n)"
            else:
                pattern = f"MATCH (d {{id: $id}})-[r:{type_filter}]-(n)"
        else:
            if direction == "outgoing":
                pattern = "MATCH (d {id: $id})-[r]->(n)"
            elif direction == "incoming":
                pattern = "MATCH (d {id: $id})<-[r]-(n)"
            else:
                pattern = "MATCH (d {id: $id})-[r]-(n)"

        results = self.query(
            f"{pattern} "
            "OPTIONAL MATCH (n)-[:BELONGS_TO]->(fw:Framework) "
            "RETURN type(r) AS relationship, n.id AS id, n.title AS title, "
            "n.summary AS summary, n.source_url AS source_url, "
            "n.doc_type AS doc_type, n.citation AS citation, "
            "n.effective_date AS effective_date, "
            "n.edition_year AS edition_year, "
            "n.heading AS heading, "
            "fw.id AS framework_id, "
            "labels(n) AS labels",
            {"id": node_id},
            query_name="get_neighbors",
        )
        return results
```

- [ ] **Step 3: Run existing neptune_client tests to verify no regression**

Run: `uv run pytest packages/graphrag/lambdas/test/test_neptune_client.py -v`

Expected: all existing tests pass. (These are mock-based and don't care about extra RETURN fields, but verify shape assumptions still hold.)

- [ ] **Step 4: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/neptune_client.py
git commit -m "feat(graphrag): surface framework_id and edition_year in retrieval

vector_search and get_neighbors now return framework_id (via
BELONGS_TO traversal) and node-level edition_year. These power
WPAM dedup at the tool layer."
```

---

## Task 5: Create the dedup helper

**Files:**
- Create: `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py`
- Create: `packages/graphrag/lambdas/test/test_wpam_dedup.py`

Pure function with no I/O. Uses `(framework_id, normalized_section_path)` as the dedup grouping key. Groups of 1 (singletons) pass through unchanged. WPAM chunks missing `edition_year` are held aside and never deduped.

- [ ] **Step 1: Write the failing tests**

Create `packages/graphrag/lambdas/test/test_wpam_dedup.py`:

```python
"""Unit tests for WPAM cross-edition chunk deduplication."""

import pytest

from wpam_dedup import dedupe_wpam_chunks


def _wpam_chunk(year: int, heading: str, chunk_id: str = None, doc_id: str = None) -> dict:
    """Helper: build a WPAM chunk dict with the fields dedup needs."""
    return {
        "chunk_id": chunk_id or f"wpam-{year}-c{heading[:5]}",
        "doc_id": doc_id or f"wpam-wisconsin-property-assessment-manual-{year}",
        "framework_id": "FW-WPAM",
        "edition_year": year,
        "heading": heading,
        "text": f"text from {year} {heading}",
    }


def test_collapses_same_heading_across_editions_to_max_year():
    chunks = [
        _wpam_chunk(2020, "Manufactured Homes"),
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
        _wpam_chunk(2022, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025


def test_target_year_overrides_max_year():
    chunks = [
        _wpam_chunk(2020, "Manufactured Homes"),
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2018)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2018


def test_target_year_not_present_falls_back_to_max():
    """User asked about 2017 but only 2018-2025 exist — give them the latest."""
    chunks = [
        _wpam_chunk(2018, "Manufactured Homes"),
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=2017)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025


def test_singleton_unique_to_one_edition_survives():
    """Content that exists only in 2018 (no peer in any other year) must pass through."""
    chunks = [
        _wpam_chunk(2018, "Deprecated Topic Removed In 2019"),
        _wpam_chunk(2025, "Modern Section"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 2
    assert {c["heading"] for c in result} == {
        "Deprecated Topic Removed In 2019",
        "Modern Section",
    }


def test_non_wpam_chunks_pass_through_unchanged():
    chunks = [
        {"chunk_id": "stat-1", "framework_id": "FW-STATUTES", "heading": "70.32",
         "doc_id": "statutes-70-32", "text": "statute text"},
        _wpam_chunk(2018, "WPAM section"),
        _wpam_chunk(2025, "WPAM section"),
        {"chunk_id": "stat-2", "framework_id": "FW-STATUTES", "heading": "70.33",
         "doc_id": "statutes-70-33", "text": "another statute"},
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 3
    framework_ids = [c["framework_id"] for c in result]
    assert framework_ids.count("FW-STATUTES") == 2
    assert framework_ids.count("FW-WPAM") == 1


def test_wpam_chunk_missing_edition_year_passes_through():
    """An old WPAM chunk loaded before this feature has no edition_year.
    It must NOT be deduped against newer chunks (we can't tell which is newer)."""
    chunks = [
        {"chunk_id": "old", "doc_id": "wpam-...", "framework_id": "FW-WPAM",
         "heading": "Manufactured Homes", "text": "old text"},
        _wpam_chunk(2025, "Manufactured Homes"),
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 2


def test_empty_input_returns_empty():
    assert dedupe_wpam_chunks([], target_year=None) == []
    assert dedupe_wpam_chunks([], target_year=2020) == []


def test_normalizes_heading_whitespace_and_case():
    """Same section, slightly different heading whitespace/case across editions."""
    chunks = [
        {"chunk_id": "c1", "doc_id": "wpam-2018", "framework_id": "FW-WPAM",
         "edition_year": 2018, "heading": "Manufactured  Homes", "text": "..."},
        {"chunk_id": "c2", "doc_id": "wpam-2025", "framework_id": "FW-WPAM",
         "edition_year": 2025, "heading": "manufactured homes", "text": "..."},
    ]
    result = dedupe_wpam_chunks(chunks, target_year=None)
    assert len(result) == 1
    assert result[0]["edition_year"] == 2025
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest packages/graphrag/lambdas/test/test_wpam_dedup.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'wpam_dedup'`.

- [ ] **Step 3: Implement the helper**

Create `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py`:

```python
"""WPAM cross-edition chunk deduplication.

The WPAM is republished annually. Without dedup, vector_search returns
near-identical chunks from many editions. This helper collapses them
to one chunk per (framework, normalized_section_heading), preferring
either the user-specified target year or the most recent edition.

Singletons (chunks unique to one edition) survive — we only collapse
groups with multiple peers. WPAM chunks missing edition_year (e.g.,
loaded before this feature shipped) pass through unchanged because
we can't compare them.
"""

import logging
import re

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_heading(heading: str | None) -> str:
    """Collapse whitespace and lowercase. Stable across cosmetic edits."""
    if not heading:
        return ""
    return _WHITESPACE_RE.sub(" ", heading).strip().lower()


def _is_wpam(chunk: dict) -> bool:
    return chunk.get("framework_id") == "FW-WPAM"


def _can_dedup(chunk: dict) -> bool:
    """A WPAM chunk is dedup-eligible iff it has both an edition_year
    (so we can pick a survivor) and a non-empty heading (so we can
    group it with peers)."""
    return (
        _is_wpam(chunk)
        and chunk.get("edition_year") is not None
        and bool(_normalize_heading(chunk.get("heading")))
    )


def _pick_survivor(group: list[dict], target_year: int | None) -> dict:
    """Pick one chunk from a group sharing the same heading.
    Prefer target_year if present; otherwise max(edition_year)."""
    if target_year is not None:
        for chunk in group:
            if chunk.get("edition_year") == target_year:
                return chunk
    return max(group, key=lambda c: c.get("edition_year", 0))


def dedupe_wpam_chunks(
    chunks: list[dict],
    target_year: int | None = None,
) -> list[dict]:
    """Collapse near-duplicate WPAM chunks across editions.

    Args:
        chunks: List of chunk dicts. Each chunk must have framework_id,
            edition_year, and heading for dedup eligibility.
        target_year: If set, prefer chunks from this year over max year.

    Returns:
        A new list with dedup applied to WPAM chunks. Non-WPAM chunks
        and dedup-ineligible WPAM chunks pass through in their original
        positions.
    """
    if not chunks:
        return []

    # Group dedup-eligible WPAM chunks by normalized heading.
    groups: dict[str, list[dict]] = {}
    eligible_indexes: set[int] = set()
    for idx, chunk in enumerate(chunks):
        if _can_dedup(chunk):
            key = _normalize_heading(chunk["heading"])
            groups.setdefault(key, []).append(chunk)
            eligible_indexes.add(idx)

    # Pick survivors per group.
    survivors_by_id: dict[str, dict] = {}
    drops = 0
    for key, group in groups.items():
        if len(group) == 1:
            # Singleton — passes through unchanged.
            chunk = group[0]
            survivors_by_id[chunk["chunk_id"]] = chunk
            continue
        survivor = _pick_survivor(group, target_year)
        survivors_by_id[survivor["chunk_id"]] = survivor
        drops += len(group) - 1

    if drops:
        logger.info(
            "wpam_dedup: collapsed %d duplicate WPAM chunks across %d heading groups "
            "(target_year=%s)",
            drops,
            sum(1 for g in groups.values() if len(g) > 1),
            target_year,
        )

    # Walk the original list and emit survivors + ineligible chunks.
    result: list[dict] = []
    for idx, chunk in enumerate(chunks):
        if idx not in eligible_indexes:
            result.append(chunk)
            continue
        if chunk["chunk_id"] in survivors_by_id:
            result.append(chunk)
            del survivors_by_id[chunk["chunk_id"]]
    return result
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest packages/graphrag/lambdas/test/test_wpam_dedup.py -v`

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py packages/graphrag/lambdas/test/test_wpam_dedup.py
git commit -m "feat(graphrag): add WPAM cross-edition chunk dedup helper

Pure function. Groups WPAM chunks by (framework, normalized
heading); picks target_year or max(year) per group. Singletons
and dedup-ineligible chunks (missing year or heading) pass
through unchanged."
```

---

## Task 6: Extend `refine_query` to surface `target_wpam_year`

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py:432-475` (refine_query branch in execute_tool)
- Modify: `packages/graphrag/lambdas/test/test_tools.py` (add tests)

The refine_query LLM is asked to also extract a target year when the user mentions one in a WPAM context. The LLM returns JSON; we parse it. On parse failure, fall back to `target_wpam_year=None` (graceful degradation).

- [ ] **Step 1: Write the failing tests**

Edit `packages/graphrag/lambdas/test/test_tools.py` — append:

```python
def test_refine_query_extracts_target_wpam_year():
    """LLM should return target_wpam_year when user explicitly mentions a year + WPAM."""
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": '{"refined_query": "WPAM agricultural land 2018", "target_wpam_year": 2018}'}]
            }
        }
    }
    with patch("tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does the 2018 WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "WPAM agricultural land 2018"
    assert result["target_wpam_year"] == 2018


def test_refine_query_no_target_year_when_no_year_mentioned():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": '{"refined_query": "WPAM agricultural land", "target_wpam_year": null}'}]
            }
        }
    }
    with patch("tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["target_wpam_year"] is None


def test_refine_query_falls_back_on_invalid_json():
    """If the LLM doesn't return JSON, treat the entire output as the
    refined query and target_wpam_year as None."""
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": "WPAM agricultural land"}]
            }
        }
    }
    with patch("tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "WPAM agricultural land"
    assert result["target_wpam_year"] is None


def test_refine_query_falls_back_on_bedrock_error():
    from tools import execute_tool

    mock_neptune = MagicMock()
    with patch("tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.side_effect = RuntimeError("bedrock unavailable")
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "what does WPAM say about agricultural land?"
    assert result["target_wpam_year"] is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest packages/graphrag/lambdas/test/test_tools.py -k refine_query -v`

Expected: FAIL — the existing refine_query branch doesn't return `target_wpam_year` and the prompt is plain-text, not JSON.

- [ ] **Step 3: Modify the refine_query branch in tools.py**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py` — replace the entire `elif tool_name == "refine_query":` block (currently lines 432-475) with:

```python
    elif tool_name == "refine_query":
        query = tool_input["query"]
        prompt = (
            "Rewrite the current user question as one standalone search query for "
            "Wisconsin property tax retrieval. Use the prior conversation only to "
            "resolve references or missing context.\n\n"
            "Also: if the user explicitly mentions a 4-digit year (e.g., '2018', "
            "'the 2024 manual') AND the question is about WPAM / Wisconsin Property "
            "Assessment Manual / property assessment guidance, populate "
            "target_wpam_year with that year. Otherwise, target_wpam_year is null. "
            "A year that refers only to a tax-filing deadline or a statute year is "
            "NOT a target_wpam_year.\n\n"
            "Return ONLY a JSON object on a single line, no prose, no markdown:\n"
            '{\"refined_query\": \"<rewritten query>\", \"target_wpam_year\": <year or null>}\n\n'
            f"Prior conversation:\n{_history_context(chat_history)}\n\n"
            f"Current question: {query}"
        )
        target_year: int | None = None
        try:
            response = bedrock.converse(
                modelId=REFINEMENT_MODEL_ID,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 256, "temperature": 0.0},
            )
            message = response["output"]["message"]
            raw = " ".join(
                block.get("text", "").strip()
                for block in message.get("content", [])
                if block.get("text")
            ).strip()
            try:
                parsed = json.loads(raw)
                refined = str(parsed.get("refined_query", "")).strip()
                year_value = parsed.get("target_wpam_year")
                if isinstance(year_value, int):
                    target_year = year_value
            except (json.JSONDecodeError, AttributeError, TypeError):
                # LLM didn't return JSON — treat output as refined query, no target year
                refined = raw
        except Exception as exc:  # noqa: BLE001
            _log_tool_event(
                "refine_query_error",
                logging.WARNING,
                tool_name=tool_name,
                error_type=type(exc).__name__,
                error=str(exc),
                **_query_fields(query),
            )
            refined = query

        if not refined:
            refined = query
        _log_tool_event(
            "refine_query_complete",
            tool_name=tool_name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            refined_query=refined,
            target_wpam_year=target_year,
            history_turns=len(chat_history or []),
            **_query_fields(query),
        )
        return {"refined_query": refined, "target_wpam_year": target_year}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest packages/graphrag/lambdas/test/test_tools.py -k refine_query -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py packages/graphrag/lambdas/test/test_tools.py
git commit -m "feat(graphrag): refine_query extracts target_wpam_year

LLM now returns a JSON envelope with refined_query plus an optional
target_wpam_year, populated only when the user explicitly mentions a
year in a WPAM context. Falls back gracefully on parse or API errors."
```

---

## Task 7: Wire dedup into `vector_search` and `get_neighbors`

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py:88-235` (toolSpecs) and `476-590` (execute_tool branches)
- Modify: `packages/graphrag/lambdas/test/test_tools.py` (add integration tests)

Both tools accept an optional `target_wpam_year` argument. After Neptune returns chunks, `dedupe_wpam_chunks(chunks, target_year)` is called.

- [ ] **Step 1: Write the failing integration tests**

Edit `packages/graphrag/lambdas/test/test_tools.py` — append:

```python
def test_vector_search_applies_wpam_dedup():
    """Two near-identical WPAM chunks from different years should collapse."""
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "wpam-2018-c1", "doc_id": "wpam-...-2018",
         "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "text": "old"},
        {"chunk_id": "wpam-2025-c1", "doc_id": "wpam-...-2025",
         "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "text": "new"},
    ]
    mock_neptune.get_neighbors.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "manufactured homes"},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2025


def test_vector_search_target_year_overrides_max():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "wpam-2018-c1", "doc_id": "wpam-...-2018",
         "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "text": "..."},
        {"chunk_id": "wpam-2025-c1", "doc_id": "wpam-...-2025",
         "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "text": "..."},
    ]
    mock_neptune.get_neighbors.return_value = []

    with patch("tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "2018 manufactured homes", "target_wpam_year": 2018},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2018


def test_get_neighbors_applies_wpam_dedup():
    from tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_neighbors.return_value = [
        {"id": "wpam-2018-c1", "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "relationship": "CITES"},
        {"id": "wpam-2025-c1", "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "relationship": "CITES"},
        {"id": "stat-70-32", "framework_id": "FW-STATUTES",
         "heading": "70.32", "relationship": "CITES"},
    ]

    result = execute_tool(
        "get_neighbors",
        {"node_id": "stat-70-32", "edge_types": ["CITES"]},
        mock_neptune,
    )

    # WPAM dedup'd to 1, statute passes through.
    assert len(result["neighbors"]) == 2
    wpam = [n for n in result["neighbors"] if n.get("framework_id") == "FW-WPAM"]
    assert len(wpam) == 1
    assert wpam[0]["edition_year"] == 2025
```

Note: `get_neighbors` returns nodes with id (not chunk_id) field. The dedup helper uses `chunk_id` as the survivor key — adjust `wpam_dedup.py` to also handle `id` as a fallback. Update `wpam_dedup.py` accordingly:

In `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py`, change the survivor key from `chunk["chunk_id"]` to a helper that prefers `chunk_id` then `id`:

```python
def _chunk_key(chunk: dict) -> str:
    """Some callers (vector_search) use 'chunk_id'; others (get_neighbors)
    use 'id'. Either is unique within a single tool result."""
    return chunk.get("chunk_id") or chunk.get("id") or ""
```

Then replace every `chunk["chunk_id"]` and `chunk.get("chunk_id")` reference inside `dedupe_wpam_chunks` and `_can_dedup` with `_chunk_key(chunk)`. Update `_can_dedup` to ensure the key is non-empty:

```python
def _can_dedup(chunk: dict) -> bool:
    return (
        _is_wpam(chunk)
        and chunk.get("edition_year") is not None
        and bool(_normalize_heading(chunk.get("heading")))
        and bool(_chunk_key(chunk))
    )
```

Also update the corresponding test in `test_wpam_dedup.py` to keep using `chunk_id` (the existing tests still pass — we're only widening, not changing existing behavior). The `test_get_neighbors_applies_wpam_dedup` test above uses `id`, exercising the new path.

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `uv run pytest packages/graphrag/lambdas/test/test_tools.py -k "vector_search_applies_wpam_dedup or vector_search_target_year_overrides or get_neighbors_applies_wpam_dedup" -v`

Expected: FAIL — `vector_search` doesn't currently call dedup, and `get_neighbors` toolSpec doesn't accept `target_wpam_year`.

- [ ] **Step 3: Update `wpam_dedup.py` to accept either chunk_id or id**

Edit `packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py`:

Add the helper and update references. Replace the file contents to match the design from Task 5 plus the `_chunk_key` helper. Specifically, add `_chunk_key` after `_normalize_heading`, modify `_can_dedup` to call it, and replace `chunk["chunk_id"]` with `_chunk_key(chunk)` in `dedupe_wpam_chunks`.

- [ ] **Step 4: Re-run the existing dedup tests to verify no regression**

Run: `uv run pytest packages/graphrag/lambdas/test/test_wpam_dedup.py -v`

Expected: 8 passed (existing tests still green).

- [ ] **Step 5: Modify the vector_search toolSpec to accept target_wpam_year**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py` — find the `vector_search` toolSpec (around line 149-175) and replace its `inputSchema` with:

```python
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant chunks",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10, max: 20)",
                            "default": 10,
                        },
                        "target_wpam_year": {
                            "type": ["integer", "null"],
                            "description": (
                                "Optional. If the user explicitly asked about a "
                                "specific WPAM edition year, pass it here so dedup "
                                "returns chunks from that edition instead of the "
                                "most recent. Use the value returned by refine_query."
                            ),
                        },
                    },
                    "required": ["query"],
                }
            },
```

- [ ] **Step 6: Modify the get_neighbors toolSpec the same way**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py` — find the `get_neighbors` toolSpec (around line 199-235) and add `target_wpam_year` to its `inputSchema.properties` (same definition as above).

- [ ] **Step 7: Wire dedup into the vector_search execute branch**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py` — at the top, add:

```python
from wpam_dedup import dedupe_wpam_chunks
```

(near the existing `from neptune_client import NeptuneClient` import).

Then in the `elif tool_name == "vector_search":` branch, immediately after `chunks = neptune.vector_search(embedding, top_k=top_k)` and BEFORE the `_log_tool_event("vector_search_neptune_complete", ...)` call, insert:

```python
        target_year = tool_input.get("target_wpam_year")
        pre_dedup_count = len(chunks)
        chunks = dedupe_wpam_chunks(chunks, target_year=target_year)
```

Update the `vector_search_neptune_complete` log call to include `pre_dedup_count=pre_dedup_count, target_wpam_year=target_year` so the trace shows both the pre- and post-dedup counts. Auto-enrichment continues unchanged below — it operates on the already-dedup'd chunk list, which is the right behavior (we don't want to enrich neighbors of dropped chunks).

- [ ] **Step 8: Wire dedup into the get_neighbors execute branch**

Edit `packages/graphrag/lambdas/agentic_retrieval/tools.py` — in the `elif tool_name == "get_neighbors":` branch (currently lines 572-590), after `neighbors = neptune.get_neighbors(...)`:

```python
        target_year = tool_input.get("target_wpam_year")
        neighbors = dedupe_wpam_chunks(neighbors, target_year=target_year)
```

Update the `get_neighbors_complete` log event to add `target_wpam_year=target_year`.

- [ ] **Step 9: Run the full test suite and verify it passes**

Run: `uv run pytest packages/graphrag/lambdas/test/ -v`

Expected: all tests pass, including the 3 new integration tests from Step 1.

- [ ] **Step 10: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py \
        packages/graphrag/lambdas/agentic_retrieval/wpam_dedup.py \
        packages/graphrag/lambdas/test/test_tools.py
git commit -m "feat(graphrag): apply WPAM dedup in vector_search and get_neighbors

Both tools now accept an optional target_wpam_year and call
dedupe_wpam_chunks on Neptune results. wpam_dedup widened to
accept either chunk_id or id as the unique key (vector_search
uses chunk_id; get_neighbors uses id)."
```

---

## Task 8: Update agent prompt to teach the year-aware flow

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/prompt.py:81-99` (CITATION RULES)

The agent already calls `refine_query` for follow-ups. We need to (a) tell it to forward `target_wpam_year` to vector_search/get_neighbors, and (b) tell it that WPAM tool results are already deduplicated.

- [ ] **Step 1: Read the current CITATION RULES section**

Read `packages/graphrag/lambdas/agentic_retrieval/prompt.py:81-99`.

- [ ] **Step 2: Insert a new bullet under the ALWAYS list**

Edit `packages/graphrag/lambdas/agentic_retrieval/prompt.py` — find the ALWAYS bullet list under `## CITATION RULES`. Insert a new bullet after the existing "Note when guidance has been SUPERSEDED" bullet:

```python
    "- The Wisconsin Property Assessment Manual (WPAM) is republished annually. "
    "Tool results from vector_search and get_neighbors are already deduplicated to the "
    "most recent edition unless the user asked about a specific year. The "
    "`edition_year` field on each chunk is your ground truth for which manual it came "
    "from. If `refine_query` returned a `target_wpam_year`, pass it to your subsequent "
    "vector_search and get_neighbors calls so the dedup picks chunks from that edition."
```

(Placement: insert this as a new bullet immediately after the existing line "- Note when guidance has been SUPERSEDED (check SUPERSEDES edges).")

- [ ] **Step 3: Verify the file still imports cleanly**

Run: `uv run python -c "from prompt import SYSTEM_PROMPT; assert 'WPAM' in SYSTEM_PROMPT and 'edition_year' in SYSTEM_PROMPT; print('OK')" 2>&1 || cd packages/graphrag/lambdas/agentic_retrieval && uv run python -c "from prompt import SYSTEM_PROMPT; assert 'WPAM' in SYSTEM_PROMPT and 'edition_year' in SYSTEM_PROMPT; print('OK')"`

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/prompt.py
git commit -m "feat(graphrag): teach agent the WPAM edition-year flow

Tells the agent to forward refine_query's target_wpam_year to
vector_search and get_neighbors, and that WPAM tool results
are pre-deduplicated to the latest edition by default."
```

---

## Task 9: Plumb `edition_year` through the shared types layer

**Files:**
- Modify: `packages/shared/lambda_layers/step_function_types/models.py:59-77` (RAGDocument)
- Modify: `packages/messages/types/message-types.ts:3-17` (SourceDocumentSchema)
- Modify: `packages/webapp/src/stores/types.ts:54-64` (Document interface)

Wire-only — no UI display in v1.

- [ ] **Step 1: Add edition_year to RAGDocument**

Edit `packages/shared/lambda_layers/step_function_types/models.py` — in the `RAGDocument` class (around line 59), add a new field after `end_page`:

```python
    end_page: int | None = Field(default=None)
    # Optional: WPAM edition year (e.g., 2025). Set on chunks from the
    # Wisconsin Property Assessment Manual; null on all other doc types.
    edition_year: int | None = Field(default=None)
```

- [ ] **Step 2: Add editionYear to the Zod schema**

Edit `packages/messages/types/message-types.ts` — in `SourceDocumentSchema` (lines 3-17), add `editionYear` after `endPage`:

```typescript
export const SourceDocumentSchema = z.object({
  documentId: z.string(),
  title: z.string(),
  content: z.string(),
  source: z.string().optional(),
  sourceUrl: z.string().optional(),
  discoveryTag: z.string().optional(),
  authorityLevel: z.number().optional(),
  s3Key: z.string().optional(),
  startPage: z.number().int().optional(),
  endPage: z.number().int().optional(),
  editionYear: z.number().int().optional(),
});
```

- [ ] **Step 3: Add editionYear to the frontend Document type**

Edit `packages/webapp/src/stores/types.ts` — in the `Document` interface (lines 54-64), add `editionYear`:

```typescript
export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;
  startPage?: number;
  endPage?: number;
  authorityLevel?: number;
  discoveryTag?: string;
  editionYear?: number;
}
```

- [ ] **Step 4: Verify TypeScript still compiles**

Run: `cd packages/webapp && bunx tsc --noEmit 2>&1 | head -20`

Expected: no errors related to `editionYear`. (Pre-existing errors in other files are fine.)

- [ ] **Step 5: Commit**

```bash
git add packages/shared/lambda_layers/step_function_types/models.py \
        packages/messages/types/message-types.ts \
        packages/webapp/src/stores/types.ts
git commit -m "feat(graphrag): plumb edition_year through shared types

RAGDocument (Pydantic), SourceDocumentSchema (Zod), and frontend
Document interface all carry edition_year as Optional[int]. Wire-only
in v1 — no UI display yet. Field is null/undefined for non-WPAM docs."
```

---

## Task 10: Wire `edition_year` into the agentic_retrieval response builder

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py` (find _build_rag_documents or equivalent)

The lambda's response-builder produces RAGDocument instances. We need to copy `edition_year` from the chunk dict into the RAGDocument.

- [ ] **Step 1: Locate the RAGDocument construction site**

Run: `grep -n "RAGDocument(" packages/graphrag/lambdas/agentic_retrieval/main.py | head -10`

Note the line numbers where `RAGDocument(...)` is constructed. These are the sites to modify.

- [ ] **Step 2: Add edition_year to each RAGDocument construction**

Edit `packages/graphrag/lambdas/agentic_retrieval/main.py` — at every `RAGDocument(...)` call site, add `edition_year=chunk.get("edition_year")` (or the equivalent variable name in scope) as a keyword argument. Example:

```python
RAGDocument(
    document_id=...,
    title=...,
    content=...,
    s3_key=chunk.get("s3_key"),
    start_page=chunk.get("start_page"),
    end_page=chunk.get("end_page"),
    edition_year=chunk.get("edition_year"),  # NEW
    ...
)
```

If the construction is from a doc-level dict (not a chunk dict), use the doc's `edition_year` field — query Neptune via `get_document` already returns `edition_year` since the field is on the Doc node. But if `get_document`'s Cypher doesn't return it, add `d.edition_year AS edition_year` to that RETURN clause too.

- [ ] **Step 3: Update get_document Cypher in neptune_client.py to return edition_year**

Edit `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py` — modify the `get_document` method's RETURN clause to include `d.edition_year AS edition_year`:

```python
    def get_document(self, doc_id: str) -> dict | None:
        """Fetch a document node by ID."""
        results = self.query(
            "MATCH (d {id: $id}) "
            "RETURN d.id AS id, d.title AS title, d.summary AS summary, "
            "d.source_url AS source_url, d.source_key AS s3_key, "
            "d.doc_type AS doc_type, d.citation AS citation, "
            "d.authority_level AS authority_level, "
            "d.effective_date AS effective_date, "
            "d.edition_year AS edition_year, "
            "labels(d) AS labels",
            {"id": doc_id},
            query_name="get_document",
        )
        return results[0] if results else None
```

- [ ] **Step 4: Run the full lambda test suite**

Run: `uv run pytest packages/graphrag/lambdas/test/ -v`

Expected: all tests pass (including the dedup tests from earlier and any agentic_retrieval tests that already exist).

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py \
        packages/graphrag/lambdas/agentic_retrieval/neptune_client.py
git commit -m "feat(graphrag): forward edition_year from chunks/docs to RAGDocument

Response-builder now copies edition_year onto every RAGDocument it
emits. get_document Cypher widened to return edition_year so doc-level
lookups also surface it."
```

---

## Task 11: Re-ingest WPAM and deploy

**Files:** none (operations only)

Phases 2 (Doc nodes) and 8 (Chunk nodes) need to run against existing nodes to write `edition_year`. The `--source-filter wpam-` flag scopes the rerun to WPAM-only.

- [ ] **Step 1: Verify WPAM editions in S3 (sanity check)**

Run: `aws s3 ls s3://wis-raw-bucket-c8e69250/raw/ --profile wisco | grep wpam`

Expected: 15 prefixes from `wpam-...-2011` through `wpam-...-2025` (plus `wpam-...-vol-1-2011`).

- [ ] **Step 2: Set up Python environment for the loader**

Run:
```bash
export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
```

Expected: a non-empty path ending in `cacert.pem`.

- [ ] **Step 3: Run a dry verification — start with phase 2 only on WPAM**

Run:
```bash
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/load.py \
  --work-bucket wis-work-bucket-c8e69250 --graph-id g-ndvl4j73v4 \
  --config scripts/graphrag/ingest_config.yaml \
  --source-filter wpam- \
  --start-phase 2 --stop-after-phase 2
```

Expected: log lines `Phase 2: Creating document nodes...` followed by `Created 15 document nodes`. No `WPAM doc ... has no extractable edition_year` warnings (all 15 prefixes carry years). Exit code 0.

- [ ] **Step 4: Spot-check edition_year on one WPAM Doc node**

Use a quick Python check from `packages/graphrag/lambdas/agentic_retrieval/`:

```bash
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  PYTHONPATH=packages/graphrag/lambdas/agentic_retrieval \
  .venv/bin/python3 -c "
from neptune_client import NeptuneClient
c = NeptuneClient(graph_id='g-ndvl4j73v4')
r = c.query('MATCH (d {id: \"wpam-wisconsin-property-assessment-manual-2025\"}) RETURN d.edition_year AS y')
print(r)
"
```

Expected: `[{'y': 2025}]`.

- [ ] **Step 5: Re-run phase 8 on WPAM to write edition_year on chunks**

Run:
```bash
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/load.py \
  --work-bucket wis-work-bucket-c8e69250 --graph-id g-ndvl4j73v4 \
  --config scripts/graphrag/ingest_config.yaml \
  --source-filter wpam- \
  --start-phase 7 --stop-after-phase 7
```

(Phase 7 in the CLI numbering is "Chunk Nodes" per `load.py:1134`.) Expected: log lines mentioning chunk batch flushes, no errors. Exit code 0.

- [ ] **Step 6: Spot-check edition_year on one WPAM Chunk node**

```bash
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco \
  PYTHONPATH=packages/graphrag/lambdas/agentic_retrieval \
  .venv/bin/python3 -c "
from neptune_client import NeptuneClient
c = NeptuneClient(graph_id='g-ndvl4j73v4')
r = c.query('MATCH (c:Chunk) WHERE c.doc_id STARTS WITH \"wpam-\" AND c.doc_id ENDS WITH \"-2025\" RETURN c.edition_year AS y LIMIT 5')
print(r)
"
```

Expected: 5 rows, each `{'y': 2025}`.

- [ ] **Step 7: Bundle and deploy the lambda**

Run:
```bash
bun run bundle
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
```

Expected: only additive changes (asset hash updates for the agentic_retrieval lambda). No infrastructure changes.

If the diff looks safe, run:

```bash
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```

Expected: deploy succeeds.

- [ ] **Step 8: Smoke test in the browser**

Open https://d11c3qt90tkvk2.cloudfront.net (or the WisconsinBotGraphRAG webapp URL).

Verify:

1. **Generic WPAM query** ("how does the WPAM define agricultural land?"):
   - Citation cards show only one WPAM source per topic.
   - Network tab → look at the `documents` WebSocket message: every WPAM document has `editionYear: 2025` (or whichever is the latest).

2. **Year-specific query** ("what did the 2018 WPAM say about agricultural land?"):
   - Citation cards show 2018 WPAM source.
   - Documents have `editionYear: 2018`.

3. **Mixed query that traverses statute → WPAM** ("what does Wis. Stat. 70.32 mean for agricultural property?"):
   - Among the cited WPAM sections, only one edition appears per heading.

If any check fails, capture the WebSocket message JSON and the trace tab and investigate.

- [ ] **Step 9: Commit deployment artifacts (if any)**

If `bun run bundle` produced any new `packages/infra/bundle/` files that should be tracked, add them:

```bash
git status --short packages/infra/bundle/
```

If the bundle directory is gitignored (likely), no commit needed. Otherwise:

```bash
git add packages/infra/bundle/
git commit -m "chore: rebundle agentic_retrieval lambda with WPAM dedup"
```

---

## Verification Checklist

After all tasks complete, verify the spec is satisfied:

- [ ] `wpam-wisconsin-property-assessment-manual-2025` Doc node has `edition_year=2025` in Neptune
- [ ] Chunks from that doc also have `edition_year=2025`
- [ ] `vector_search("manufactured homes")` returns at most one chunk per `(framework, heading)` group when WPAM duplicates exist
- [ ] `vector_search("manufactured homes", target_wpam_year=2018)` returns the 2018 chunks instead of 2025
- [ ] `refine_query` on "what did the 2018 WPAM say about X" returns `target_wpam_year=2018`
- [ ] `refine_query` on "what's the 2018 deadline for property tax appeals" returns `target_wpam_year=null` (year mentioned but not WPAM-y)
- [ ] Frontend `documents` payload carries `editionYear` on WPAM cards
- [ ] All unit tests pass: `uv run pytest packages/graphrag/lambdas/test/ scripts/graphrag/tests/ -v`
- [ ] WisconsinBotGraphRAG stack deployed with new lambda bundle

If any item fails, the corresponding task needs revisiting before declaring done.
