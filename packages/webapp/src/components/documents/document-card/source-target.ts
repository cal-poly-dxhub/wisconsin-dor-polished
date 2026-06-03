export type SourceTarget =
  | { kind: 's3'; s3Key: string }
  | { kind: 'url'; url: string };

/**
 * Decide where a card's source button should navigate.
 *
 * S3 objects are preferred ONLY for PDFs: the citation resolver mints a
 * presigned URL with a `#page=N` anchor that drops the user on the exact
 * cited page. Flat `.txt` objects (case-law opinions, advisory/news pages)
 * have no page anchor, so a clean public `sourceUrl` (e.g. a revenue.wi.gov
 * page or Google Scholar) is strictly better than a presigned blob link.
 *
 * Kept dependency-free (no module-load side effects) so it stays trivially
 * unit-testable in isolation.
 */
export function chooseSourceTarget(document: {
  s3Key?: string;
  sourceUrl?: string;
}): SourceTarget | null {
  const isPdf = !!document.s3Key && /\.pdf$/i.test(document.s3Key);
  if (document.s3Key && (isPdf || !document.sourceUrl)) {
    return { kind: 's3', s3Key: document.s3Key };
  }
  if (document.sourceUrl) {
    return { kind: 'url', url: document.sourceUrl };
  }
  return null;
}
