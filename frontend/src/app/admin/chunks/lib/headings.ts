import type { ChunkIndexEntry } from './chunks-api';

/**
 * Turn a chunk heading into the label of the section it belongs to.
 *
 * Chunks are grouped by this label so that every chunk cut out of the same
 * statute section / manual chapter sits inside one box in the grid. The corpus
 * mixes several heading conventions, so each gets its own pattern:
 *
 *   "70.11(4) Property exempted"      → "§ 70.11"   (statute, subsection dropped)
 *   "§ 70.32 Real estate, how valued" → "§ 70.32"
 *   "Tax 12.05(1)(a) Certification"   → "Tax 12.05" (admin rule)
 *   "Chapter 9 — Agricultural"        → "Chapter 9" (WPAM)
 *   "Appendix B: Conversion charts"   → "Appendix B"
 *   "Part IV — Standards"             → "Part IV"
 *
 * Anything unrecognised falls back to the whole heading (whitespace collapsed),
 * which still groups consecutive chunks that share a heading verbatim.
 */
const HEADING_PATTERNS: { pattern: RegExp; label: (m: RegExpMatchArray) => string }[] = [
  // Admin rule: "Tax 12.05(1)(a)". Must precede the bare-number rule so the
  // "Tax" qualifier is not dropped.
  {
    pattern: /^tax\s*(\d{1,3}\.\d{1,3})/i,
    label: m => `Tax ${m[1]}`,
  },
  // Statute: "70.11(4)", "§ 70.32", "s. 70.995". Subsections collapse into the
  // parent section.
  {
    pattern: /^(?:§+\s*|s\.\s*|sec\.?\s*)?(\d{1,3}\.\d{1,3})(?![\d.])/i,
    label: m => `§ ${m[1]}`,
  },
  // WPAM: "Chapter 9", "Ch. 12A".
  {
    pattern: /^(?:chapter|ch\.?)\s*(\d+[a-z]?)\b/i,
    label: m => `Chapter ${m[1].toUpperCase()}`,
  },
  {
    pattern: /^appendix\s+([a-z0-9]+)/i,
    label: m => `Appendix ${m[1].toUpperCase()}`,
  },
  {
    pattern: /^part\s+([ivxlcdm]+|\d+)\b/i,
    label: m => `Part ${m[1].toUpperCase()}`,
  },
  {
    pattern: /^(?:section|sec\.?)\s*(\d+(?:\.\d+)*)/i,
    label: m => `Section ${m[1]}`,
  },
  // Generic dotted outline numbering: "1.2.3 Overview" groups under "1.2".
  {
    pattern: /^(\d+\.\d+)(?:\.\d+)*/,
    label: m => m[1],
  },
];

export function getHeadingPrefix(heading: string | null | undefined): string {
  if (!heading) return '';
  const normalized = heading.replace(/\s+/g, ' ').trim();
  if (!normalized) return '';

  for (const { pattern, label } of HEADING_PATTERNS) {
    const match = normalized.match(pattern);
    if (match) return label(match);
  }
  return normalized;
}

export interface ChunkGroup {
  /** Display label for the section, e.g. "§ 70.11" or "Chapter 9". */
  label: string;
  chunks: ChunkIndexEntry[];
}

/** Collapse a chunk list into runs of consecutive chunks sharing a section. */
export function groupChunksByHeading(chunks: ChunkIndexEntry[]): ChunkGroup[] {
  const groups: ChunkGroup[] = [];
  for (const chunk of chunks) {
    const label = getHeadingPrefix(chunk.heading);
    const last = groups[groups.length - 1];
    if (last && last.label === label) {
      last.chunks.push(chunk);
    } else {
      groups.push({ label, chunks: [chunk] });
    }
  }
  return groups;
}
