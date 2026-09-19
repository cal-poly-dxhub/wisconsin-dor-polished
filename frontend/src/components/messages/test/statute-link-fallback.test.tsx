import { test, expect } from 'bun:test';
import React from 'react';
import { renderToString } from 'react-dom/server';
import AnimatedMarkdown from '@/components/messages/animated-markdown';

function hrefs(content: string, docUrls: Record<string, string>) {
  const html = renderToString(
    React.createElement(AnimatedMarkdown as never, { content, animate: false, docUrls })
  );
  return (html.match(/href="([^"]*)"/g) ?? []).map((h) => h.slice(6, -1));
}

test('a statute link with a page keeps the card PDF URL and swaps the page', () => {
  expect(
    hrefs('[§ 70.47](doc:statutes-70#page=38)', {
      'statutes-70': 'https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf#page=53',
    })
  ).toEqual(['https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf#page=38']);
});

test('a statute link with no page and a section in its label goes to the per-section page', () => {
  expect(hrefs('Under [§ 74.37](doc:statutes-74), file a claim.', {})).toEqual([
    'https://docs.legis.wisconsin.gov/document/statutes/74.37',
  ]);
});

test('a statute link with no page and no section falls back to the chapter PDF', () => {
  expect(hrefs('[chapter 74](doc:statutes-74)', {})).toEqual([
    'https://docs.legis.wisconsin.gov/statutes/statutes/74.pdf',
  ]);
});
