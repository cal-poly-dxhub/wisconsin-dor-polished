// Allow-list mirrors ALLOWED_METADATA_KEYS in
// packages/graphrag/lambdas/agentic_retrieval/main.py. Any key not listed
// here is dropped before rendering — defense-in-depth in case a backend
// version slips through with free-form content.
const ALLOWED_METADATA_KEYS = new Set([
  'chunkCount',
  'docCount',
  'neighborCount',
  'topScore',
  'faqCount',
  'documentCount',
  'chainLength',
  'opinionChars',
  'refined',
  'citedDocCount',
  'latencyMs',
  'keywordFallback',
  'preDedupCount',
  'authorityBreakdown',
  'relationshipCounts',
  'discoveryCounts',
  'caseLawCount',
]);

export function sanitizeTraceMetadata(
  metadata: unknown,
): Record<string, unknown> {
  if (!metadata || typeof metadata !== 'object') return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(metadata as Record<string, unknown>)) {
    if (ALLOWED_METADATA_KEYS.has(k)) out[k] = v;
  }
  return out;
}

export function formatTraceMetadata(metadata: unknown): string {
  const m = sanitizeTraceMetadata(metadata);
  const parts: string[] = [];
  const addCount = (key: string, singular: string, plural = `${singular}s`) => {
    const v = m[key];
    if (typeof v === 'number' && v > 0) {
      parts.push(`${v} ${v === 1 ? singular : plural}`);
    }
  };

  addCount('faqCount', 'FAQ hit');
  addCount('chunkCount', 'chunk');
  addCount('docCount', 'source');
  addCount('documentCount', 'document');
  addCount('chainLength', 'authority step');
  addCount('citedDocCount', 'citation');
  addCount('opinionChars', 'char');

  const neighborCount = m.neighborCount;
  if (typeof neighborCount === 'number' && neighborCount > 0) {
    parts.push(`${neighborCount} graph ${neighborCount === 1 ? 'neighbor' : 'neighbors'} enriched`);
  }

  const topScore = m.topScore;
  if (typeof topScore === 'number' && topScore > 0) {
    parts.push(`top score ${topScore.toFixed(2)}`);
  }
  const latencyMs = m.latencyMs;
  if (typeof latencyMs === 'number' && latencyMs > 0) {
    parts.push(`${latencyMs}ms`);
  }
  if (m.refined === true) {
    parts.push('refined');
  }
  if (m.keywordFallback === true) {
    parts.push('keyword fallback');
  }

  return parts.join(' · ');
}
