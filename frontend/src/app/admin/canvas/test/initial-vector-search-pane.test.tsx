/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { InitialVectorSearchPane } from '../components/initial-vector-search-pane';

const baseData = {
  query: 'What information is used to determine my assessment?',
  latencyMs: 420,
  preDedupCount: 60,
  chunkCount: 15,
  broadChunkCount: 7,
  totalChunkCount: 22,
  topK: 15,
  diversityCapPerDoc: 5,
  docCount: 4,
  topScore: 0.8712,
  docChunks: {
    'wpam-wisconsin-property-assessment-manual-2026': 5,
    'statutes-70': 3,
  },
  authorityBreakdown: { '2': 3, '5': 5 },
  scoreBuckets: { '0.8-0.9': 3, '0.7-0.8': 7 },
  refinedQuery: 'Wisconsin property assessment full value',
  broadQuery: 'What information is used to determine my assessment?',
  broadSkipped: false,
  broadPreDedupCount: 55,
  broadKeptCount: 15,
  broadTopScore: 0.79,
  broadAuthorityBreakdown: { '5': 6, '7': 9 },
  broadScoreBuckets: { '0.8-0.9': 4, '0.7-0.8': 8 },
  broadFullDocChunks: {
    'wpam-wisconsin-property-assessment-manual-2026': 5,
    'news-2025-assessment-deadline-reminder': 4,
  },
  broadDocChunks: {
    'news-2025-assessment-deadline-reminder': 4,
    'gov_publications-2026-agricultural-assessment-guide': 3,
  },
};

describe('InitialVectorSearchPane', () => {
  test('renders parallel arm columns with full breakdown cards', () => {
    const html = renderToString(<InitialVectorSearchPane data={baseData} />);

    expect(html).toContain('Initial Vector Search');
    expect(html).toContain('Narrow arm');
    expect(html).toContain('Broad arm');
    expect(html).toContain('60 Neptune hits');
    expect(html).toContain('55 Neptune hits');
    expect(html).toContain('Authority mix');
    const authorityCount = (html.match(/Authority mix/g) || []).length;
    expect(authorityCount).toBeGreaterThanOrEqual(2);
    const scoreCount = (html.match(/Score distribution/g) || []).length;
    expect(scoreCount).toBeGreaterThanOrEqual(2);
    expect(html).not.toContain('Pipeline');
    expect(html).toContain('Additive merge only');
    expect(html).toContain('chunks to model</span>');
  });

  test('shows broad skipped message in column', () => {
    const html = renderToString(
      <InitialVectorSearchPane
        data={{
          ...baseData,
          broadSkipped: true,
          broadPreDedupCount: undefined,
          broadKeptCount: undefined,
        }}
      />,
    );

    expect(html).toContain('Skipped');
    expect(html).toContain('refined query matches verbatim');
  });
});
