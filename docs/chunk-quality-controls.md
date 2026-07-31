# Chunk Quality Controls

Chunking is the highest-leverage step in the ingestion pipeline: chunk boundaries
decide what a vector search can retrieve, and chunk *quality* decides whether the
graph fills with garbled table fragments, leaked headings, and short concentrated
embeddings that outrank substantive content. Every document type in the corpus has
different failure modes, so the pipeline routes each to a strategy tuned for it and
then applies a shared quality pass.

This doc describes how each doc type is chunked and the quality controls applied
along the way. All code lives in `tools/ingestion/chunking/` unless noted.

> **Rebuild reminder.** Chunking runs inside the Fargate Docker image. Any change
> under `chunking/` requires `cd tools/ingestion/docker && ./build_and_push.sh`
> before it takes effect on Fargate.

## The three chunking paths

Not everything goes through the PDF chunker. There are three distinct paths,
chosen in `extract.py` before chunking:

1. **Case law** — any doc with `doc_type == "case_law"` or a `case-law-` id is
   routed to `process_case_law_document` → `case_law.select_and_chunk`
   (`chunking/case_law.py`). It never touches the PDF chunker.
2. **Non-PDF text (HTML, `.txt`)** — anything whose key doesn't end in `.pdf`
   (e.g. scraped FAQ/news HTML) uses a fixed-size sliding window in `extract.py`
   (`chunk_size` 2000, `chunk_overlap` 200; chunks under 50 chars dropped).
3. **PDFs** — routed by `process_pdf_from_s3` (`pdfChunker.py`) to one of four
   strategies below.

Only path 3 uses the strategy router and the shared quality pass. Paths 1 and 2
are described in their own sections at the end.

## PDF strategy routing

`get_chunking_strategy(source_id)` (`pdfChunker.py`) picks a strategy from the
doc_id prefix, falling back to a small legacy exact-match dict
(`CHUNKER_BY_SOURCE`) and then to `general`:

```python
if source_id.startswith("wpam-"):        return "wpam"
if source_id.startswith("admin_rules-"): return "admin_rule"
if source_id.startswith("statutes-"):    return "statute"
return CHUNKER_BY_SOURCE.get(source_id, "general")
```

There are exactly **four** strategies. Every PDF doc category maps to one:

| Doc category | doc_id prefix | Strategy | Char cap |
|--------------|---------------|----------|---------:|
| Statutes | `statutes-{ch}` | `statute` | 3500 |
| Admin rules | `admin_rules-tax-{n}` | `admin_rule` | 3500 |
| WPAM | `wpam-...` | `wpam` | 2500 |
| Gov pubs / guides / advisories | `guide-`, `advisory-`, … | `general` | 2500 |
| IAAO | `iaao-...` | `general` | 2500 |
| USPAP | `uspap-...` | `general` | 2500 |
| Constitution | (generic) | `general` | 2500 |

Caps live in `pdfChunker.py`: `CHUNK_MAX_CHARS = 2500` is the default;
`_CHUNK_CAP_BY_STRATEGY = {"statute": 3500, "admin_rule": 3500}` overrides it for
the two legal-text strategies. `wpam` and `general` inherit the 2500 default via
`get_chunk_cap()` — there is no explicit entry for them. The 2500 cap sits well
under Titan Embed v2's ~8000-char silent-truncation threshold, leaving room for the
heading prefix, `\n\n` rejoining, and JSON overhead added downstream.

## The shared post-chunking pipeline

Regardless of strategy, `process_pdf_from_s3` runs the same ordered pipeline. The
WPAM-only steps are inert for other strategies.

```
PyMuPDF extraction (Textract fallback)
    → strip_boilerplate            (all docs, strategy-aware)
    → <strategy chunker>           (statute | admin_rule | wpam | general)
    → TOC removal                  (all docs — is_toc_chunk)
    → filter_wpam_chunks           (WPAM only)
    → repair_wpam_subheadings      (WPAM only)
    → extract_clean_plaintext      (all docs)
    → _enforce_chunk_cap           (all docs)
    → merge_short_chunks           (WPAM only)
```

Note `merge_short_chunks` runs **last**, after cap enforcement, so it can reabsorb
tiny tail fragments that the hard-cap split produced.

## Extraction (`pymupdf_extractor.py`)

**PyMuPDF-first, Textract-fallback.** The corpus is digital-native, so OCR adds
nothing and PyMuPDF is faster and free. Textract fires only when PyMuPDF fails the
quality gate.

- **Body font size** (`_get_body_font_size`): character-count-weighted mode of
  rounded span sizes across all pages (default 12.0).
- **Line classification** (`_classify_line`), by char-weighted mean span size vs.
  body size: **title** if ≥1.4× (or ≥1.15× *and* all-bold); **header** if ≥1.1×
  (or all-bold and ≥1.05×); else **body**.
- **Table detection**: `page.find_tables()`, then `looks_like_real_table()`
  rejects false positives — <2 rows, sparsity >60% empty cells
  (`_MAX_EMPTY_CELL_RATIO = 0.60`), or multi-column prose (long cells + few rows).
  Genuine tables render as `" | "`-joined rows.
- **XML markers** (non-statute docs): `<titles>`, `<headers>`, `<tables>` tag the
  text for downstream splitting. Statutes emit raw untagged text.
- **Outputs**: `header_split` (text split on `<titles>`) and `line_page_mapping`
  (`list[(line, 1-based page)]`) — the basis for per-line page tracking.
- **Quality gate** (`extraction_looks_good`): PyMuPDF output must have ≥5 lines,
  ≥1 non-empty line, and average stripped length ≥3, or Textract takes over.

## Boilerplate stripping (`boilerplate.py`)

Runs on the `(line, page)` mapping before any chunking. `GENERAL_PATTERNS` apply to
every doc; each strategy adds its own group (`_STRATEGY_PATTERNS`).

- **General (all docs):** bare page numbers (`^\d{1,4}$`); "Wisconsin Department
  of Revenue"; "Back to table of contents"; date-stamp lines
  (Revised/Published/Effective/Updated `<date>`).
- **Statute:** "Updated 20XX … Wisconsin Statutes" running headers; bare
  `Chapter N`; all-caps section-title running headers.
- **Admin Rule:** "WISCONSIN ADMINISTRATIVE CODE" / "…DEPARTMENT OF REVENUE";
  "Published under s. X.X"; "Register `<month> <year>` No. N"; `Chapter Tax N`.
- **WPAM:** "Wisconsin Property Assessment Manual"; "Vol. N, page N-N"; bare
  `Chapter N`. **Plus running-header dedup** (`_strip_wpam_running_headers`):
  counts each `Chapter N Title` line; any appearing **more than 3 times** (i.e.
  ≥4) is treated as a running header and all but the first non-TOC occurrence are
  removed (the first is kept as the chunker's split signal).
- **Guide/general strategy:** no extra patterns (`GUIDE_PATTERNS = []`).

## Strategy 1 — statute (`chunk_document_statute`)

State-law PDFs (`statutes-*`), cap **3500**.

- **Section boundaries:** chapter-aware regex. If the doc_id yields a chapter
  (`statutes-70`), the pattern anchors on `{chapter}.\d+` (e.g. `70.32 Real
  estate, how valued.`); otherwise a generic `\d+.\d+` section pattern.
- **Multi-page merge:** consecutive fragments of the same section (same heading)
  are merged back together.
- **Oversized sections** split via `_split_statute_section` at subsection markers
  `(1)`, `(a)`, `(4m)`, then sentence boundaries, then line breaks. Tiny tail
  fragments (`min_tail = 200`) are greedily merged back into the predecessor if
  the combined length stays under `merge_cap = 3000`.

## Strategy 2 — admin_rule (`chunk_document_admin_rule`)

Admin-code PDFs (`admin_rules-*`), cap **3500**.

- **Rule boundaries:** regex `Tax\s\d+\.\d+` detects rule IDs.
- **Fragment grouping:** all fragments are grouped by normalized rule ID via an
  `OrderedDict`, so a rule's TOC entry and its body occurrence (which may be pages
  apart) merge into one chunk.
- **Stub drop:** rules whose body is under `_MIN_BODY_CHARS = 80` are dropped.
- Oversized rules split via the same `_split_statute_section` logic as statutes.

## Strategy 3 — wpam (`chunk_document_wpam`)

The Wisconsin Property Assessment Manual, cap **2500**. WPAM PDFs are the hardest
in the corpus — multi-column layouts, dense tables, running headers on every page,
and numbered-list items that masquerade as section headings — so this strategy
plus the WPAM-only filters below carry the most machinery.

Chunking walks `line_page_mapping` directly (each line carries its source page):

- **Chapter headings** (`_is_chapter_heading`): rejects mid-prose references like
  "Chapter 10 explains that…" via line-length (<80 chars), suffix, and
  first-word-casing checks.
- **Section headers** (`_looks_like_section_header`), tight patterns only:
  ALL-CAPS ≤6 tokens; `A. Title`; `1. Methodology`; `IV. Methodology`. Capped at
  ≤8 words / ≤80 chars to reject long sentences that merely start with a
  heading-like token.
- **Size:** `max_words = 1200` and the 2500-char cap, checked on every line
  append; flush on whichever hits first.
- **In-loop small-chunk merge:** chunks under `min_merge_words = 80` merge forward
  with the next chunk if they share a chapter heading and the result stays under
  `max_merge_total = 500` words / 2500 chars.

> **Why heading detection is restrictive.** An earlier broad pattern
> (`^[A-Z][A-Za-z\s]{3,}$`) matched any capitalized standalone line, so phrases
> like "Real Property Assessment" became headings and propagated across 10+
> adjacent chunks. Tightening the patterns prevents the problem at the source
> rather than repairing it downstream.

## Strategy 4 — general (`chunk_document`)

Everything else — gov pubs, guides, advisories, IAAO, USPAP, constitution — cap
**2500**.

- **Heading splits** at roman-numeral (`^[IVXLCDM]+\s*[.\-–:]`) and capital-letter
  (`^[A-Z]\s*[.\-–:]`) section markers (a capital-letter subheading must be more
  than one word).
- **Size:** `max_words = 1200` word limit and the 2500-char cap both trigger
  flushes.
- No small-chunk merge; relies on the shared cap-enforcement + clean-plaintext
  passes.

## Shared quality pass

### TOC detection (`toc_detector.py`, all docs)

TOC entries match user queries lexically ("appeals process") without carrying the
answer, and their concentrated query terms outscore real content. `is_toc_chunk`
flags a chunk when either:

- its heading is a pure roman numeral (`V.`, `XIV.`) **and** the body has ≥1
  leader-dot sequence; or
- the chunk is ≤1500 chars, has ≥2 leader-dot sequences, and those cover ≥20% of
  the text.

A leader-dot sequence is `(?:\.[ \t]*){5,}\.` — five-plus dots (optionally
whitespace-separated) ending in a dot. Detection is deliberately conservative:
keeping a borderline TOC chunk is cheaper than dropping a real content chunk that
happens to mention page numbers. Every strategy also treats any line containing a
leader-dot run as body text, so TOC entries never become the current heading.

### Clean plaintext (`extract_clean_plaintext`, all docs)

Strips residual XML tags and drops chunks that are empty, index/title pages
(non-statute, <15 words), or too short (<50 words *and* a single sentence — unless
the first line matches a heading pattern like `Tax 18.05`).

### Cap enforcement (`_enforce_chunk_cap`, all docs)

The in-loop word/char triggers only fire at line boundaries, so a chunk with one
very long line — or whose heading + body sum sneaks past the cap — can escape.
This pass hard-splits any over-cap chunk at, in order: a double newline within the
last 20% of the cap window, a single newline within that window, or a hard
character cut. Each piece inherits the same metadata. (For statutes/admin rules the
chunker also enforced the cap internally, so this is a second, belt-and-suspenders
pass.)

## WPAM-only filters (`wpam_chunk_filter.py`)

WPAM's layout problems survive the generic passes, so three extra filters run only
for WPAM.

### `filter_wpam_chunks`

Operates on the chunk body (text minus heading/subheading lines):

| Filter | Threshold | Catches |
|--------|-----------|---------|
| `body_too_short` | body < 60 chars | Fragments that produce artificially concentrated embeddings |
| `garbled_columns` | ≥8 pipes, <30 chars per pipe, >40% of lines short (<40 chars) | Multi-column text misread as prose ("boa \| rd") |
| `table_cells` | >30% single-char lines, **or** >70% lines <10 chars with >5 lines total | Vertical table headers rendered one letter per line (C-O-D-E) |

Bullet/symbol characters are excluded from the single-char / short-line counts.

### `repair_wpam_subheadings`

The chunker carries `current_section` forward until the next section header. If a
numbered-list item ("1. The record requested does not exist.") matches the section
pattern, it can become the subheading for dozens of subsequent chunks. Fix: count
how many chunks share each subheading; any appearing on **more than 5** chunks is
cleared (real WPAM subsection titles appear on 1–2 chunks). Runs after
`filter_wpam_chunks` so removed chunks don't inflate the count.

### `merge_short_chunks`

Runs last (after cap enforcement) to reabsorb fragments the hard-split produced.
Merges a chunk **backward** into its predecessor when the chunk text is <200 chars,
it shares the same `heading` (chapter) as the predecessor, and the combined result
is ≤3000 chars. Backward, because a short fragment is almost always the tail of the
previous chunk's thought; merging forward would combine unrelated content.

## Case law (`case_law.py::select_and_chunk`)

Court opinions get an analysis-focused selective chunker that deliberately *omits*
low-value text rather than down-ranking it. It retains the majority opinion's
opening issue/holding synopsis, the majority legal analysis (starting at a
confidently detected transition — "we begin our analysis", "standard of review",
etc.), and the disposition. It normally drops captions, counsel lists,
facts/procedure, trailing notes, and separate opinions, falling back to full
majority text only when the analysis transition can't be located.

- Sizing: `TARGET_SIZE = 3500`, `HARD_CAP = 4200`, `OVERLAP = 160`,
  `MIN_TRAILING_CHUNK = 900`.
- OCR repair: CourtListener text sometimes OCRs the pilcrow (¶) as "ś"; a narrow
  regex repairs it before paragraph numbers.

## Non-PDF text (`extract.py`)

Scraped HTML (FAQ/news pages) and `.txt` sources bypass all of the above. They use
a fixed sliding window: `chunk_size = 2000`, `chunk_overlap = 200`, dropping any
window whose stripped text is under 50 chars. No heading detection, no quality
filters — the assumption is that this content is short and clean. (FAQ Q&A pairs
destined for the Bedrock FAQ Knowledge Base are handled separately by
`ops/extract_faq_qa_pairs.py`, not this path.)

## Observability

All filters log counts at runtime, e.g.:

```
🧹 Dropped 3 TOC chunks for wpam-ch17.pdf
🧹 Dropped 2 low-quality WPAM chunks for wpam-ch17.pdf
```

When `DEBUG = True` in `pdfChunker.py`, three JSONL logs are written per document:
`chunk_logs/raw_chunks/`, `chunk_logs/removed/` (with removal reasons), and
`chunk_logs/final_chunks/`.

## Key parameters

| Parameter | Value | Location |
|-----------|-------|----------|
| `CHUNK_MAX_CHARS` (wpam/general) | 2500 | `pdfChunker.py` |
| `_CHUNK_CAP_BY_STRATEGY` (statute/admin_rule) | 3500 | `pdfChunker.py` |
| `max_words` (general & wpam in-loop) | 1200 | `pdfChunker.py` |
| `min_merge_words` (wpam) | 80 | `pdfChunker.py` |
| `max_merge_total` (wpam) | 500 | `pdfChunker.py` |
| `_MIN_BODY_CHARS` (admin_rule stub drop) | 80 | `pdfChunker.py` |
| `merge_short_chunks` min_chars / max_merged_chars | 200 / 3000 | `wpam_chunk_filter.py` |
| `repair_wpam_subheadings` max_occurrences | 5 | `wpam_chunk_filter.py` |
| TOC coverage / matches / max len | 0.2 / 2 / 1500 | `toc_detector.py` |
| WPAM running-header repeat threshold | >3 | `boilerplate.py` |
| Table sparsity reject ratio | >0.60 | `pymupdf_extractor.py` |
| Case-law TARGET / HARD_CAP / OVERLAP | 3500 / 4200 / 160 | `case_law.py` |
| Non-PDF chunk_size / overlap | 2000 / 200 | `extract.py` |

## Key design decisions

- **2500-char cap vs. Titan's ~8000-char window:** the heading prefix added at
  flush time, `\n\n` rejoining, and metadata JSON overhead all add characters. A
  2500-char budget at chunk time guarantees the assembled embedded text stays well
  within Titan's window without measuring the output.
- **Conservative TOC detection:** only unambiguous leader-dot TOCs are flagged.
  False negatives (keeping a TOC chunk) are cheaper than false positives.
- **No LLM in quality filtering:** all filters are pattern-matching and
  statistical heuristics. LLM calls are expensive at ingestion scale, and the
  failure modes (garbled columns, leaked headings) are structural, not semantic.
- **Prevention over repair:** the WPAM heading-misattribution fix tightened
  detection patterns rather than adding a post-hoc corrector — cheaper to prevent
  a bad split than to unwind it downstream.

See also [fargate-ingestion](fargate-ingestion.md) for how these run on Fargate and the load phases
that consume the chunks.
