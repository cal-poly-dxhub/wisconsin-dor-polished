export interface InlineCitation {
  label: string;
  page: number;
}

const DOC_LINK_RE = /\[([^\]]+)\]\(doc:([^)#]+)#page=(\d+)\)/g;

// Matched emphasis wrapper: ***x***, **x**, __x__, *x*, _x_.
const WRAPPED_EMPHASIS_RE = /^(\*\*\*|___|\*\*|__|\*|_)([\s\S]+?)\1$/;

/**
 * Citation labels are rendered as plain text, but the model often writes case
 * names with markdown emphasis ("*Hermann v. Town of Delavan*"). Nothing
 * renders that markdown downstream, so the asterisks leak into the card. Strip
 * matched wrappers first, then any stray leading/trailing marker run.
 */
export function stripMarkdownEmphasis(text: string): string {
  const original = text.trim();
  let out = original;

  for (;;) {
    const next = out.replace(WRAPPED_EMPHASIS_RE, '$2').trim();
    if (next === out) break;
    out = next;
  }

  const unwrapped = out.replace(/^[*_]+/, '').replace(/[*_]+$/, '').trim();
  // A label made only of markers is not emphasis — leave it as the author wrote it.
  return unwrapped.length > 0 ? unwrapped : original;
}

export function parseInlineCitations(markdown: string): Map<string, InlineCitation[]> {
  const result = new Map<string, InlineCitation[]>();

  for (const match of markdown.matchAll(DOC_LINK_RE)) {
    const label = stripMarkdownEmphasis(match[1]);
    const docId = match[2];
    const page = parseInt(match[3], 10);

    if (!result.has(docId)) {
      result.set(docId, []);
    }
    const entries = result.get(docId)!;
    if (!entries.some(e => e.page === page)) {
      entries.push({ label, page });
    }
  }

  return result;
}
