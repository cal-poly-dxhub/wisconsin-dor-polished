import re
from collections import Counter
from typing import List, Tuple

import fitz  # PyMuPDF

STATUTE_NOISE_PATTERNS = [
    re.compile(r"Updated\s+\d{4}.*Wisconsin\s+Statutes", re.IGNORECASE),
    re.compile(r"^\d+$"),
    re.compile(r"^Chapter\s+\d+\s*$"),
]


def strip_statute_noise(
    line_page_mapping: List[Tuple[str, int]],
) -> List[Tuple[str, int]]:
    """Remove repeating headers, footers, and bare page numbers from statute PDFs."""
    return [
        (line, pnum)
        for line, pnum in line_page_mapping
        if not any(p.match(line.strip()) for p in STATUTE_NOISE_PATTERNS)
    ]


def _get_body_font_size(doc: fitz.Document) -> float:
    """Determine the most common font size (body text) weighted by character count."""
    size_counter: Counter = Counter()
    for page in doc:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        size_counter[round(span["size"], 1)] += len(text)
    if not size_counter:
        return 12.0
    return size_counter.most_common(1)[0][0]


def _is_bold(span: dict) -> bool:
    flags = span.get("flags", 0)
    if flags & (1 << 4):
        return True
    return "bold" in span.get("font", "").lower()


def _classify_line(spans: list, body_size: float) -> str:
    """Classify a text line as 'title', 'header', or 'body' based on font metrics."""
    if not spans:
        return "body"

    total_len = sum(len(s.get("text", "")) for s in spans)
    if total_len == 0:
        return "body"

    weighted_size = sum(s["size"] * len(s.get("text", "")) for s in spans) / total_len
    all_bold = all(_is_bold(s) for s in spans if s.get("text", "").strip())

    if weighted_size >= body_size * 1.4:
        return "title"
    if weighted_size >= body_size * 1.15 and all_bold:
        return "title"
    if weighted_size >= body_size * 1.1 or (
        all_bold and weighted_size >= body_size * 1.05
    ):
        return "header"

    return "body"


# Prose-as-table signatures. Real tables with long description cells (revision
# logs, tax-allocation grids) have MANY rows; fake tables from multi-column
# prose have few rows. We combine row count + max-cell-length to tell them
# apart: a 4-row "table" with a 1000+ char cell is prose; a 26-row table with
# a 220 char cell is a real revision log.
_LONG_CELL_MIN_ROWS = 5
_VERY_LONG_CELL_MIN_ROWS = 8
_LONG_CELL_CHARS = 200
_VERY_LONG_CELL_CHARS = 500


def looks_like_real_table(rows: list[list]) -> bool:
    """Return True when the extracted rows look like a genuine data grid.

    ``find_tables()`` routinely flags multi-column prose pages as tables. Row-
    joining those with ``" | "`` scrambles reading order and injects pipe noise
    into chunks. We reject when the shape looks prose-like:
      - Fewer than 2 rows (a 1-row "table" is almost always a header line).
      - No cell has any content.
      - Few rows AND a long cell (prose-as-table): the signature of POG p.41's
        contact-info page, which comes back as 4 rows × 2 cols with a
        1244-char cell. Real multi-row tables pass even when individual
        cells are long (revision logs with 11-26 rows and 200-500 char
        description cells).
    """
    if not rows or len(rows) < 2:
        return False

    any_content = False
    max_cell = 0
    for row in rows:
        for cell in row:
            text = str(cell).strip() if cell else ""
            if not text:
                continue
            any_content = True
            if len(text) > max_cell:
                max_cell = len(text)
    if not any_content:
        return False

    n_rows = len(rows)
    if max_cell > _VERY_LONG_CELL_CHARS and n_rows < _VERY_LONG_CELL_MIN_ROWS:
        return False
    if max_cell > _LONG_CELL_CHARS and n_rows < _LONG_CELL_MIN_ROWS:
        return False
    return True


def _extract_table_text(table) -> str:
    rows = table.extract()
    lines = []
    for row in rows:
        cells = [str(c).strip() if c else "" for c in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _rects_overlap(r1: tuple, r2: tuple) -> bool:
    return not (r1[2] < r2[0] or r2[2] < r1[0] or r1[3] < r2[1] or r2[3] < r1[1])


def extract_with_pymupdf(
    pdf_path: str, is_statute: bool
) -> Tuple[List[str], List[Tuple[str, int]]]:
    """
    Extract structured text from a PDF using PyMuPDF with font-based detection.

    Returns ``(header_split, line_page_mapping)`` matching the contract expected
    by ``chunk_document`` / ``chunk_document_statute`` / ``chunk_document_wpam``.
    """
    doc = fitz.open(pdf_path)
    body_size = _get_body_font_size(doc)

    all_tagged_lines: List[str] = []
    line_page_mapping: List[Tuple[str, int]] = []

    for page in doc:
        page_num = page.number + 1  # fitz is 0-based; pipeline expects 1-based

        # Detect tables on this page
        try:
            tables_result = page.find_tables()
            candidate_tables = tables_result.tables if tables_result else []
        except Exception:
            candidate_tables = []
        # Reject false-positive tables (multi-column prose layouts misdetected
        # as tables). Blocks overlapping a rejected candidate will fall through
        # to the normal text path below. Per-table extract() is wrapped so a
        # malformed candidate can't fail the whole page.
        table_objs = []
        for t in candidate_tables:
            try:
                rows = t.extract()
            except Exception:
                continue
            if looks_like_real_table(rows):
                table_objs.append(t)
        table_rects = [t.bbox for t in table_objs]
        emitted_tables: set = set()

        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block.get("type") != 0:
                continue

            block_rect = tuple(block["bbox"])

            # Check if this block overlaps a detected table
            table_idx = None
            for ti, tr in enumerate(table_rects):
                if _rects_overlap(block_rect, tr):
                    table_idx = ti
                    break

            if table_idx is not None:
                if table_idx not in emitted_tables:
                    emitted_tables.add(table_idx)
                    table_text = _extract_table_text(table_objs[table_idx])
                    if table_text.strip() and not is_statute:
                        tagged = f"<tables><table>{table_text}</table>"
                        all_tagged_lines.append(tagged)
                        line_page_mapping.append((tagged, page_num))
                    elif table_text.strip():
                        for tl in table_text.split("\n"):
                            tl = tl.strip()
                            if tl:
                                all_tagged_lines.append(tl)
                                line_page_mapping.append((tl, page_num))
                continue

            # Non-table text blocks
            for line_dict in block.get("lines", []):
                spans = line_dict.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue

                if is_statute:
                    all_tagged_lines.append(text)
                    line_page_mapping.append((text, page_num))
                else:
                    classification = _classify_line(spans, body_size)
                    if classification == "title":
                        tagged = f"<titles><<title>><title>{text}</title><</title>>"
                    elif classification == "header":
                        tagged = f"<headers><<header>><header>{text}</header><</header>>"
                    else:
                        tagged = text
                    all_tagged_lines.append(tagged)
                    line_page_mapping.append((tagged, page_num))

    doc.close()

    if is_statute:
        line_page_mapping = strip_statute_noise(line_page_mapping)
        all_tagged_lines = [l for l, _ in line_page_mapping]

    full_text = "\n".join(all_tagged_lines)
    if is_statute:
        header_split = [full_text]
    else:
        header_split = full_text.split("<titles>")

    return header_split, line_page_mapping


def extract_raw_text_with_pymupdf(pdf_path: str) -> str:
    """Extract plain text from a PDF using PyMuPDF (no tagging or structuring)."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text().strip()
        if text:
            pages.append(text)
    doc.close()

    raw_text = "\n\n".join(pages)
    raw_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw_text)
    raw_text = re.sub(r"[ \t]+", " ", raw_text)
    return raw_text.strip()


def extraction_looks_good(
    header_split: List[str],
    line_page_mapping: List[Tuple[str, int]],
    min_lines: int = 5,
) -> bool:
    """Evaluate whether PyMuPDF extraction produced sufficient usable content."""
    if len(line_page_mapping) < min_lines:
        return False

    non_empty = sum(1 for text, _ in line_page_mapping if text.strip())
    if non_empty == 0:
        return False

    total_chars = sum(len(text.strip()) for text, _ in line_page_mapping)
    avg_len = total_chars / max(1, non_empty)
    if avg_len < 3:
        return False

    return True
