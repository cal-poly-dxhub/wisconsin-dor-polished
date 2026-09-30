// The subset of the backend's ALLOWED_METADATA_KEYS
// (backend/lambdas/agentic_retrieval/tracing/emitter.py) that this formatter
// renders. Any key not listed is dropped before rendering: defense-in-depth in
// case a backend version slips through with free-form content. It only needs
// the keys formatTraceMetadata reads; a test fails if one of them is no longer
// on the backend list, which is how stale keys used to linger here.
export const ALLOWED_METADATA_KEYS = new Set([
  'chunkCount',
  'docCount',
  'neighborCount',
  'topScore',
  'faqCount',
  'documentCount',
  'opinionChars',
  'citedDocCount',
  'latencyMs',
  'keywordFallback',
  'sectionCount',
  'caseCount',
  'worksheetCount',
  'sheetCount',
  'flowchartCount',
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
  addCount('citedDocCount', 'citation');
  addCount('opinionChars', 'char');
  addCount('sectionCount', 'section');
  addCount('caseCount', 'case');
  addCount('worksheetCount', 'worksheet');
  addCount('sheetCount', 'sheet');
  addCount('flowchartCount', 'flowchart');

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
  if (m.keywordFallback === true) {
    parts.push('keyword fallback');
  }

  return parts.join(' · ');
}
