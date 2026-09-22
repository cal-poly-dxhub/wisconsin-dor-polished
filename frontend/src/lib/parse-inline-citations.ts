import { parseDocHref } from './parse-doc-href';

export interface InlineCitation {
  label: string;
  page: number;
}

/**
 * Any markdown link whose target starts with `doc:`. The href itself is handed
 * to parseDocHref, which owns the grammar — including the dual-source form
 * `doc:<primary>#page=<p>&ref=<ref>#page=<n>`, whose `&`/second `#` the old
 * "stop at the first #" regex could not express.
 */
const DOC_LINK_RE = /\[([^\]]+)\]\((doc:[^)\s]+)\)/g;

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

/**
 * Map doc_id → the inline citations that point at it, used by the source cards
 * to show which pages an answer actually quoted.
 *
 * A DUAL href (`doc:<primary>#page=<p>&ref=<ref>#page=<n>`) is attributed to
 * the PRIMARY doc only. The label on such a link quotes the primary's words
 * ("the court in *Hermann* held ...") while merely *naming* the ref section,
 * so hanging that label off the ref's card would read as a quotation the ref
 * never made — and would resurrect statute section cards that InlineSources
 * deliberately suppresses when nothing in the prose cited them directly. A
 * ref-only mention therefore does not, by itself, keep a source card alive;
 * the backend should emit a plain single-target link when it wants the
 * statute/admin-rule card to show.
 */
export function parseInlineCitations(markdown: string): Map<string, InlineCitation[]> {
  const result = new Map<string, InlineCitation[]>();

  for (const match of markdown.matchAll(DOC_LINK_RE)) {
    const label = stripMarkdownEmphasis(match[1]);
    const parsed = parseDocHref(match[2]);
    // A citation needs a page to be useful on a card — pageless doc links
    // (e.g. an FAQ) are links, not citations.
    if (!parsed || parsed.page === undefined) continue;
    const docId = parsed.docId;
    const page = parsed.page;

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
