export function formatTraceMetadata(metadata: unknown): string {
  if (!metadata || typeof metadata !== 'object') return '';
  const m = metadata as Record<string, unknown>;
  const parts: string[] = [];
  const addCount = (key: string, singular: string, plural = `${singular}s`) => {
    const v = m[key];
    if (typeof v === 'number' && v > 0) {
      parts.push(`${v} ${v === 1 ? singular : plural}`);
    }
  };

  addCount('faqCount', 'FAQ hit');
  addCount('chunkCount', 'chunk');
  addCount('docCount', 'doc');
  addCount('neighborCount', 'neighbor');
  addCount('documentCount', 'document');
  addCount('chainLength', 'authority step');
  addCount('citedDocCount', 'citation');
  addCount('opinionChars', 'char');

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

  return parts.join(' · ');
}
