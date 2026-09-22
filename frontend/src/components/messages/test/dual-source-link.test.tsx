/** @bun */
import { describe, test, expect } from 'bun:test';
import React from 'react';
import { renderToString } from 'react-dom/server';
import AnimatedMarkdown from '@/components/messages/animated-markdown';

function render(
  content: string,
  opts: {
    docUrls?: Record<string, string>;
    docTones?: Record<string, string>;
    docTitles?: Record<string, string>;
    animate?: boolean;
  } = {}
) {
  return renderToString(
    React.createElement(AnimatedMarkdown as never, {
      content,
      animate: opts.animate ?? false,
      docUrls: opts.docUrls ?? {},
      docTones: opts.docTones,
      docTitles: opts.docTitles,
    })
  );
}

function hrefs(html: string) {
  return (html.match(/href="([^"]*)"/g) ?? []).map(h => h.slice(6, -1));
}

const DUAL =
  'The court in [*Hermann*](doc:case_law-hermann#page=3&ref=statutes-74#page=12) construed the claim statute.';

describe('DualSourceLink', () => {
  test('uses the dual tone and renders a chooser with both rows', () => {
    const html = render(DUAL, {
      docUrls: { 'case_law-hermann': 'https://scholar.google.com/hermann' },
      docTitles: {
        'case_law-hermann': 'Hermann v. Town of Delavan',
        'statutes-74': 'Ch. 74 — Property Tax Collection',
      },
    });

    expect(html).toContain('source-link--dual');
    expect(html).toContain('source-link-dual-popover');
    // Both rows' titles are present in the chooser.
    expect(html).toContain('Hermann v. Town of Delavan');
    expect(html).toContain('Ch. 74 — Property Tax Collection');
    // ...each with its page.
    expect(html).toContain('p. 3');
    expect(html).toContain('p. 12');
    // The chooser is closed until the link is clicked.
    expect(html).toContain('hidden=""');
  });

  test('the outer element is a real anchor at the PRIMARY url (cmd/middle-click)', () => {
    const html = render(DUAL, {
      docUrls: { 'case_law-hermann': 'https://scholar.google.com/hermann' },
    });
    const all = hrefs(html);
    // First anchor is the inline link itself → the primary, page-qualified.
    expect(all[0]).toBe('https://scholar.google.com/hermann#page=3');
    // Then one row per target.
    expect(all).toEqual([
      'https://scholar.google.com/hermann#page=3',
      'https://scholar.google.com/hermann#page=3',
      'https://docs.legis.wisconsin.gov/statutes/statutes/74.pdf#page=12',
    ]);
  });

  test('a statute ref with no page falls back to the per-section legis URL, like a single link', () => {
    const html = render(
      '[§ 74.37 as applied in Hermann](doc:case_law-hermann#page=3#ref=statutes-74)',
      { docUrls: { 'case_law-hermann': 'https://scholar.google.com/hermann' } }
    );
    expect(hrefs(html)).toContain('https://docs.legis.wisconsin.gov/document/statutes/74.37');
  });

  test('falls back to a readable id when no card title is available', () => {
    const html = render(DUAL, {
      docUrls: { 'case_law-hermann': 'https://scholar.google.com/hermann' },
    });
    expect(html).toContain('case_law-hermann');
    expect(html).toContain('Wis. Stat. ch. 74');
  });

  test('works in the animated branch too', () => {
    const html = render(DUAL, {
      animate: true,
      docUrls: { 'case_law-hermann': 'https://scholar.google.com/hermann' },
    });
    expect(html).toContain('source-link--dual');
    expect(html).toContain('source-link-dual-popover');
  });
});

describe('single-target links are untouched (regression: persisted answers)', () => {
  test('renders a plain SourceLink with the authority tone and no chooser', () => {
    const html = render('See [§ 70.32](doc:statutes-70#page=23).', {
      docUrls: { 'statutes-70': 'https://docs.legis.wisconsin.gov/ch70.pdf' },
      docTones: { 'statutes-70': 'statute' },
    });
    expect(html).toContain('source-link source-link--statute');
    expect(html).not.toContain('source-link--dual');
    expect(html).not.toContain('source-link-dual-popover');
    expect(hrefs(html)).toEqual(['https://docs.legis.wisconsin.gov/ch70.pdf#page=23']);
  });

  test('a pageless doc link still resolves to the card URL', () => {
    const html = render('[FAQ](doc:faq_pages-slf-useassmt)', {
      docUrls: { 'faq_pages-slf-useassmt': 'https://revenue.wi.gov/faq' },
      docTones: { 'faq_pages-slf-useassmt': 'faq' },
    });
    expect(hrefs(html)).toEqual(['https://revenue.wi.gov/faq']);
    expect(html).not.toContain('source-link-dual');
  });

  test('an unresolvable doc link degrades to plain text, as before', () => {
    const html = render('[Unknown](doc:mystery-doc)', { docUrls: {} });
    expect(hrefs(html)).toEqual([]);
    expect(html).toContain('Unknown');
  });
});
