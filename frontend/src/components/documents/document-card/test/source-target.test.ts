/** @bun */
import { describe, test, expect } from 'bun:test';
import { appendPageFragment, chooseSourceTarget } from '../source-target';

describe('chooseSourceTarget', () => {
  test('sourceUrl produces a url target (public link is stable, no auth needed)', () => {
    const target = chooseSourceTarget({
      sourceUrl: 'https://docs.legis.wisconsin.gov/statutes/statutes/70.pdf',
    });
    expect(target).toEqual({ kind: 'url', url: 'https://docs.legis.wisconsin.gov/statutes/statutes/70.pdf' });
  });

  test('no s3 fallback: a doc without sourceUrl yields null', () => {
    // The citation-resolver (presigned S3) path was removed — every corpus
    // doc must carry a public sourceUrl.
    const target = chooseSourceTarget({ sourceUrl: undefined });
    expect(target).toBeNull();
  });

  test('sourceUrl only', () => {
    const target = chooseSourceTarget({ sourceUrl: 'https://scholar.google.com/x' });
    expect(target).toEqual({ kind: 'url', url: 'https://scholar.google.com/x' });
  });

  test('neither yields null', () => {
    expect(chooseSourceTarget({})).toBeNull();
  });
});

describe('appendPageFragment', () => {
  test('appends #page=N for positive page', () => {
    expect(appendPageFragment('https://example.com/doc.pdf', 5)).toBe('https://example.com/doc.pdf#page=5');
  });

  test('returns url unchanged when page is undefined', () => {
    expect(appendPageFragment('https://example.com/doc.pdf', undefined)).toBe('https://example.com/doc.pdf');
  });

  test('returns url unchanged when page is 0', () => {
    expect(appendPageFragment('https://example.com/doc.pdf', 0)).toBe('https://example.com/doc.pdf');
  });
});
