export interface InlineCitation {
  label: string;
  page: number;
}

const DOC_LINK_RE = /\[([^\]]+)\]\(doc:([^)#]+)#page=(\d+)\)/g;

export function parseInlineCitations(markdown: string): Map<string, InlineCitation[]> {
  const result = new Map<string, InlineCitation[]>();

  for (const match of markdown.matchAll(DOC_LINK_RE)) {
    const label = match[1];
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
