/** @bun */
import { describe, test, expect } from 'bun:test';
import { parseInlineCitations } from '../parse-inline-citations';

describe('parseInlineCitations', () => {
  test('extracts single citation with page', () => {
    const md = 'See [§ 70.32](doc:statutes-70#page=23) for details.';
    const result = parseInlineCitations(md);
    expect(result.get('statutes-70')).toEqual([{ label: '§ 70.32', page: 23 }]);
  });

  test('groups multiple citations for same doc', () => {
    const md = [
      '[§ 70.32](doc:statutes-70#page=23)',
      '[§ 70.47](doc:statutes-70#page=56)',
    ].join(' and ');
    const result = parseInlineCitations(md);
    expect(result.get('statutes-70')).toEqual([
      { label: '§ 70.32', page: 23 },
      { label: '§ 70.47', page: 56 },
    ]);
  });

  test('deduplicates same page', () => {
    const md = '[§ 70.32](doc:statutes-70#page=23) and [Wis. Stat. § 70.32](doc:statutes-70#page=23)';
    const result = parseInlineCitations(md);
    expect(result.get('statutes-70')).toHaveLength(1);
  });

  test('separates citations for different docs', () => {
    const md = '[§ 70.32](doc:statutes-70#page=23) and [Tax 18.05](doc:admin_rules-document-18#page=1)';
    const result = parseInlineCitations(md);
    expect(result.get('statutes-70')).toEqual([{ label: '§ 70.32', page: 23 }]);
    expect(result.get('admin_rules-document-18')).toEqual([{ label: 'Tax 18.05', page: 1 }]);
  });

  test('ignores doc links without #page= fragment', () => {
    const md = '[FAQ](doc:faq_pages-slf-useassmt) is useful.';
    const result = parseInlineCitations(md);
    expect(result.size).toBe(0);
  });

  test('returns empty map for text without doc links', () => {
    const md = 'No citations here, just [a link](https://example.com).';
    const result = parseInlineCitations(md);
    expect(result.size).toBe(0);
  });
});
