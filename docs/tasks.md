# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 21 | Add z-score normalization to search_document result filtering | — |
| 40 | Harden inline linking prose — quote verbatim instead of paraphrasing | — |

## Done

| # | Task |
|---|------|
| 3 | Tune model tone — reduce overconfident statements |
| 4 | Replace Step Function with direct Lambda invoke |
| 7 | Refactor agentic retrieval Lambda (main.py) |
| 8 | Add boilerplate stripping before chunking |
| 10 | Externalize prompts from Lambda code |
| 11 | Add managed compute for ingestion (Fargate) |
| 12 | Fix WebSocket streaming hang on background tabs |
| 13 | Harden authority hierarchy enforcement (authority-aware re-ranking) |
| 9 | Reduce topic clustering batch size |
| 15 | Add settings modal with detailed trace toggle |
| 6 | Reduce PDF chunk size for consistency and precision |
| 16 | Support URL-based session routing and preserve "new chat" state on reload |
| 18 | Show traversed sources in UI during agentic retrieval |
| 19 | Fix train-of-thought flicker on sidebar session hover |
| 24 | Multi-citation source cards (aggregate inline citations per parent doc) |
| 1 | Disambiguate generic queries before full retrieval |
| 14 | Improve case law discovery in vector_search auto-enrichment |
| 23 | Strip WPAM running headers from chunk text |
| 25 | Fix WPAM 2025 garbled table chunks and heading metadata |
| 2 | Fixing linking issues |
| 22 | Apply over-fetch multiplier when target_wpam_year is set |
| 29 | Enable prompt caching for agentic retrieval (switch to invoke_model) |
| 30 | get_section chunk grid visualization — show cosine/z-score per chunk in trace UI |
| 28 | WPAM 2019 heading loss — boilerplate stripper keeps TOC copy, strips real chapter start |
| 36 | Full corpus refresh — scrape, ingest missing docs, reingest stale content |
| 38 | Restructure tools/ directory — consolidate ingestion pipeline |
| 32 | Show trimmed section page index in answer synthesis trace card |
| 35 | Graph wiring overhaul — stubs as routing nodes, not dead ends |
| 39 | Discover and ingest 2026 news pages |
| 26 | Admin ingestion page — ingest documents via URL from the UI |
| 17 | Handle multipart queries (split or unified answering strategy) |
| 20 | Add user persona setting (government worker vs. citizen) |
| 27 | Fix sparse WPAM subheadings — use PyMuPDF `<header>` font tags |
| 41 | Fix statute citation page numbers, card titles, and chunker page-header pollution |
| 42 | Markarian hierarchy query fails to ground `statutes-70` (turn-budget exhaustion) |
| 5 | Replace LLM classification with structural parsers |

---

## Task Details

---

---

---

### Task 21: Add z-score normalization to search_document result filtering

**Context:** Z-score normalization was investigated for `vector_search` (2026-06-25) and found non-viable there — heterogeneous source types create overlapping score distributions where below-mean often means "authoritative but linguistically different," not "irrelevant." However, `search_document` operates within a single document where all chunks share the same writing style, vocabulary density, and embedding characteristics.

**Why it works for search_document:** When searching within one document (e.g., "find the section about agricultural classification in WPAM"), score differences genuinely reflect relevance differences — it's one distribution, not five overlapping ones. Z-score can separate "this chapter answers your question" from "this chapter mentions a keyword in passing."

**Current behavior:** `search_document` fetches 800 chunks globally, filters to the target doc (often 30-100 matches for WPAM), then returns top_k. No adaptive quality floor — it always returns exactly top_k results regardless of whether the bottom results are noise.

**Proposed:** After filtering to the target doc's chunks, compute z-scores and drop chunks with z < 0 (below mean). This adaptively shrinks results when scores form a tight cluster with a few outliers, while preserving more results when scores are broadly distributed.

**Other potential locations:**
- **FAQ search** — signal "no confident match" when all scores are low and flat
- **Case law discovery** — separate on-point cases from tangential citations of the same broad statute

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — `search_document` (line 857), `faq_search` (line 534)
- `backend/lambdas/agentic_retrieval/neptune_client.py` — raw scores returned from Neptune

---



