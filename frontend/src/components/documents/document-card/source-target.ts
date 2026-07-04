export type SourceTarget =
  | { kind: 's3'; s3Key: string }
  | { kind: 'url'; url: string };

/**
 * Decide where a card's source button should navigate.
 *
 * Public sourceUrl is preferred when available — it's a stable link that
 * doesn't expire. The citation resolver (S3 presigned URL) is the fallback
 * for docs without a public URL (e.g. IAAO/USPAP standards).
 *
 * Kept dependency-free (no module-load side effects) so it stays trivially
 * unit-testable in isolation.
 */
export function chooseSourceTarget(document: {
  s3Key?: string;
  sourceUrl?: string;
}): SourceTarget | null {
  if (document.sourceUrl) {
    return { kind: 'url', url: document.sourceUrl };
  }
  if (document.s3Key) {
    return { kind: 's3', s3Key: document.s3Key };
  }
  return null;
}

export function appendPageFragment(url: string, page?: number): string {
  if (!page || page <= 0) return url;
  return `${url.replace(/#.*$/, '')}#page=${page}`;
}
