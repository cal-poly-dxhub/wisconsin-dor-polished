# Fix: Stub Promotion Page Number

## Problem

When the model cites a statute (e.g., WIS-STAT-70.995 or WIS-STAT-70.32) in its answer, and that statute was discovered via a graph edge (not via vector search), the source card at the bottom of the response showed the wrong page number — always page 3 instead of the actual definition page (page 55 for §70.995, page 23 for §70.32).

This only affects statutes discovered via the stub promotion path (graph-neighbor discovery), not statutes found via direct chunk retrieval (which carry correct page metadata from the chunk itself).

## Root Cause

Statute section nodes in Neptune (like WIS-STAT-70.995) are stubs — they have no chunks of their own. When cited, the `find_stub_promotion` function resolves them to their parent PDF (statutes-70) by finding a chunk that CITES the stub and reading that chunk's `start_page`.

The old query ordered by `c.start_page ASC LIMIT 1` — meaning it always returned the first chunk in the document that cross-references the statute. For Chapter 70, that's always page 3 (TOC/annotations area), where statutes are mentioned in passing. The chunk that actually defines §70.995 is on page 55, but it was never selected because lower-page cross-references always won.

## The Fix

**File:** `backend/lambdas/agentic_retrieval/neptune_client.py` — `find_stub_promotion()` method

Changed the Cypher query to add a `def_score` ranking that distinguishes definition chunks from cross-reference chunks:

```cypher
CASE
  WHEN $section_prefix <> ''
    AND c.text STARTS WITH ($section_prefix + ' ')
    AND substring(c.text, size($section_prefix) + 1, 1) >= 'A'
    AND substring(c.text, size($section_prefix) + 1, 1) <= 'Z'
    THEN 0
  ELSE 1 END AS def_score
ORDER BY rank ASC, def_score ASC, c.start_page ASC
```

**How it works:** The statute chunker produces definition chunks whose text starts with `"<section_number> <Title>"` — e.g., `"70.995 Definitions."` or `"70.32 Real estate, how valued."` Cross-references start with lowercase text or subsection markers like `(1)`. The fix detects this pattern: if the chunk text starts with the section number followed by a space and a capital letter `[A-Z]`, it's scored as a definition (`def_score = 0`); otherwise it's a cross-reference (`def_score = 1`).

The `ORDER BY` now sorts by:

1. **`rank`** — prefer parent docs in the same framework (statutes-70 for WIS-STAT-70.*)
2. **`def_score`** — prefer definition chunks over cross-references
3. **`c.start_page`** — tiebreaker among equal-scoring chunks

## Result

- WIS-STAT-70.995 now resolves to page 55 (the actual definition page) instead of page 3 (a TOC cross-reference).
- WIS-STAT-70.32 resolves to page 23 instead of page 3.
- The fix is format-agnostic — it works regardless of line-break patterns or PDF extraction quirks, and doesn't rely on heading metadata that may be stale.
- Fallback behavior preserved: if no definition chunk is found, lowest page number among parent-matching chunks is still used.
