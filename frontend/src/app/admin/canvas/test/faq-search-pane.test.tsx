/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { FaqSearchPane } from '../components/faq-search-pane';

describe('FaqSearchPane', () => {
  test('renders score bars, threshold, and transition label', () => {
    const html = renderToString(
      <FaqSearchPane
        data={{
          query: 'What information is used to determine my assessment?',
          latencyMs: 145,
          faqCount: 5,
          topScore: 0.82,
          faqScoreThreshold: 0.7,
          faqScores: [0.82, 0.78, 0.74, 0.65, 0.61],
          topFaqSnippet: 'All real property in Wisconsin must be assessed at its full value.',
          transitionLabel: 'FAQ match found, supplementing with graph search',
        }}
      />,
    );

    expect(html).toContain('FAQ Search');
    expect(html).toContain('145');
    expect(html).toContain('ms');
    expect(html).toContain('What information is used to determine my assessment?');
    expect(html).toContain('FAQ match found, supplementing with graph search');
    expect(html).toContain('0.82');
    expect(html).toContain('0.70');
    expect(html).toContain('above threshold');
    expect(html).toContain('total');
  });

  test('renders empty state when no FAQs match', () => {
    const html = renderToString(
      <FaqSearchPane
        data={{
          query: 'obscure topic',
          faqCount: 0,
          topScore: 0,
          faqScoreThreshold: 0.7,
          faqScores: [],
          topFaqSnippet: '',
        }}
      />,
    );

    expect(html).toContain('No FAQ matches');
    expect(html).not.toContain('Semantic Match Scores');
  });
});
