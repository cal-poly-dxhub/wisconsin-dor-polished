/** @bun */
import { describe, test, expect } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { FAQCardCompact } from '../faq-card';

describe('FAQCardCompact source link', () => {
  const base = { faqId: 'faq_1', question: 'Q?', answer: 'A.' };

  test('renders a revenue.wi.gov link when sourceUrl is present', () => {
    const html = renderToString(
      <FAQCardCompact
        faq={{ ...base, sourceUrl: 'https://www.revenue.wi.gov/Pages/FAQS/x.aspx' }}
        isExpanded={false}
        onClick={() => {}}
      />
    );
    expect(html).toContain('https://www.revenue.wi.gov/Pages/FAQS/x.aspx');
    expect(html).toContain('revenue.wi.gov');
  });

  test('renders no source link when sourceUrl is absent', () => {
    const html = renderToString(
      <FAQCardCompact faq={base} isExpanded={false} onClick={() => {}} />
    );
    expect(html).not.toContain('href=');
  });
});
