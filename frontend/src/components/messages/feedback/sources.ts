import type { ResourceItem, Document, FAQ } from '@/stores/types';

// A flattened, display-ready view of a cited source, used by the triage and
// per-source-note UIs. Keeps id derivation (documentId vs faqId) in one place.
export interface SourceEntry {
  id: string;
  title: string;
  kind: 'document' | 'faq';
}

interface InlineLink {
  id: string;
  label: string;
}

const MD_LINK_RE = /\[([^\]]+)\]\(([^)\s]+)\)/g;

// Every inline markdown link in the response, deduped by label+href. Used by the
// "wrong page / broken link" picker so the user can point at the exact link.
// (href is used only to build a stable dedup id, not surfaced in the UI.)
export function parseInlineLinks(markdown: string | undefined | null): InlineLink[] {
  if (!markdown) return [];
  const seen = new Set<string>();
  const links: InlineLink[] = [];
  for (const match of markdown.matchAll(MD_LINK_RE)) {
    const label = match[1].trim();
    const href = match[2].trim();
    const id = `${label}::${href}`;
    if (seen.has(id)) continue;
    seen.add(id);
    links.push({ id, label });
  }
  return links;
}

export function toSourceEntries(items: ResourceItem[]): SourceEntry[] {
  return items.map((item) => {
    if (item.type === 'document') {
      const doc = item.data as Document;
      return { id: `doc-${doc.documentId}`, title: doc.title, kind: 'document' as const };
    }
    const faq = item.data as FAQ;
    return { id: `faq-${faq.faqId}`, title: faq.question, kind: 'faq' as const };
  });
}
