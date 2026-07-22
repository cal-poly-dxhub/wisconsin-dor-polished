"""
Case-law chunking strategy: paragraph-aware with overlap.

Strategy (hybrid):
  - Chunk 0: LLM-generated holding summary (produced by _summarize_opinion, not here)
  - Chunks 1..N: paragraph-aware body chunks from the opinion text

Design principles:
  1. Prefer breaking at paragraph boundaries (¶N markers or double-newline)
  2. Target ~3500 chars per chunk (soft limit) — never exceed 4200 (hard cap)
  3. Overlap: repeat the last ~200 chars of the previous chunk at the start of the next
     to preserve cross-chunk context for embedding
  4. Strip the court header (everything before ¶1 or first paragraph break) — that's
     case name, parties, procedural caption that adds nothing to retrieval
  5. Preserve footnotes: keep them inline (they often contain critical legal reasoning)
  6. Each chunk gets a heading label: the ¶-range it covers, or a detected section name

The goal is chunks where EACH chunk is independently useful for vector search:
  - A chunk about "standard of review" should embed near that concept
  - A chunk about "application of §70.32" should embed near property-assessment queries
  - Facts chunks will naturally embed far from legal-principle queries (self-mitigating noise)

Usage:
    python tools/ingestion/chunking_test/chunk_case_law.py [--target-size 3500] [--overlap 200]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TARGET_SIZE = 3500  # soft target per chunk (chars)
DEFAULT_HARD_CAP = 4200     # absolute max — force-split if exceeded
DEFAULT_OVERLAP = 200       # chars of overlap between consecutive chunks


@dataclass
class ChunkConfig:
    target_size: int = DEFAULT_TARGET_SIZE
    hard_cap: int = DEFAULT_HARD_CAP
    overlap: int = DEFAULT_OVERLAP


@dataclass
class Chunk:
    """A single chunk produced from an opinion."""
    index: int
    text: str
    heading: str = ""
    para_start: str = ""  # e.g. "¶12" or ""
    para_end: str = ""    # e.g. "¶15" or ""
    section: str = ""     # e.g. "Analysis" if detected

    @property
    def char_count(self) -> int:
        return len(self.text)


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------

# Pattern: everything before the first ¶1 (modern opinions)
_PARA_ONE_RE = re.compile(r"¶\s*1\b")

# Pattern: detect paragraph markers anywhere
_PARA_MARKER_RE = re.compile(r"¶\s*(\d+)")

# Pattern: detect section headings (Roman numerals or labeled)
_SECTION_HEADING_RE = re.compile(
    r"(?:^|\s)([IVX]+\.\s*(?:BACKGROUND|FACTS|ANALYSIS|DISCUSSION|CONCLUSION|"
    r"STANDARD OF REVIEW|ISSUE|HOLDING|PROCEDURAL HISTORY|DISPOSITION|"
    r"NATURE OF THE CASE)[^\n]*)",
    re.IGNORECASE,
)

# Standalone section labels (without roman numeral prefix)
_STANDALONE_HEADING_RE = re.compile(
    r"(?:^|\s)((?:BACKGROUND|FACTS|ANALYSIS|DISCUSSION|CONCLUSION|"
    r"STANDARD OF REVIEW|ISSUE|HOLDING|PROCEDURAL HISTORY|DISPOSITION|"
    r"NATURE OF THE CASE)\.?)\s",
    re.IGNORECASE,
)


def strip_header(text: str) -> str:
    """Remove court header/caption — everything before ¶1 or the first sentence.

    For modern opinions (with ¶ markers), strips everything before ¶1.
    For older opinions (no ¶ markers), returns as-is (they start with the author name).
    """
    m = _PARA_ONE_RE.search(text)
    if m:
        return text[m.start():]
    # No ¶ markers — older opinions typically start with "AUTHOR, J." which is
    # fine to keep as it's short and provides context.
    return text


# ---------------------------------------------------------------------------
# Paragraph splitting
# ---------------------------------------------------------------------------

@dataclass
class Paragraph:
    """A logical paragraph from the opinion text."""
    text: str
    marker: str = ""   # e.g. "¶12" or ""
    section: str = ""  # current section heading, if detected


def split_into_paragraphs(text: str) -> list[Paragraph]:
    """Split opinion text into logical paragraphs.

    Uses ¶N markers as primary delimiters (modern opinions).
    Falls back to sentence-boundary heuristics for older opinions.
    """
    markers = list(_PARA_MARKER_RE.finditer(text))

    if markers:
        # Modern opinion: split at each ¶N
        paragraphs = []
        current_section = ""

        for i, m in enumerate(markers):
            start = m.start()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            para_text = text[start:end].strip()

            # Check if this paragraph contains a section heading
            sec_match = _SECTION_HEADING_RE.search(para_text[:200])
            if sec_match:
                current_section = sec_match.group(1).strip()
            else:
                standalone = _STANDALONE_HEADING_RE.search(para_text[:100])
                if standalone:
                    current_section = standalone.group(1).strip()

            paragraphs.append(Paragraph(
                text=para_text,
                marker=f"¶{m.group(1)}",
                section=current_section,
            ))

        return paragraphs

    else:
        # Older opinion: split on sentence-ending patterns followed by whitespace
        # These opinions are one long paragraph essentially. Split on logical breaks:
        #   - Double spaces after periods (common in legal text)
        #   - Page markers like *123
        #   - Section headings
        # Fall back to splitting every ~800 chars at sentence boundaries
        paragraphs = []
        current_section = ""

        # Try splitting on page markers (*NNN) which indicate page breaks
        page_splits = re.split(r'(?=\*\d{2,4}\s)', text)

        if len(page_splits) > 3:
            # Page markers give us reasonable splits
            for chunk in page_splits:
                chunk = chunk.strip()
                if not chunk:
                    continue
                sec_match = _SECTION_HEADING_RE.search(chunk[:200])
                if sec_match:
                    current_section = sec_match.group(1).strip()
                else:
                    standalone = _STANDALONE_HEADING_RE.search(chunk[:100])
                    if standalone:
                        current_section = standalone.group(1).strip()

                paragraphs.append(Paragraph(
                    text=chunk,
                    marker="",
                    section=current_section,
                ))
        else:
            # Last resort: split on sentence boundaries every ~800 chars
            sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z¶"])', text)
            current_chunk = ""
            for sent in sentences:
                if len(current_chunk) + len(sent) > 800 and current_chunk:
                    sec_match = _STANDALONE_HEADING_RE.search(current_chunk[:100])
                    if sec_match:
                        current_section = sec_match.group(1).strip()
                    paragraphs.append(Paragraph(
                        text=current_chunk.strip(),
                        marker="",
                        section=current_section,
                    ))
                    current_chunk = sent
                else:
                    current_chunk += (" " if current_chunk else "") + sent
            if current_chunk.strip():
                paragraphs.append(Paragraph(
                    text=current_chunk.strip(),
                    marker="",
                    section=current_section,
                ))

        return paragraphs


# ---------------------------------------------------------------------------
# Chunking logic
# ---------------------------------------------------------------------------

def chunk_opinion(text: str, config: ChunkConfig | None = None) -> list[Chunk]:
    """Chunk an opinion text into retrieval-ready chunks.

    Returns a list of Chunk objects (NOT including the summary chunk 0 —
    that's produced separately by the LLM summarizer).
    """
    if config is None:
        config = ChunkConfig()

    # Step 1: Strip header
    body = strip_header(text)

    # Step 2: Split into paragraphs
    paragraphs = split_into_paragraphs(body)

    if not paragraphs:
        # Degenerate case: return as single chunk
        return [Chunk(index=0, text=body[:config.hard_cap], heading="Full text")]

    # Step 3: Merge paragraphs into chunks respecting target size
    chunks: list[Chunk] = []
    current_text = ""
    current_paras: list[Paragraph] = []

    for para in paragraphs:
        candidate = (current_text + "\n\n" + para.text).strip() if current_text else para.text

        if len(candidate) <= config.target_size:
            # Fits in current chunk
            current_text = candidate
            current_paras.append(para)
        elif not current_text:
            # Single paragraph exceeds target — force it into a chunk
            # (will be split at hard cap if needed)
            current_text = para.text
            current_paras.append(para)
        else:
            # Current chunk is full — emit it
            chunks.append(_make_chunk(len(chunks), current_text, current_paras, config))

            # Start new chunk with overlap from previous
            overlap_text = current_text[-config.overlap:] if config.overlap else ""
            # Find clean break point for overlap (start of sentence)
            if overlap_text:
                sent_break = re.search(r'(?<=[.!?])\s+', overlap_text)
                if sent_break:
                    overlap_text = overlap_text[sent_break.end():]

            current_text = (overlap_text + "\n\n" + para.text).strip() if overlap_text else para.text
            current_paras = [para]

    # Emit final chunk
    if current_text.strip():
        chunks.append(_make_chunk(len(chunks), current_text, current_paras, config))

    # Step 4: Hard-cap splitting for any oversized chunks
    final_chunks: list[Chunk] = []
    for chunk in chunks:
        if chunk.char_count <= config.hard_cap:
            final_chunks.append(chunk)
        else:
            # Force-split at sentence boundaries
            sub_chunks = _force_split(chunk, config)
            for sc in sub_chunks:
                sc.index = len(final_chunks)
                final_chunks.append(sc)

    # Re-index
    for i, c in enumerate(final_chunks):
        c.index = i

    return final_chunks


def _make_chunk(index: int, text: str, paras: list[Paragraph], config: ChunkConfig) -> Chunk:
    """Create a Chunk from accumulated paragraphs."""
    # Determine heading from paragraph markers
    markers = [p.marker for p in paras if p.marker]
    if markers:
        para_start = markers[0]
        para_end = markers[-1]
        heading = f"{para_start}–{para_end}" if para_start != para_end else para_start
    else:
        para_start = ""
        para_end = ""
        heading = ""

    # Use section name if available
    sections = [p.section for p in paras if p.section]
    section = sections[-1] if sections else ""
    if section and not heading:
        heading = section

    return Chunk(
        index=index,
        text=text,
        heading=heading,
        para_start=para_start,
        para_end=para_end,
        section=section,
    )


def _force_split(chunk: Chunk, config: ChunkConfig) -> list[Chunk]:
    """Split an oversized chunk at sentence boundaries."""
    text = chunk.text
    sub_chunks = []
    start = 0

    while start < len(text):
        end = start + config.target_size

        if end >= len(text):
            sub_chunks.append(Chunk(
                index=0,
                text=text[start:].strip(),
                heading=chunk.heading,
                section=chunk.section,
            ))
            break

        # Find sentence break near target
        search_region = text[end - 200:end + 200]
        breaks = list(re.finditer(r'(?<=[.!?])\s+', search_region))
        if breaks:
            # Pick the break closest to target
            best = min(breaks, key=lambda m: abs(m.start() + (end - 200) - end))
            actual_end = end - 200 + best.end()
        else:
            # No sentence break found — hard cut
            actual_end = end

        sub_chunks.append(Chunk(
            index=0,
            text=text[start:actual_end].strip(),
            heading=chunk.heading,
            section=chunk.section,
        ))

        # Apply overlap
        start = actual_end - config.overlap

    return sub_chunks


# ---------------------------------------------------------------------------
# CLI / test runner
# ---------------------------------------------------------------------------

def print_chunk_report(filename: str, chunks: list[Chunk], text_len: int) -> None:
    """Print a concise report of chunk quality for one opinion."""
    print(f"\n{'='*70}")
    print(f"FILE: {filename}")
    print(f"  Original: {text_len:,} chars → {len(chunks)} chunks")
    sizes = [c.char_count for c in chunks]
    avg = sum(sizes) / len(sizes) if sizes else 0
    print(f"  Chunk sizes: min={min(sizes):,} | avg={avg:,.0f} | max={max(sizes):,}")
    print(f"  {'─'*66}")

    for c in chunks:
        first_line = c.text[:80].replace('\n', ' ')
        last_line = c.text[-80:].replace('\n', ' ')
        heading_str = f"[{c.heading}]" if c.heading else ""
        section_str = f" ({c.section})" if c.section else ""
        print(f"  Chunk {c.index:2d} | {c.char_count:5,} chars | {heading_str}{section_str}")
        print(f"           FIRST: {first_line}...")
        print(f"           LAST:  ...{last_line}")
        print()


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Test case-law chunking strategy")
    parser.add_argument("--target-size", type=int, default=DEFAULT_TARGET_SIZE)
    parser.add_argument("--hard-cap", type=int, default=DEFAULT_HARD_CAP)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--samples-dir", default=os.path.join(os.path.dirname(__file__), "samples"))
    parser.add_argument("--file", help="Process a single file instead of all samples")
    args = parser.parse_args()

    config = ChunkConfig(
        target_size=args.target_size,
        hard_cap=args.hard_cap,
        overlap=args.overlap,
    )

    print(f"Chunking config: target={config.target_size}, hard_cap={config.hard_cap}, overlap={config.overlap}")
    print(f"{'='*70}")

    if args.file:
        files = [args.file]
    else:
        files = sorted(
            os.path.join(args.samples_dir, f)
            for f in os.listdir(args.samples_dir)
            if f.endswith(".txt")
        )

    total_chunks = 0
    all_sizes = []

    for filepath in files:
        text = open(filepath).read()
        chunks = chunk_opinion(text, config)
        total_chunks += len(chunks)
        all_sizes.extend(c.char_count for c in chunks)
        print_chunk_report(os.path.basename(filepath), chunks, len(text))

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"  Files processed: {len(files)}")
    print(f"  Total chunks: {total_chunks}")
    if all_sizes:
        print(f"  Overall sizes: min={min(all_sizes):,} | avg={sum(all_sizes)/len(all_sizes):,.0f} | max={max(all_sizes):,}")
        under_1k = sum(1 for s in all_sizes if s < 1000)
        over_target = sum(1 for s in all_sizes if s > config.target_size)
        print(f"  Chunks < 1000 chars: {under_1k} ({under_1k*100//len(all_sizes)}%)")
        print(f"  Chunks > target ({config.target_size}): {over_target} ({over_target*100//len(all_sizes)}%)")


if __name__ == "__main__":
    main()
