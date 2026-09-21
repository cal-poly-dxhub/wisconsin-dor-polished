/** @bun */
import { describe, test, expect, beforeAll } from 'bun:test';
import { JSDOM } from 'jsdom';

import {
  slugifyHeading,
  headingDomId,
  depthForLevel,
  barWidthForDepth,
  barOpacityForDepth,
  indentForDepth,
  collectHeadings,
  MAX_TOC_DEPTH,
} from '../answer-toc';

// collectHeadings walks a real DOM subtree; install a jsdom window for it.
beforeAll(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const g = globalThis as unknown as Record<string, unknown>;
  g.window = dom.window;
  g.document = dom.window.document;
});

describe('slugifyHeading', () => {
  test('lowercases and hyphenates', () => {
    expect(slugifyHeading('How Assessments Work')).toBe('how-assessments-work');
  });

  test('drops punctuation and collapses separators', () => {
    expect(slugifyHeading('§ 70.32(2)(c) — Agricultural Land')).toBe(
      '70-32-2-c-agricultural-land'
    );
  });

  test('strips accents rather than dropping the letter', () => {
    expect(slugifyHeading('Résumé of Appeals')).toBe('resume-of-appeals');
  });

  test('never yields an empty or dangling-hyphen slug', () => {
    expect(slugifyHeading('***')).toBe('section');
    expect(slugifyHeading('   ')).toBe('section');
    const long = slugifyHeading('a'.repeat(30) + ' ' + 'b'.repeat(30));
    expect(long.length).toBeLessThanOrEqual(48);
    expect(long.endsWith('-')).toBe(false);
  });
});

describe('headingDomId', () => {
  test('is scoped by message id, so two answers never collide', () => {
    expect(headingDomId('q-1', 'Overview', 0)).toBe('toc-q-1-0-overview');
    expect(headingDomId('q-2', 'Overview', 0)).toBe('toc-q-2-0-overview');
    expect(headingDomId('q-1', 'Overview', 0)).not.toBe(headingDomId('q-2', 'Overview', 0));
  });

  test('the ordinal disambiguates repeated headings in one answer', () => {
    expect(headingDomId('q-1', 'Exceptions', 1)).not.toBe(
      headingDomId('q-1', 'Exceptions', 4)
    );
  });

  test('is stable for the same inputs', () => {
    expect(headingDomId('q-9', 'When to File', 3)).toBe(headingDomId('q-9', 'When to File', 3));
  });
});

describe('heading-to-bar mapping', () => {
  test('depth is measured against the shallowest heading in the answer', () => {
    // An answer built from h2/h3 reads the same as one built from h3/h4.
    expect(depthForLevel(2, 2)).toBe(0);
    expect(depthForLevel(3, 2)).toBe(1);
    expect(depthForLevel(3, 3)).toBe(0);
    expect(depthForLevel(4, 3)).toBe(1);
  });

  test('depth is clamped so deep nesting never runs off the mapping', () => {
    expect(depthForLevel(6, 1)).toBe(MAX_TOC_DEPTH);
    expect(depthForLevel(1, 3)).toBe(0);
  });

  test('bars get shorter and dimmer as depth increases', () => {
    const widths = [0, 1, 2, 3].map(barWidthForDepth);
    expect(widths).toEqual([28, 20, 15, 12]);
    for (let i = 1; i < widths.length; i += 1) {
      expect(widths[i]).toBeLessThan(widths[i - 1]);
      expect(barOpacityForDepth(i)).toBeLessThan(barOpacityForDepth(i - 1));
    }
  });

  test('out-of-range depths clamp to the ends of the mapping', () => {
    expect(barWidthForDepth(-1)).toBe(28);
    expect(barWidthForDepth(99)).toBe(12);
  });

  test('panel indent grows 12px per depth step', () => {
    expect(indentForDepth(0)).toBe(0);
    expect(indentForDepth(1)).toBe(12);
    expect(indentForDepth(2)).toBe(24);
    expect(indentForDepth(99)).toBe(MAX_TOC_DEPTH * 12);
  });
});

function body(html: string): HTMLElement {
  const el = document.createElement('div');
  el.innerHTML = html;
  return el;
}

describe('collectHeadings', () => {
  test('assigns ids in document order and reports level + depth', () => {
    const root = body(`
      <h2>Overview</h2>
      <p>text</p>
      <h3>Agricultural Land</h3>
      <h3>Undeveloped Land</h3>
      <h2>What to file</h2>
    `);
    const headings = collectHeadings(root, 'q-1');

    expect(headings.map(h => h.text)).toEqual([
      'Overview',
      'Agricultural Land',
      'Undeveloped Land',
      'What to file',
    ]);
    expect(headings.map(h => h.depth)).toEqual([0, 1, 1, 0]);
    expect(headings[0].id).toBe('toc-q-1-0-overview');
    expect(root.querySelector('h3')?.id).toBe('toc-q-1-1-agricultural-land');
  });

  test('ids are unique and stable across repeated collection', () => {
    const root = body('<h2>Notes</h2><h3>Notes</h3><h3>Notes</h3>');
    const first = collectHeadings(root, 'q-7');
    const ids = first.map(h => h.id);
    expect(new Set(ids).size).toBe(3);
    expect(collectHeadings(root, 'q-7').map(h => h.id)).toEqual(ids);
  });

  test('skips empty headings and normalizes whitespace in the label', () => {
    const root = body('<h2>  Filing   Deadlines \n </h2><h3></h3>');
    const headings = collectHeadings(root, 'q-3');
    expect(headings).toHaveLength(1);
    expect(headings[0].text).toBe('Filing Deadlines');
    expect(headings[0].level).toBe(2);
  });

  test('returns nothing for an answer with no headings', () => {
    expect(collectHeadings(body('<p>just prose</p>'), 'q-4')).toEqual([]);
  });
});
