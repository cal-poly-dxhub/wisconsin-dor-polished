# WPAM Chunk Quality Controls

The Wisconsin Property Assessment Manual (WPAM) PDFs are the hardest documents in the corpus to chunk well. They mix multi-column layouts, dense tables, running headers on every page, and numbered-list items that look like section headings. Without targeted quality controls, the graph fills with garbled table fragments, leaked headings, and short concentrated embeddings that outrank substantive content in vector search.

This doc describes the WPAM-specific controls applied during extraction.

## Pipeline Position

All WPAM quality controls run inside `process_pdf_from_s3()` in `tools/pdf_chunking/pdfChunker.py`, after chunking but before the final chunk list is emitted:

```
PyMuPDF extraction → boilerplate stripping → chunk_document_wpam()
    → TOC removal → filter_wpam_chunks() → repair_wpam_subheadings()
    → extract_clean_plaintext() → _enforce_chunk_cap() → merge_short_chunks()
```

## 1. Boilerplate Stripping (pre-chunking)

**File:** `tools/pdf_chunking/boilerplate.py`

Applied to the raw `(line, page_num)` mapping before any chunking logic runs.

**What it removes:**
- Bare page numbers (`^\d{1,4}$`)
- "Wisconsin Department of Revenue" standalone lines
- "Back to table of contents" navigation links
- Date stamps like "Revised 01/2024" or "Updated January 15, 2024"
- WPAM-specific: "Wisconsin Property Assessment Manual", "Vol. 2, page 17-5", bare "Chapter N" lines

**Running header deduplication:** WPAM PDFs repeat "Chapter 17 Agricultural Valuation" at the top of every page. The stripper counts how often each `Chapter N Title` line appears — if more than 3 times, it keeps only the first occurrence (which the chunker uses as a split signal) and removes all subsequent duplicates.

## 2. Chunking Strategy (`chunk_document_wpam`)

**File:** `tools/pdf_chunking/pdfChunker.py:444-662`

Walks `line_page_mapping` directly (each line carries its source page from extraction). Splits on:

- **Chapter headings** — detected by `_is_chapter_heading()`, which rejects mid-prose references like "Chapter 10 explains that..." by checking line length (< 80 chars), suffix patterns, and first-word casing after the chapter number.
- **Section headers** — detected by `_looks_like_section_header()` with tight pattern matching:
  - All-caps lines up to 6 tokens (e.g., "OVERVIEW", "DEFINITIONS")
  - `A. Title` / `B. Notes` style
  - `1. Methodology` numbered style
  - `IV. Methodology` roman-numeral style
  - Length cap: max 8 words / 80 chars to reject long sentences that start with heading-like tokens.

**Why the heading detection is restrictive:** The prior pattern (`^[A-Z][A-Za-z\s]{3,}$`) accepted any capitalized line with letters and spaces. This matched phrases like "Real Property Assessment" when they landed on their own line, producing spurious chunk splits and misattributed headings across 10+ adjacent chunks. The current patterns require explicit structural markers.

**Size enforcement:**
- Word limit: 1200 words per chunk
- Char limit: `CHUNK_MAX_CHARS = 2500` (well under Titan Embed v2's 8000-char silent-truncation threshold)
- Both are checked on every line append; flush triggers on whichever hits first.

**Internal merge pass:** After the main loop, chunks under 80 words are merged forward with the next chunk if they share the same chapter heading and the combined result stays under 500 words / `CHUNK_MAX_CHARS`.

## 3. TOC Chunk Detection

**File:** `tools/pdf_chunking/toc_detector.py`

Runs on all raw chunks immediately after chunking, before WPAM-specific filters.

**Problem:** TOC entries match user queries lexically ("contact information", "appeals process") without carrying the answer. They outscore real content in vector search because they contain the exact query terms in a concentrated form.

**Detection (conservative — errs toward keeping):**
- Pure roman-numeral heading (`V.`, `XIV.`) + any dot-leader sequence in the body → always TOC
- Short chunk (≤ 1500 chars) with ≥ 2 leader-dot sequences covering ≥ 20% of text length → TOC

**What a leader-dot sequence looks like:** `". . . . . ."` or `".........."` — five or more consecutive dots with optional whitespace between them.

## 4. WPAM Quality Filters

**File:** `tools/pdf_chunking/wpam_chunk_filter.py`

### `filter_wpam_chunks()`

Operates on the chunk body (text minus heading/subheading lines). Returns `(kept, removed)` with removal reasons for logging.

| Filter | Threshold | What it catches |
|--------|-----------|-----------------|
| `body_too_short` | < 60 chars | Fragment chunks that would produce artificially concentrated embeddings |
| `garbled_columns` | ≥ 8 pipes, < 30 chars/pipe, > 40% short lines | Column-interleaved text from multi-column layouts misread as prose (produces "boa \| rd" style noise) |
| `table_cells` | > 30% single-char lines, or > 70% lines under 10 chars with > 5 lines total | Vertical table headers rendered as one letter per line (C-O-D-E) |

### `repair_wpam_subheadings()`

**Problem:** The chunker sets `current_section` when it encounters a section header, and carries it forward until the next one. If a numbered-list item like "1. The record requested does not exist." matches the section pattern, it becomes the subheading for every subsequent chunk until the next real section header — sometimes 40+ chunks.

**Fix:** Count how many chunks share each subheading. Any subheading appearing on > 5 chunks is cleared (real WPAM subsection titles appear on 1–2 chunks max). This runs after filtering so removed chunks don't inflate the count.

## 5. Clean-Plaintext Filter

**File:** `tools/pdf_chunking/pdfChunker.py:678-730`

Runs on all doc types after WPAM-specific filters. For WPAM it catches:

- **Empty chunks** — after all the stripping, some chunks have no content left
- **Too-short chunks** — < 50 words AND only 1 sentence (unless the first line matches a heading pattern like `Tax 18.05`)
- **Index/title pages** — chunks with < 15 words that look like a chapter cover page

## 6. Hard Cap Enforcement (`_enforce_chunk_cap`)

**File:** `tools/pdf_chunking/pdfChunker.py:323-356`

The in-loop word/char triggers only fire at line boundaries. A chunk with one very long line, or whose heading + body sum pushes past 2500 chars, can escape the primary flush. This pass hard-splits any over-cap chunk at:

1. Double newline (paragraph break) within the last 20% of the cap window
2. Single newline within the last 20%
3. Hard character cut (last resort)

Each split piece becomes its own chunk with the same metadata. The tail content becomes embeddable instead of being vector-invisible from Titan's silent truncation.

## 7. Short-Chunk Merge (`merge_short_chunks`)

**File:** `tools/pdf_chunking/wpam_chunk_filter.py:126-170`

Runs AFTER `_enforce_chunk_cap` to catch fragments it produced. Merges backward:

- Chunk text < 200 chars
- Same `heading` (chapter) as predecessor
- Combined result ≤ 3000 chars

**Why merge backward:** Short fragments are almost always the tail of the previous chunk's thought (a paragraph that was split at a sub-optimal boundary). Merging forward would combine unrelated content.

## Observability

All filters log counts at runtime:
```
🧹 Dropped 3 TOC chunks for wpam-ch17.pdf
🧹 Dropped 2 low-quality WPAM chunks for wpam-ch17.pdf
```

When `DEBUG = True` in `pdfChunker.py`, three JSONL logs are written per document:
- `chunk_logs/raw_chunks/{doc_id}_{timestamp}.jsonl` — chunks immediately after the chunker
- `chunk_logs/removed/{doc_id}_{timestamp}.jsonl` — all removed chunks with reasons
- `chunk_logs/final_chunks/{doc_id}_{timestamp}.jsonl` — the final emitted chunk list

## Key Design Decisions

**2500-char cap vs. Titan's 8000-char limit:** We use 2500, not 7500 or 8000. The heading prefix added at flush time, the `\n\n` rejoining in `extract_clean_plaintext`, and metadata JSON overhead all add characters. A 2500-char budget at chunk time guarantees the final embedded text stays well within Titan's window without needing to measure the assembled output.

**Conservative TOC detection:** We only flag text that is unambiguously a leader-dot TOC. False negatives (keeping a TOC chunk) are cheaper than false positives (dropping a content chunk that happens to mention page numbers).

**No LLM in quality filtering:** All filters use pattern matching and statistical heuristics. LLM calls are expensive at ingestion scale (100+ WPAM pages per chapter × 20 chapters), and the failure modes are structural (garbled columns, leaked headings) rather than semantic.

**Heading misattribution fix:** The audit found that overly broad heading patterns caused long sentences to become headings, which then propagated across 10+ chunks. The fix was to tighten detection patterns rather than add a post-hoc repair step — preventing the problem is cheaper than correcting it downstream.
