// Utilities for mapping DOM text selections over rendered markdown to stable
// character offsets, and back. Offsets index into the concatenation of the
// container's text nodes in document order (getPlainText). This survives React
// re-renders because it's anchored to text content, not element identity.

interface OffsetRange {
  start: number;
  end: number;
  quote: string;
}

// Walk text nodes in document order. Skips nothing — the same walker is used by
// getPlainText, selectionToOffsets, and offsetsToRange so offsets stay aligned.
function textNodeWalker(container: Node): TreeWalker {
  return document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
}

export function getPlainText(container: HTMLElement): string {
  const walker = textNodeWalker(container);
  let text = '';
  let node = walker.nextNode();
  while (node) {
    text += node.textContent ?? '';
    node = walker.nextNode();
  }
  return text;
}

// Absolute plain-text offset of a range boundary (node + local offset) within
// `container`. Measured by the length of the text between the container start
// and the boundary, which correctly handles boundaries that land on element
// nodes (e.g. selectNodeContents) as well as text nodes, and stays consistent
// with getPlainText (both concatenate text content in document order).
function absoluteOffset(
  container: HTMLElement,
  boundaryNode: Node,
  boundaryOffset: number
): number {
  const measure = document.createRange();
  measure.selectNodeContents(container);
  measure.setEnd(boundaryNode, boundaryOffset);
  return measure.toString().length;
}

// Map a DOM Selection to plain-text offsets within `container`. Returns null if
// the selection is empty, collapsed, or lies outside the container.
export function selectionToOffsets(
  container: HTMLElement,
  selection: Selection | null
): OffsetRange | null {
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) {
    return null;
  }

  const a = absoluteOffset(container, range.startContainer, range.startOffset);
  const b = absoluteOffset(container, range.endContainer, range.endOffset);
  if (a === null || b === null) return null;

  const start = Math.min(a, b);
  const end = Math.max(a, b);
  if (end <= start) return null;

  const quote = getPlainText(container).slice(start, end);
  return { start, end, quote };
}

// Build a DOM Range spanning [start, end) plain-text offsets in `container`.
// Returns null if the offsets can't be resolved (e.g. content changed).
export function offsetsToRange(
  container: HTMLElement,
  start: number,
  end: number
): Range | null {
  if (end <= start) return null;
  const walker = textNodeWalker(container);
  let acc = 0;
  let startNode: Node | null = null;
  let startLocal = 0;
  let endNode: Node | null = null;
  let endLocal = 0;

  let node = walker.nextNode();
  while (node) {
    const len = node.textContent?.length ?? 0;
    if (startNode === null && acc + len > start) {
      startNode = node;
      startLocal = start - acc;
    }
    if (endNode === null && acc + len >= end) {
      endNode = node;
      endLocal = end - acc;
      break;
    }
    acc += len;
    node = walker.nextNode();
  }

  if (!startNode || !endNode) return null;

  const range = document.createRange();
  range.setStart(startNode, startLocal);
  range.setEnd(endNode, endLocal);
  return range;
}
