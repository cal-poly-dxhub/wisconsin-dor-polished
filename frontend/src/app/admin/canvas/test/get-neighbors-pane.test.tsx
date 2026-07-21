/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { GetNeighborsPane } from '../components/get-neighbors-pane';

describe('GetNeighborsPane', () => {
  test('renders ten semantically ranked neighbors with scores', () => {
    const neighbors = Array.from({ length: 10 }, (_, index) => ({
      docId: `case-${index + 1}`,
      title: `Case ${index + 1}`,
      relationship: 'SEMANTIC_MATCH',
      rank: index + 1,
      score: 0.99 - index / 100,
    }));

    const html = renderToString(
      <GetNeighborsPane
        data={{
          seedDoc: 'Wis. Stat. 70.32',
          neighbors,
          latencyMs: 42,
          ranked: true,
          query: 'agricultural assessment',
          topK: 10,
          totalCandidates: 27,
        }}
      />,
    );

    expect(html).toContain('Ranked');
    expect(html).toContain('agricultural assessment');
    expect(html).toContain('27');
    expect(html).toContain('top 10');
    expect(html).toContain('Case 1');
    expect(html).toContain('Case 10');
    expect(html).toContain('0.990');
    expect(html).toContain('0.900');
  });
});
