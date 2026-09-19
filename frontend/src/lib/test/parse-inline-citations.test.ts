/** @bun */
import { describe, test, expect } from 'bun:test';
import { parseInlineCitations, stripMarkdownEmphasis } from '../parse-inline-citations';

describe('stripMarkdownEmphasis', () => {
  test('strips a matched italic wrapper from a case name', () => {
    expect(stripMarkdownEmphasis('*Hermann v. Town of Delavan*')).toBe(
      'Hermann v. Town of Delavan'
    );
  });

  test('strips bold, bold-italic and underscore wrappers', () => {
    expect(stripMarkdownEmphasis('**Wis. Stat. § 70.32**')).toBe('Wis. Stat. § 70.32');
    expect(stripMarkdownEmphasis('***Markarian***')).toBe('Markarian');
    expect(stripMarkdownEmphasis('_Tax 12.05_')).toBe('Tax 12.05');
    expect(stripMarkdownEmphasis('__Tax 12.05__')).toBe('Tax 12.05');
  });

  test('strips an unbalanced leading or trailing marker', () => {
    expect(stripMarkdownEmphasis('*Hermann v. Town of Delavan')).toBe(
      'Hermann v. Town of Delavan'
    );
    expect(stripMarkdownEmphasis('Hermann v. Town of Delavan**')).toBe(
      'Hermann v. Town of Delavan'
    );
  });

  test('leaves plain labels and interior punctuation alone', () => {
    expect(stripMarkdownEmphasis('§ 70.32(1)')).toBe('§ 70.32(1)');
    expect(stripMarkdownEmphasis('WPAM Ch. 9 — Income Approach')).toBe(
      'WPAM Ch. 9 — Income Approach'
    );
    expect(stripMarkdownEmphasis('State ex rel. *Markarian* v. Cudahy')).toBe(
      'State ex rel. *Markarian* v. Cudahy'
    );
  });

  test('never strips a label down to nothing', () => {
    expect(stripMarkdownEmphasis('***')).toBe('***');
  });
});

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

  test('strips markdown emphasis from the citation label', () => {
    const md = 'See [*Hermann v. Town of Delavan*](doc:case-law-hermann#page=1).';
    const result = parseInlineCitations(md);
    expect(result.get('case-law-hermann')).toEqual([
      { label: 'Hermann v. Town of Delavan', page: 1 },
    ]);
  });

  test('returns empty map for text without doc links', () => {
    const md = 'No citations here, just [a link](https://example.com).';
    const result = parseInlineCitations(md);
    expect(result.size).toBe(0);
  });
});
