/** @bun */
import { describe, test, expect } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { SourceGroupGrid, type SourceEntry } from '../source-group-grid';
import type { SourceKind } from '../source-taxonomy';

const entry = (kind: SourceKind, key: string): SourceEntry => ({
  key,
  kind,
  node: <span>{`card:${key}`}</span>,
});

function orderOfHeaders(html: string, labels: string[]): string[] {
  return labels
    .map(label => ({ label, at: html.indexOf(label) }))
    .filter(x => x.at >= 0)
    .sort((a, b) => a.at - b.at)
    .map(x => x.label);
}

describe('SourceGroupGrid', () => {
  test('renders group headers in authority order with counts', () => {
    const html = renderToString(
      <SourceGroupGrid
        entries={[
          entry('faq', 'f1'),
          entry('wpam', 'w1'),
          entry('statute', 's1'),
          entry('case-law', 'c1'),
          entry('admin-rule', 'a1'),
        ]}
      />
    );
    expect(
      orderOfHeaders(html, [
        'Statutes &amp; Constitution',
        'Case Law',
        'Admin Rules',
        'Assessment Manual (WPAM)',
        'FAQs &amp; News',
      ])
    ).toEqual([
      'Statutes &amp; Constitution',
      'Case Law',
      'Admin Rules',
      'Assessment Manual (WPAM)',
      'FAQs &amp; News',
    ]);
    // Empty groups are not rendered at all.
    expect(html).not.toContain('Government Publications');
    expect(html).not.toContain('>Other<');
  });

  test('expands every group when 6 items or fewer', () => {
    const html = renderToString(
      <SourceGroupGrid
        entries={[
          entry('statute', 's1'),
          entry('case-law', 'c1'),
          entry('admin-rule', 'a1'),
          entry('wpam', 'w1'),
          entry('gov-pub', 'g1'),
          entry('faq', 'f1'),
        ]}
      />
    );
    for (const key of ['s1', 'c1', 'a1', 'w1', 'g1', 'f1']) {
      expect(html).toContain(`card:${key}`);
    }
    expect(html).not.toContain('aria-expanded="false"');
  });

  test('collapses all but the first two groups above 6 items', () => {
    const html = renderToString(
      <SourceGroupGrid
        entries={[
          entry('statute', 's1'),
          entry('case-law', 'c1'),
          entry('admin-rule', 'a1'),
          entry('wpam', 'w1'),
          entry('gov-pub', 'g1'),
          entry('faq', 'f1'),
          entry('news', 'n1'),
        ]}
      />
    );
    // Statutes + Case Law open...
    expect(html).toContain('card:s1');
    expect(html).toContain('card:c1');
    // ...everything below stays collapsed (header only, no card).
    for (const key of ['a1', 'w1', 'g1', 'f1', 'n1']) {
      expect(html).not.toContain(`card:${key}`);
    }
    expect(html).toContain('Admin Rules');
    expect(html).toContain('aria-expanded="false"');
  });

  test('renders the flowchart card above the groups, outside them', () => {
    const html = renderToString(
      <SourceGroupGrid
        entries={[entry('statute', 's1')]}
        leading={<span>flowchart-card</span>}
      />
    );
    expect(html.indexOf('flowchart-card')).toBeGreaterThanOrEqual(0);
    expect(html.indexOf('flowchart-card')).toBeLessThan(
      html.indexOf('Statutes &amp; Constitution')
    );
  });

  test('renders nothing without entries or a leading card', () => {
    expect(renderToString(<SourceGroupGrid entries={[]} />)).toBe('');
  });
});
