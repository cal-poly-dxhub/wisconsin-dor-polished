import base64
import io
import json
import os
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any

import boto3
import botocore
from botocore.config import Config
from PIL import Image
from textractor.data.text_linearization_config import TextLinearizationConfig

from tools.ingestion.chunking.aws_utils import (
    delete_s3_prefix,
    download_pdf_from_s3,
    extract_textract_data,
)
from tools.ingestion.chunking.boilerplate import strip_boilerplate
from tools.ingestion.chunking.pymupdf_extractor import (
    extract_raw_text_with_pymupdf,
    extract_with_pymupdf,
    extraction_looks_good,
)
from tools.ingestion.chunking.toc_detector import is_toc_chunk
from tools.ingestion.chunking.wpam_chunk_filter import (
    filter_wpam_chunks,
    merge_short_chunks,
    repair_wpam_subheadings,
)

config = Config(read_timeout=600, retries={"max_attempts": 5})

MEDIA_BUCKET_NAME = os.environ.get("TEXTRACT_STAGING_BUCKET", "textract-chunk-result-dhgoel")

# Debug flag to control chunk logging
DEBUG = True  # Set to False to disable chunk logging
logging_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Lazy-initialized module-level clients (avoid AWS calls at import time)
_s3 = None
_region_name = None


def _get_s3():
    global _s3, _region_name
    if _s3 is None:
        _s3 = boto3.client("s3")
        session = boto3.session.Session()
        _region_name = session.region_name
        try:
            _ensure_bucket_exists(_s3, MEDIA_BUCKET_NAME)
        except Exception:
            pass
    return _s3


def _ensure_bucket_exists(s3_client, bucket_name: str):
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' exists")
    except botocore.exceptions.ClientError:
        print(f"Bucket '{bucket_name}' does not exist. Creating it...")
        s3_client.create_bucket(
            Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": _region_name}
        )


CHUNKER_BY_SOURCE = {
    "state-laws": "statute",
    "admin-rules": "admin_rule",
    "assessment-manual": "wpam",
}


def get_chunking_strategy(source_id: str) -> str:
    if source_id.startswith("wpam-"):
        return "wpam"
    if source_id.startswith("admin_rules-"):
        return "admin_rule"
    if source_id.startswith("statutes-"):
        return "statute"
    return CHUNKER_BY_SOURCE.get(source_id, "general")


def encode_image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_chunk_logs_dir():
    """Get the chunk logs directory path and create it if DEBUG is enabled."""
    if not DEBUG:
        return None

    chunk_logs_dir = "./pdf_chunking/chunk_logs"
    os.makedirs(chunk_logs_dir, exist_ok=True)
    return chunk_logs_dir


def strip_newline(cell: Any) -> str:
    """Remove newline characters from a cell value."""
    return str(cell).strip()


def sub_header_content_splitter(string: str) -> list[str]:
    """Split content by XML tags and return relevant segments."""
    pattern = re.compile(r"<<[^>]+>>")
    segments = re.split(pattern, string)
    result = []
    for segment in segments:
        if segment.strip():
            if "<header>" not in segment and "<list>" not in segment and "<table>" not in segment:
                segment = [x.strip() for x in segment.split("\n") if x.strip()]
                result.extend(segment)
            else:
                result.append(segment)
    return result


def split_list_items_(items: str) -> list[str]:
    """Split a string into a list of items, handling nested lists."""
    parts = re.split("(<<list>><list>|</list><</list>>)", items)
    output = []

    inside_list = False
    list_item = ""

    for p in parts:
        if p == "<<list>><list>":
            inside_list = True
            list_item = p
        elif p == "</list><</list>>":
            inside_list = False
            list_item += p
            output.append(list_item)
            list_item = ""
        elif inside_list:
            list_item += p.strip()
        else:
            output.extend(p.split("\n"))
    return output


def process_document(document, local_pdf_path: str):
    """
    Hybrid extraction:
    - Use Textractor's get_text() for structured, layout-aware text (<titles>, <headers>, etc.)
    - Simultaneously build (line_text, page_num) tuples for accurate page tracking.
    """
    filename = os.path.basename(local_pdf_path).lower()
    is_statute = "wi-admin" in filename or "wi-statute" in filename

    if is_statute:
        config = TextLinearizationConfig(
            hide_figure_layout=False,
            hide_table_layout=False,
            hide_header_layout=False,
            hide_footer_layout=False,
            hide_page_num_layout=False,
        )
    else:
        config = TextLinearizationConfig(
            hide_figure_layout=False,
            hide_table_layout=False,
            title_prefix="<titles><<title>><title>",
            title_suffix="</title><</title>>",
            hide_header_layout=True,
            section_header_prefix="<headers><<header>><header>",
            section_header_suffix="</header><</header>>",
            table_prefix="<tables><table>",
            table_suffix="</table>",
            list_layout_prefix="<<list>><list>",
            list_layout_suffix="</list><</list>>",
            hide_footer_layout=True,
            hide_page_num_layout=True,
        )

    structured_text_lines = []  # all structured lines (for chunking)
    line_page_mapping = []  # flat list of (text, page_num) for exact mapping

    for page in document.pages:
        page_text = page.get_text(config=config)
        lines = [x.strip() for x in page_text.split("\n") if x.strip()]
        structured_text_lines.extend(lines)

        # record which page these lines came from
        for line in lines:
            line_page_mapping.append((line, page.page_num))

    # join structured text for chunking
    result = "\n".join(structured_text_lines)

    if is_statute:
        header_split = [result]
    else:
        header_split = result.split("<titles>")

    """flowchart_chunks = extract_flowcharts_from_document(
        document, bedrock_runtime, os.path.basename(local_pdf_path)
    )"""

    flowchart_chunks = []

    return header_split, line_page_mapping, flowchart_chunks


# Dot-leader sequence used by TOC entries. Any line containing five or more
# consecutive dots (each optionally followed by whitespace) is a TOC entry —
# we never let such a line become a heading, since its body is just
# "<section-title> . . . . . . <page-number>" and it would pollute every
# downstream chunk with a nonsense heading.
_LEADER_IN_LINE = re.compile(r"(?:\.[ \t\xa0]*){5,}\.")

# Titan Embed Text v2 silently truncates inputs past 8000 characters in
# embed.py. Any chunk larger than this was partially vector-invisible:
# stored in Neptune, shown at retrieval, but the tail bytes were not part of
# the match decision. 7500 leaves a margin for character-counting imprecision
# between the chunker (which measures the buffer) and the final joined text
# (which includes heading prefixes added at flush time).
CHUNK_MAX_CHARS = 2500

_CHUNK_CAP_BY_STRATEGY = {
    "statute": 3500,
    "admin_rule": 3500,
}


def get_chunk_cap(strategy: str = "") -> int:
    return _CHUNK_CAP_BY_STRATEGY.get(strategy, CHUNK_MAX_CHARS)


def _count_chars_in_buffer(buffer: list[tuple[str, int]]) -> int:
    """Approximate char count for a (line, page) buffer joined by newlines."""
    if not buffer:
        return 0
    return sum(len(text) for text, _ in buffer) + max(0, len(buffer) - 1)


def _strip_tags(line: str) -> str:
    """Strip ``<title>``/``<header>``/``<table>`` XML markers for text-only views."""
    return re.sub(r"<[^>]+>", "", line).strip()


def chunk_document(header_split, file, BUCKET, line_page_mapping):
    """Chunk a general-purpose PDF.

    Walks ``line_page_mapping`` directly so each buffered line carries its
    source page from the start. Chunk page ranges are derived from the buffer
    (``min``/``max`` of its pages), not reconstructed by substring-matching
    chunk body lines against the whole document — the old approach inflated
    page ranges whenever a chunk contained boilerplate text like a page
    footer, which appears on every page.

    Chunks are split on roman-numeral section headings and capital-letter
    subsection headings. A candidate heading line that contains a TOC
    dot-leader sequence is treated as body text (TOC entries look like
    headings textually but reference a page rather than start a section).
    """
    max_words = 1200
    chunks = []
    doc_id = os.path.basename(file)

    roman_pattern = re.compile(r"^(?:[IVXLCDM]+)\s*[\.\-–:]")
    capital_pattern = re.compile(r"^[A-Z]\s*[\.\-–:]")

    def count_words(entries: list[tuple[str, int]]) -> int:
        return sum(len(re.findall(r"\w+", text)) for text, _ in entries)

    def flush_chunk(buffer: list[tuple[str, int]], heading: str, subheading: str) -> None:
        if not buffer:
            return
        prefix = f"{heading}\n{subheading}" if subheading else heading
        body = "\n".join(text for text, _ in buffer)
        chunk_text = f"{prefix}\n{body}" if prefix else body
        pages = {page for _, page in buffer}
        start_page, end_page = (min(pages), max(pages)) if pages else (1, 1)
        chunks.append(
            {
                "text": chunk_text.strip(),
                "metadata": {
                    "doc_id": doc_id,
                    "heading": heading,
                    "subheading": subheading,
                    "start_page": start_page,
                    "end_page": end_page,
                },
            }
        )

    roman_heading = ""
    sub_heading = ""
    buffer: list[tuple[str, int]] = []

    for raw_line, page in line_page_mapping:
        for segment in sub_header_content_splitter(raw_line):
            line = _strip_tags(segment)
            if not line:
                continue

            # TOC entries match the heading regex textually but reference a
            # page number via dot-leaders. Treat them as body text so they
            # never become the current heading.
            if _LEADER_IN_LINE.search(line):
                buffer.append((line, page))
                if (
                    count_words(buffer) > max_words
                    or _count_chars_in_buffer(buffer) > CHUNK_MAX_CHARS
                ):
                    flush_chunk(buffer, roman_heading, sub_heading)
                    buffer = []
                continue

            if roman_pattern.match(line):
                flush_chunk(buffer, roman_heading, sub_heading)
                roman_heading = line
                sub_heading = ""
                buffer = []
                continue

            if capital_pattern.match(line) and len(line.split()) > 1:
                if buffer:
                    flush_chunk(buffer, roman_heading, sub_heading)
                    buffer = []
                sub_heading = line
                continue

            buffer.append((line, page))

            if count_words(buffer) > max_words or _count_chars_in_buffer(buffer) > CHUNK_MAX_CHARS:
                flush_chunk(buffer, roman_heading, sub_heading)
                buffer = []

    flush_chunk(buffer, roman_heading, sub_heading)
    return _enforce_chunk_cap(chunks)


def _enforce_chunk_cap(chunks: list[dict], cap: int = 0) -> list[dict]:
    """Split any residual chunks that exceed the cap.

    The in-loop word/char triggers only fire at line boundaries, so a chunk
    with one very long line or whose heading+body sum pushes past the cap
    can still escape the primary flush. This pass walks the output and hard-
    splits any over-cap chunk at paragraph/line breaks, preferring natural
    seams within the last 20% of the cap window. Worst case is a mid-line
    hard-cut for single-line paragraphs longer than the cap — which
    is still better than Titan's silent truncation, because the tail content
    becomes its own embeddable chunk instead of being vector-invisible.
    """
    if not cap:
        cap = CHUNK_MAX_CHARS
    final: list[dict] = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= cap:
            final.append(chunk)
            continue
        pieces: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + cap, len(text))
            if end < len(text):
                break_hint = text.rfind("\n\n", start + int(cap * 0.8), end)
                if break_hint == -1:
                    break_hint = text.rfind("\n", start + int(cap * 0.8), end)
                if break_hint != -1 and break_hint > start:
                    end = break_hint
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            start = end
        if len(pieces) >= 2 and len(pieces[-1]) < 200:
            pieces[-2] = pieces[-2] + "\n" + pieces[-1]
            pieces.pop()
        for piece in pieces:
            split_chunk = {k: (dict(v) if isinstance(v, dict) else v) for k, v in chunk.items()}
            split_chunk["text"] = piece
            final.append(split_chunk)
    return final


def _split_statute_section(text: str, cap: int = CHUNK_MAX_CHARS, min_tail: int = 200) -> list[str]:
    """Split a long statute section at semantic boundaries.

    Priority: subsection markers → sentence boundaries → line breaks.
    Tiny tail fragments are merged back into the predecessor.
    """
    if len(text) <= cap:
        return [text]

    # Step 1: split at subsection boundaries — (1), (2), (4m), (a), etc.
    subsection_re = re.compile(r"(?=\n\s*\(\d+[a-z]*\)\s|\n\s*\([a-z]\)\s)")
    segments = subsection_re.split(text)
    segments = [s.strip() for s in segments if s.strip()]

    # Step 2: greedy-merge small adjacent subsections back together
    merged_segs = _greedy_merge_segments(segments, cap)

    # Step 3: any piece still over cap → split at sentence boundaries, then lines
    sentence_re = re.compile(r"(?<=\.) {2,}(?=[A-Z(])")
    final: list[str] = []
    for piece in merged_segs:
        if len(piece) <= cap:
            final.append(piece)
        else:
            sent_segments = sentence_re.split(piece)
            if len(sent_segments) > 1:
                for sp in _greedy_merge_segments(sent_segments, cap):
                    if len(sp) <= cap:
                        final.append(sp)
                    else:
                        final.extend(_split_at_lines(sp, cap))
            else:
                final.extend(_split_at_lines(piece, cap))

    # Step 4: merge tiny tails back into predecessor rather than leave orphans.
    # Only merge if predecessor is already under the primary cap — don't undo
    # intentional splits from step 3.
    merge_cap = 3000
    if len(final) <= 1:
        return final
    result = [final[0]]
    for piece in final[1:]:
        if len(piece) < min_tail and len(result[-1]) <= cap:
            combined = result[-1] + "\n" + piece
            if len(combined) <= merge_cap:
                result[-1] = combined
            else:
                result.append(piece)
        else:
            result.append(piece)
    return result


def _greedy_merge_segments(segments: list[str], cap: int) -> list[str]:
    """Greedily merge adjacent segments while staying under cap."""
    if not segments:
        return []
    out = [segments[0]]
    for seg in segments[1:]:
        combined = out[-1] + "\n" + seg
        if len(combined) <= cap:
            out[-1] = combined
        else:
            out.append(seg)
    return out


def _split_at_lines(text: str, cap: int) -> list[str]:
    """Last-resort split at newline boundaries."""
    results: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + cap, len(text))
        if end < len(text):
            break_hint = text.rfind("\n\n", start + int(cap * 0.8), end)
            if break_hint == -1:
                break_hint = text.rfind("\n", start + int(cap * 0.8), end)
            if break_hint != -1 and break_hint > start:
                end = break_hint
        piece = text[start:end].strip()
        if piece:
            results.append(piece)
        start = end
    return results


def chunk_document_statute(header_split, file, BUCKET, line_page_mapping):
    """
    Chunk WI Statute PDFs.
    Each numbered section becomes its own chunk.
    """
    doc_id = os.path.basename(file)
    chunks = []

    # Pattern for statute sections like "70.32 Real estate, how valued."
    # Must NOT match subsection references like "70.32 (2) (a) 6."
    chapter_match = re.search(r"statutes-(?:document-)?(\d+)", doc_id)
    if chapter_match:
        chapter = chapter_match.group(1)
        rule_pattern = re.compile(rf"({re.escape(chapter)}\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)")
    else:
        rule_pattern = re.compile(r"(\d+\.\d+[A-Za-z\-]*)(?:\s+[A-Z]|\s*$)")

    heading, local_buffer = None, []

    def flush_chunk(heading, buffer):
        """Flush one statute section into a chunk with correct page metadata."""
        if heading and buffer:
            pages = {p for _, p in buffer}
            start_page, end_page = min(pages), max(pages)
            chunk_text = f"{heading}\n" + "\n".join(txt for txt, _ in buffer).strip()
            chunks.append(
                {
                    "text": chunk_text,
                    "metadata": {
                        "doc_id": doc_id,
                        "heading": heading,
                        "start_page": start_page,
                        "end_page": end_page,
                    },
                }
            )

    # Walk through line–page mapping directly
    for line, page_num in line_page_mapping:
        clean = line.strip()
        if not clean:
            continue

        if rule_pattern.match(clean):
            flush_chunk(heading, local_buffer)
            heading, local_buffer = clean, []
        else:
            local_buffer.append((clean, page_num))

    # Flush final rule
    flush_chunk(heading, local_buffer)

    # Merge multi-page duplicates
    merged_chunks, last_heading, last_lines, last_start, last_end = [], None, [], None, None
    for ch in chunks:
        h = ch["metadata"]["heading"]
        sp, ep = ch["metadata"]["start_page"], ch["metadata"]["end_page"]

        if h == last_heading:
            last_lines.append(ch["text"].split("\n", 1)[1])
            last_end = ep
        else:
            if last_heading:
                merged_chunks.append(
                    {
                        "text": f"{last_heading}\n{'\n'.join(last_lines).strip()}",
                        "metadata": {
                            "doc_id": doc_id,
                            "heading": last_heading,
                            "start_page": last_start,
                            "end_page": last_end,
                        },
                    }
                )
            last_heading, last_start, last_end = h, sp, ep
            last_lines = [ch["text"].split("\n", 1)[1]]

    if last_heading:
        merged_chunks.append(
            {
                "text": f"{last_heading}\n{'\n'.join(last_lines).strip()}",
                "metadata": {
                    "doc_id": doc_id,
                    "heading": last_heading,
                    "start_page": last_start,
                    "end_page": last_end,
                },
            }
        )

    # Split oversized sections at subsection/sentence boundaries
    cap = get_chunk_cap("statute")
    final_chunks: list[dict] = []
    for chunk in merged_chunks:
        text = chunk["text"]
        if len(text) <= cap:
            final_chunks.append(chunk)
            continue
        parts = _split_statute_section(text, cap=cap)
        for part in parts:
            split_chunk = {k: (dict(v) if isinstance(v, dict) else v) for k, v in chunk.items()}
            split_chunk["text"] = part
            final_chunks.append(split_chunk)

    return final_chunks


def chunk_document_admin_rule(header_split, file, BUCKET, line_page_mapping):
    """
    Chunk Wisconsin Administrative Code PDFs (Tax XX.XX rules).

    Addresses admin-rule-specific issues:
    - TOC entries on page 1 match the rule pattern but carry no body content.
    - Page-continuation headers restate the rule ID with different trailing text.
    - Groups all fragments by normalized rule ID, then drops stubs.
    """
    doc_id = os.path.basename(file)
    rule_pattern = re.compile(r"(Tax\s\d+\.\d+[^ \n]*)")

    # First pass: collect (rule_id, body_lines, pages) per rule-match boundary.
    raw_sections: list[tuple[str, list[tuple[str, int]]]] = []
    current_id, local_buffer = None, []

    for line, page_num in line_page_mapping:
        clean = line.strip()
        if not clean:
            continue
        m = rule_pattern.match(clean)
        if m:
            if current_id is not None:
                raw_sections.append((current_id, local_buffer))
            current_id = m.group(1)
            remainder = clean[m.end() :].strip()
            local_buffer = [(remainder, page_num)] if remainder else []
        else:
            local_buffer.append((clean, page_num))

    if current_id is not None:
        raw_sections.append((current_id, local_buffer))

    # Second pass: group by rule_id (merges non-adjacent TOC + body occurrences).
    grouped: OrderedDict[str, list[tuple[str, int]]] = OrderedDict()
    for rule_id, lines in raw_sections:
        grouped.setdefault(rule_id, []).extend(lines)

    # Third pass: build chunks, drop stubs, split oversized.
    _MIN_BODY_CHARS = 80
    cap = get_chunk_cap("admin_rule")
    final_chunks: list[dict] = []

    for rule_id, body_lines in grouped.items():
        body_text = "\n".join(txt for txt, _ in body_lines).strip()
        if len(body_text) < _MIN_BODY_CHARS:
            continue
        pages = {p for _, p in body_lines}
        start_page, end_page = (min(pages), max(pages)) if pages else (1, 1)
        chunk_text = f"{rule_id}\n{body_text}"

        if len(chunk_text) <= cap:
            final_chunks.append(
                {
                    "text": chunk_text,
                    "metadata": {
                        "doc_id": doc_id,
                        "heading": rule_id,
                        "start_page": start_page,
                        "end_page": end_page,
                    },
                }
            )
        else:
            parts = _split_statute_section(chunk_text, cap=cap)
            for part in parts:
                final_chunks.append(
                    {
                        "text": part,
                        "metadata": {
                            "doc_id": doc_id,
                            "heading": rule_id,
                            "start_page": start_page,
                            "end_page": end_page,
                        },
                    }
                )

    return final_chunks


def chunk_document_wpam(header_split, file, BUCKET, line_page_mapping):
    """
    Chunk Wisconsin Property Assessment Manual (WPAM) PDFs.

    - Detects and skips Table of Contents or mini-TOC fragments.
    - Groups by Chapter headings and Section titles.
    - Automatically merges small related sections.
    - Returns final, ready-to-use chunks (like other chunkers).

    Size discipline: chunks are capped at CHUNK_MAX_CHARS characters. The cap
    is below Titan Embed v2's 8000-char silent-truncation threshold so every
    chunk's embedding covers its full text. Both the main flush path and the
    small-chunk merge step enforce this cap — prior versions only checked
    word count, letting paragraph-dense WPAM text drift past 10KB.
    """
    doc_id = os.path.basename(file)
    chunks = []

    # --- Patterns ---
    _chapter_re = re.compile(r"^Chapter\s+(\d+)(\S*)", re.IGNORECASE)

    def _is_chapter_heading(line: str) -> bool:
        """Detect real WPAM chapter headings while rejecting mid-prose references.

        Real titles: "Chapter 17", "Chapter 14 – Agricultural Valuation",
        "Chapter 50 Facilities – Sec. 70.11(4)(a), Wis. Stats."

        False positives: "Chapter 10 explains that...",
        "Chapter 9. The cost approach is often called...",
        "Chapter 14 of the WPAM, Agricultural Valuation, includes..."
        """
        m = _chapter_re.match(line)
        if not m:
            return False
        suffix = m.group(2)
        remainder = line[m.end() :].strip()
        if suffix and not re.match(r"^[–—.:]*[A-D]?$", suffix):
            return False
        if suffix == ".":
            return False
        if not remainder:
            return True
        if remainder[0] in ",)|(":
            return False
        first_word = remainder.split()[0] if remainder.split() else ""
        if first_word and first_word[0].islower():
            return False
        if len(line) >= 80:
            return False
        return True

    # Section-header detection. WPAM headings are typically one of:
    #   - All-caps, short ("OVERVIEW", "INTRODUCTION", "DEFINITIONS")
    #   - Numbered/lettered prefix ("A. Manufacturing Property", "1. Methodology")
    #   - Roman-numeral prefix ("I. Major Concepts", "IV. Methodology")
    # The prior pattern (``^[A-Z][A-Za-z\s]{3,}$``) accepted any line starting
    # with a capital letter and containing only letters+spaces — that included
    # mid-prose phrases like "Real Property Assessment" or "Manufacturing
    # Property" when they happened to land on their own line, producing
    # spurious chunk splits. Audit findings: long sentences shared as a
    # heading prefix across 10+ adjacent retrieved chunks.
    section_header_patterns = [
        re.compile(r"^[A-Z]{2,}(?:\s+[A-Z]{2,}){0,5}\s*$"),  # ALL-CAPS up to 6 tokens
        re.compile(r"^[A-Z]\.\s+[A-Z][A-Za-z]"),  # "A. Title" / "B. Notes"
        re.compile(r"^\d+\.\s+[A-Z][A-Za-z]"),  # "1. Methodology"
        re.compile(r"^[IVX]+\.\s+[A-Z][A-Za-z]"),  # "IV. Methodology"
    ]

    def _looks_like_section_header(line: str) -> bool:
        """Return True only when the line has a structural heading signature.

        Length cap (8 words / 80 chars) defends against long sentences that
        happen to start with a heading-like token.
        """
        if len(line) > 80 or len(line.split()) >= 8:
            return False
        return any(p.match(line) for p in section_header_patterns)

    max_words = 1200
    min_merge_words = 80  # merge chunks smaller than this
    max_merge_total = 500  # only merge if result < this many words

    # --- Helpers ---

    def clean_line(line: str) -> str:
        if not line:
            return ""
        return re.sub(r"<[^>]+>", "", str(line)).strip()

    def count_words(entries):
        if isinstance(entries, str):
            return len(re.findall(r"\w+", entries))
        # Support both list[str] and list[tuple[str, int]]
        return sum(len(re.findall(r"\w+", e[0] if isinstance(e, tuple) else e)) for e in entries)

    def count_chars(buffer: list[tuple[str, int]]) -> int:
        """Approximate char count of buffer contents joined by newlines."""
        if not buffer:
            return 0
        return sum(len(text) for text, _ in buffer) + len(buffer) - 1

    def is_probably_toc(text: str) -> bool:
        """Detect full or mini TOCs."""
        lowered = text.lower()
        if any(k in lowered for k in ["table of contents", "appendix", "glossary", "revisions"]):
            return True
        lines = text.splitlines()
        if len(lines) < 2:
            return False
        page_refs = sum(1 for line in lines if re.search(r"\b\d+-\d+\b", line))
        if page_refs / max(1, len(lines)) > 0.3:
            return True
        if re.match(r"^Chapter\s+\d+", text) and re.search(r"\b\d+-\d+\b", text):
            return True
        return False

    # --- Chunk Collector ---
    def flush_chunk(buffer: list[tuple[str, int]], chapter=None, section=None):
        if not buffer:
            return

        body_lines = [text for text, _ in buffer]
        chapter = chapter or ""
        section = section or ""
        heading = f"{chapter}\n{section}".strip()
        text = "\n".join(([heading] + body_lines) if heading else body_lines).strip()
        if not text or is_probably_toc(text):
            return

        pages = {page for _, page in buffer}
        sp, ep = (min(pages), max(pages)) if pages else (1, 1)
        chunks.append(
            {
                "text": text,
                "metadata": {
                    "doc_id": doc_id,
                    "heading": chapter or "Untitled",
                    "subheading": section or None,
                    "start_page": sp,
                    "end_page": ep,
                },
            }
        )

    # --- Main Chunk Loop ---
    # Walk line_page_mapping directly so each buffered line keeps its source
    # page. Reconstructing pages via global substring match (the prior
    # approach) inflated page ranges whenever chunks contained repeating
    # boilerplate like page footers.
    current_chapter, current_section = None, None
    buffer: list[tuple[str, int]] = []

    for raw_line, page in line_page_mapping:
        for segment in sub_header_content_splitter(raw_line):
            line = clean_line(segment)
            if not line:
                continue

            if _LEADER_IN_LINE.search(line):
                buffer.append((line, page))
                if count_words(buffer) > max_words or count_chars(buffer) > CHUNK_MAX_CHARS:
                    flush_chunk(buffer, current_chapter, current_section)
                    buffer = []
                continue

            if _is_chapter_heading(line):
                flush_chunk(buffer, current_chapter, current_section)
                current_chapter, current_section, buffer = line, None, []
                continue

            if _looks_like_section_header(line):
                flush_chunk(buffer, current_chapter, current_section)
                current_section, buffer = line, []
                continue

            buffer.append((line, page))

            if count_words(buffer) > max_words or count_chars(buffer) > CHUNK_MAX_CHARS:
                flush_chunk(buffer, current_chapter, current_section)
                buffer = []

    # Final flush
    flush_chunk(buffer, current_chapter, current_section)

    # --- Merge Small Chunks (internal like statute merging) ---
    merged_chunks = []
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        text = chunk["text"]
        word_count = count_words(text)

        # try merging with next if both share same chapter
        if word_count < min_merge_words and i + 1 < len(chunks):
            next_chunk = chunks[i + 1]
            same_heading = chunk["metadata"]["heading"] == next_chunk["metadata"]["heading"]
            combined = text.strip() + "\n\n" + next_chunk["text"].strip()
            if (
                same_heading
                and count_words(combined) <= max_merge_total
                and len(combined) <= CHUNK_MAX_CHARS
            ):
                merged_chunks.append(
                    {
                        "text": combined,
                        "metadata": {
                            **chunk["metadata"],
                            "end_page": next_chunk["metadata"].get(
                                "end_page", chunk["metadata"]["end_page"]
                            ),
                        },
                    }
                )
                i += 2
                continue

        merged_chunks.append(chunk)
        i += 1

    return _enforce_chunk_cap(merged_chunks)


def parse_s3_uri(s3_uri):
    # Ensure the URI starts with "s3://"
    if not s3_uri.startswith("s3://"):
        raise ValueError("Invalid S3 URI")

    # Remove the "s3://" prefix
    s3_path = s3_uri[5:]

    # Split the path into bucket and key
    bucket_name, *key_parts = s3_path.split("/", 1)
    file_key = key_parts[0] if key_parts else ""

    return bucket_name, file_key


def extract_clean_plaintext(doc_chunks, doc_id=None, is_statute=False):
    all_cleaned_content = []
    removed_chunks = []

    heading_pattern = re.compile(r"^(?:[IVXLCDM]+\.)|^[A-Z]\.|^Tax\s\d+\.\d+")

    def clean_line(line):
        try:
            if line is None:
                return ""
            return re.sub(r"<[^>]+>", "", str(line)).strip()
        except Exception:
            print("⚠️ clean_line failed on line:", repr(line))
            return ""

    def looks_like_index(text: str) -> bool:
        """Detect statute chapter headers/index pages that should be removed."""
        short = len(text.split()) < 15
        return short

    for idx, chunk in enumerate(doc_chunks):
        if not isinstance(chunk, dict) or "text" not in chunk:
            continue
        chunk_text = chunk["text"]

        # normalize lines
        lines = [clean_line(line) for line in chunk_text.split("\n") if clean_line(line)]
        lines = [line for line in lines if isinstance(line, str)]
        if not lines:
            removed_chunks.append({"text": chunk_text, "reason": "Empty"})
            continue
        text = "\n\n".join(lines)

        # stats
        word_count = sum(len(line.split()) for line in lines)
        sentence_count = sum(1 for line in lines if line.endswith((".", "?", "!")))

        if not is_statute and looks_like_index(text):
            removed_chunks.append({"text": text, "reason": "index/title"})
            continue

        # === Always keep if heading-style ===
        if heading_pattern.match(lines[0]):
            all_cleaned_content.append((idx, text))
            continue

        # === Adaptive thresholds ===
        if word_count < 50 and sentence_count == 1:
            removed_chunks.append(
                {
                    "text": text,
                    "reason": f"Too short ({word_count} words, {sentence_count} sentences)",
                }
            )
            continue

        all_cleaned_content.append((idx, text))
    return all_cleaned_content, removed_chunks


def extract_raw_text_from_document(document) -> str:
    """
    Extract raw text from a Textract document without any filtering or chunking.
    This is used as a fallback when the filtered approach returns no chunks.

    Args:
        document: Textract document object

    Returns:
        str: Raw text content from the document
    """
    # Simple configuration for raw text extraction
    simple_config = TextLinearizationConfig(
        hide_figure_layout=True,
        hide_table_layout=False,  # Keep tables as text
        hide_header_layout=True,
        hide_footer_layout=True,
        hide_page_num_layout=True,
    )

    all_text = []
    for page in document.pages:
        page_text = page.get_text(config=simple_config)
        if page_text.strip():
            all_text.append(page_text.strip())

    # Join all pages with double newlines
    raw_text = "\n\n".join(all_text)

    # Basic cleanup - remove excessive whitespace and XML tags
    raw_text = re.sub(r"<[^>]+>", "", raw_text)  # Remove XML tags
    raw_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw_text)  # Collapse multiple newlines
    raw_text = re.sub(r"[ \t]+", " ", raw_text)  # Normalize spaces

    return raw_text.strip()


def extract_raw_text_from_pdf_s3(bucket_name: str, s3_file_path: str) -> str:
    """
    Extract raw text from a PDF in S3.
    Tries PyMuPDF first, falls back to Textract.

    Args:
        bucket_name (str): The S3 bucket name.
        s3_file_path (str): The object key for the PDF file.

    Returns:
        str: Raw text content from the PDF
    """
    print(f"Extracting raw text from {os.path.basename(s3_file_path)}")
    local_pdf_path = download_pdf_from_s3(_get_s3(), bucket_name, s3_file_path)

    # Try PyMuPDF first
    try:
        raw_text = extract_raw_text_with_pymupdf(local_pdf_path)
        if raw_text and len(raw_text.split()) >= 10:
            print(
                f"Extracted raw text with PyMuPDF from {os.path.basename(s3_file_path)} successfully."
            )
            return raw_text
        print("PyMuPDF raw text insufficient, falling back to Textract...")
    except Exception as e:
        print(f"PyMuPDF raw text extraction failed ({e}), falling back to Textract...")

    # Textract fallback
    s3_uri = f"s3://{bucket_name}/{s3_file_path}"
    if not MEDIA_BUCKET_NAME:
        raise ValueError("MEDIA_BUCKET_NAME environment variable is not set.")

    textract_output_path = None
    try:
        document, local_pdf_path, textract_output_path = extract_textract_data(
            _get_s3(), s3_uri, bucket_name, MEDIA_BUCKET_NAME
        )
        raw_text = extract_raw_text_from_document(document)

        print(f"Extracted raw text from {os.path.basename(s3_file_path)} with Textract fallback.")
        return raw_text
    finally:
        if textract_output_path:
            media_bucket, prefix = parse_s3_uri(textract_output_path)
            print("fallback-->deleting Textract output from s3")
            delete_s3_prefix(_get_s3(), media_bucket, prefix)


def process_pdf_from_s3(
    bucket_name: str, s3_file_path: str, document_url: str = "n/a", source_id: str = "n/a"
) -> list:
    """
    Processes a PDF from S3 and returns a list of cleaned text + flowchart chunks.

    Uses PyMuPDF as the primary extraction engine; falls back to Textract when
    PyMuPDF fails or produces insufficient output.

    Args:
        bucket_name (str): The S3 bucket name.
        s3_file_path (str): The object key for the PDF file.
        document_url (str, optional): The source URL of the document. Defaults to "n/a".
        source_id (str, optional): The source identifier for routing. Defaults to "n/a".

    Returns:
        list: A list of cleaned text + flowchart chunks (dicts).
    """
    doc_id = os.path.basename(s3_file_path)
    strategy = get_chunking_strategy(source_id)
    is_statute = strategy in ("statute", "admin_rule")
    print(f"Processing {doc_id} (strategy={strategy}, source_id={source_id})")

    # --- Download PDF locally (shared by both extraction paths) ---
    local_pdf_path = download_pdf_from_s3(_get_s3(), bucket_name, s3_file_path)

    # --- Try PyMuPDF extraction first ---
    header_split = None
    line_page_mapping = None
    flowchart_chunks = []
    textract_output_path = None
    used_textract = False

    try:
        header_split, line_page_mapping = extract_with_pymupdf(local_pdf_path, is_statute)
        if not extraction_looks_good(header_split, line_page_mapping):
            print(f"PyMuPDF quality gate failed for {doc_id}, falling back to Textract...")
            header_split = None
    except Exception as e:
        print(f"PyMuPDF extraction failed for {doc_id} ({e}), falling back to Textract...")

    # --- Textract fallback ---
    if header_split is None:
        if not MEDIA_BUCKET_NAME:
            raise ValueError("MEDIA_BUCKET_NAME environment variable is not set.")

        s3_uri = f"s3://{bucket_name}/{s3_file_path}"
        try:
            document, local_pdf_path, textract_output_path = extract_textract_data(
                _get_s3(), s3_uri, bucket_name, MEDIA_BUCKET_NAME
            )
            header_split, line_page_mapping, flowchart_chunks = process_document(
                document, local_pdf_path
            )
            used_textract = True
            print(f"Using Textract fallback for {doc_id}")
        except Exception:
            if textract_output_path:
                media_bucket, prefix = parse_s3_uri(textract_output_path)
                delete_s3_prefix(_get_s3(), media_bucket, prefix)
            raise

    # --- Strip boilerplate (all doc types) ---
    line_page_mapping = strip_boilerplate(line_page_mapping, strategy=strategy)
    if is_statute:
        header_split = ["\n".join(text for text, _ in line_page_mapping)]
    else:
        full_text = "\n".join(text for text, _ in line_page_mapping)
        header_split = full_text.split("<titles>")

    try:
        # --- Run chunking ---
        if strategy == "admin_rule":
            raw_chunks = chunk_document_admin_rule(
                header_split, s3_file_path, bucket_name, line_page_mapping
            )
        elif strategy == "statute":
            raw_chunks = chunk_document_statute(
                header_split, s3_file_path, bucket_name, line_page_mapping
            )
        elif strategy == "wpam":
            raw_chunks = chunk_document_wpam(
                header_split, s3_file_path, bucket_name, line_page_mapping
            )
        else:
            raw_chunks = chunk_document(header_split, s3_file_path, bucket_name, line_page_mapping)

        chunk_logs_dir = get_chunk_logs_dir()
        if chunk_logs_dir:
            raw_chunks_dir = os.path.join(chunk_logs_dir, "raw_chunks")
            os.makedirs(raw_chunks_dir, exist_ok=True)
            raw_chunks_path = os.path.join(raw_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl")

            with open(raw_chunks_path, "w") as f:
                for idx, chunk in enumerate(raw_chunks):
                    record = {
                        "text": chunk["text"],
                        "metadata": {"doc_id": doc_id, "chunk_index": idx},
                    }
                    f.write(json.dumps(record, indent=2) + "\n")
            print(f"✅ Saved raw chunks to {raw_chunks_path}")

        # --- Drop TOC / leader-dot chunks ---
        # TOC fragments match queries lexically without carrying content,
        # so they outrank real pages. Filter before clean-plaintext so the
        # removal log captures them alongside other dropped chunks.
        toc_removed = []
        filtered_raw_chunks = []
        for idx, chunk in enumerate(raw_chunks):
            metadata = chunk.get("metadata", {})
            if is_toc_chunk(chunk.get("text", ""), metadata.get("heading")):
                toc_removed.append(
                    {
                        "reason": "toc_chunk",
                        "chunk_index": idx,
                        "heading": metadata.get("heading"),
                        "subheading": metadata.get("subheading"),
                        "text": chunk.get("text", ""),
                    }
                )
                continue
            filtered_raw_chunks.append(chunk)
        if toc_removed:
            print(f"🧹 Dropped {len(toc_removed)} TOC chunks for {doc_id}")
        raw_chunks = filtered_raw_chunks

        # --- WPAM quality filters (garbled tables + subheading repair) ---
        wpam_quality_removed = []
        if strategy == "wpam":
            raw_chunks, wpam_removed = filter_wpam_chunks(raw_chunks)
            wpam_quality_removed = [
                {"reason": r.pop("_filter_reason"), "text": r.get("text", ""), "chunk_index": i}
                for i, r in enumerate(wpam_removed)
            ]
            if wpam_quality_removed:
                print(
                    f"🧹 Dropped {len(wpam_quality_removed)} low-quality WPAM chunks for {doc_id}"
                )
            raw_chunks = repair_wpam_subheadings(raw_chunks)

        # --- Clean text chunks ---
        cleaned_text_chunks, removed_chunks = extract_clean_plaintext(
            raw_chunks, doc_id=doc_id, is_statute=is_statute
        )
        removed_chunks = toc_removed + wpam_quality_removed + removed_chunks

        if chunk_logs_dir:
            removed_chunks_dir = os.path.join(chunk_logs_dir, "removed")
            os.makedirs(removed_chunks_dir, exist_ok=True)
            removed_chunks_path = os.path.join(
                removed_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl"
            )
            with open(removed_chunks_path, "w") as f:
                for chunk in removed_chunks:
                    f.write(json.dumps(chunk, indent=2) + "\n")
            print(f"✅ Saved {len(removed_chunks)} removed chunks to {removed_chunks_path}")

        # --- Merge cleaned chunks + flowcharts ---
        all_chunks = []
        total_chunks = len(cleaned_text_chunks) + len(flowchart_chunks)

        for out_idx, (raw_idx, chunk) in enumerate(cleaned_text_chunks):
            raw_meta = raw_chunks[raw_idx]["metadata"]
            start_page = raw_meta.get("start_page", 1)
            end_page = raw_meta.get("end_page", start_page)

            all_chunks.append(
                {
                    "chunk_id": f"{doc_id}_final_{out_idx}",
                    "text": chunk,
                    "metadata": {
                        "doc_id": doc_id,
                        "source": s3_file_path,
                        "source_url": f"{document_url}#page={start_page}"
                        if (document_url and start_page)
                        else document_url,
                        "chunk_index": out_idx,
                        "total_chunks": total_chunks,
                        "source_id": source_id,
                        "start_page": start_page,
                        "end_page": end_page,
                        "heading": raw_meta.get("heading", ""),
                        "subheading": raw_meta.get("subheading", ""),
                    },
                }
            )

        for idx, fc in enumerate(flowchart_chunks, start=len(cleaned_text_chunks)):
            all_chunks.append(
                {
                    "chunk_id": f"{doc_id}_flowchart_{idx}",
                    "text": fc["text"],
                    "metadata": fc["metadata"]
                    | {
                        "source": s3_file_path,
                        "source_url": document_url,
                        "chunk_index": idx,
                        "total_chunks": total_chunks,
                        "source_id": source_id,
                    },
                }
            )

        # Final cap enforcement: extract_clean_plaintext rejoins lines with
        # double newlines which can push a chunk past the cap that was
        # compliant during chunk_document's buffer-time measurement. Re-run
        # the splitter so the emitted chunks are guaranteed <= cap.
        all_chunks = _enforce_chunk_cap(all_chunks, cap=get_chunk_cap(strategy))

        # Merge short tail fragments created by _enforce_chunk_cap back into
        # their predecessor. Must run AFTER cap enforcement to catch fragments
        # produced by splitting.
        if strategy == "wpam":
            all_chunks = merge_short_chunks(all_chunks)

        if chunk_logs_dir:
            final_chunks_dir = os.path.join(chunk_logs_dir, "final_chunks")
            os.makedirs(final_chunks_dir, exist_ok=True)
            final_chunks_path = os.path.join(
                final_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl"
            )
            with open(final_chunks_path, "w") as f:
                for chunk in all_chunks:
                    f.write(json.dumps(chunk, indent=2) + "\n")
            print(
                f"✅ Saved {len(all_chunks)} final chunks (including flowcharts) to {final_chunks_path}"
            )

        return all_chunks

    finally:
        if used_textract and textract_output_path:
            media_bucket, prefix = parse_s3_uri(textract_output_path)
            delete_s3_prefix(_get_s3(), media_bucket, prefix)
