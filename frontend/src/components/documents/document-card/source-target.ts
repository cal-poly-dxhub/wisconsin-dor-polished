export type SourceTarget = { kind: 'url'; url: string };

/**
 * Decide where a card's source button should navigate.
 *
 * Every corpus document carries a stable public sourceUrl
 * (revenue.wi.gov, docs.legis.wisconsin.gov, iaao.org, ...). The old
 * citation-resolver fallback (presigned S3 URLs for docs without a public
 * link) was removed once the graph was cleaned of URL-less legacy docs.
 *
 * Kept dependency-free (no module-load side effects) so it stays trivially
 * unit-testable in isolation.
 */
export function chooseSourceTarget(document: {
  sourceUrl?: string;
}): SourceTarget | null {
  if (document.sourceUrl) {
    return { kind: 'url', url: document.sourceUrl };
  }
  return null;
}

export function appendPageFragment(url: string, page?: number): string {
  if (!page || page <= 0) return url;
  return `${url.replace(/#.*$/, '')}#page=${page}`;
}
