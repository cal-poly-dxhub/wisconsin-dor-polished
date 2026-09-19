/** @bun */
import { describe, test, expect } from 'bun:test';
import {
  classifySourceKind,
  defaultOpenGroupIds,
  groupSourceEntries,
  SOURCE_GROUPS,
  SOURCE_KIND_META,
  type SourceKind,
} from '../source-taxonomy';

describe('classifySourceKind', () => {
  test('classifies by doc_id prefix', () => {
    const cases: [string, SourceKind][] = [
      ['constitution-article-viii', 'constitution'],
      ['statutes-70', 'statute'],
      ['statute-70-32', 'statute'],
      ['case-law/hermann-v-town-of-delavan', 'case-law'],
      ['admin_rules-tax-12', 'admin-rule'],
      ['wpam-2024-volume-1', 'wpam'],
      ['gov_publications-guide-for-assessors', 'gov-pub'],
      ['complex_inquiry_pages-manufacturing', 'gov-pub'],
      ['form_instructions-pa-521c', 'form'],
      ['faq_pages-slf-useassmt', 'faq'],
      ['news_pages-2026-04-29-camping-trailers', 'news'],
      ['iaao-standard-on-mass-appraisal', 'iaao'],
      ['uspap-2024-standard-6', 'uspap'],
    ];
    for (const [documentId, expected] of cases) {
      expect(classifySourceKind({ documentId })).toBe(expected);
    }
  });

  test('tolerates the raw/ prefix and mixed case', () => {
    expect(classifySourceKind({ documentId: 'raw/WPAM-2024-Volume-1' })).toBe('wpam');
  });

  test('falls back to authority level when the prefix is unfamiliar', () => {
    expect(classifySourceKind({ documentId: 'mystery-doc', authorityLevel: 2 })).toBe('statute');
    expect(classifySourceKind({ documentId: 'mystery-doc', authorityLevel: 5 })).toBe('wpam');
  });

  test('news beats the gov-pub authority level it is loaded under', () => {
    // news_pages-* has no source_to_framework entry, so it lands on FW-GOV-PUBS
    // (level 7). The id prefix is the only signal that it is a news page.
    expect(classifySourceKind({ documentId: 'news_pages-x', authorityLevel: 7 })).toBe('news');
  });

  test('returns "other" when nothing matches', () => {
    expect(classifySourceKind({})).toBe('other');
    expect(classifySourceKind({ documentId: 'zzz', authorityLevel: 42 })).toBe('other');
  });

  test('every kind has card + accent styling', () => {
    for (const group of SOURCE_GROUPS) {
      for (const kind of group.kinds) {
        expect(SOURCE_KIND_META[kind].label.length).toBeGreaterThan(0);
        expect(SOURCE_KIND_META[kind].card).toContain('bg-');
        expect(SOURCE_KIND_META[kind].card).not.toContain('border-l');
        expect(SOURCE_KIND_META[kind].accent).toContain('dark:');
      }
    }
  });
});

const entry = (kind: SourceKind) => ({ kind });

describe('groupSourceEntries', () => {
  test('orders groups by authority and drops empty ones', () => {
    const groups = groupSourceEntries([
      entry('faq'),
      entry('wpam'),
      entry('statute'),
      entry('case-law'),
      entry('gov-pub'),
      entry('admin-rule'),
      entry('uspap'),
      entry('news'),
      entry('constitution'),
      entry('form'),
    ]);
    expect(groups.map(g => g.id)).toEqual([
      'statutes',
      'case-law',
      'admin-rules',
      'wpam',
      'gov-pubs',
      'faqs-news',
      'other',
    ]);
    expect(groups[0].label).toBe('Statutes & Constitution');
    // statute + constitution land together, in the caller's original order.
    expect(groups[0].items).toEqual([entry('statute'), entry('constitution')]);
    expect(groups.find(g => g.id === 'gov-pubs')!.items).toHaveLength(2);
    expect(groups.find(g => g.id === 'faqs-news')!.items).toHaveLength(2);
  });

  test('omits groups with no members', () => {
    const groups = groupSourceEntries([entry('wpam'), entry('wpam')]);
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe('wpam');
    expect(groups[0].items).toHaveLength(2);
  });

  test('returns nothing for no entries', () => {
    expect(groupSourceEntries([])).toEqual([]);
  });
});

describe('defaultOpenGroupIds', () => {
  test('opens every group when the total is 6 items or fewer', () => {
    const groups = groupSourceEntries([
      entry('statute'),
      entry('case-law'),
      entry('admin-rule'),
      entry('wpam'),
      entry('gov-pub'),
      entry('faq'),
    ]);
    expect(groups).toHaveLength(6);
    expect(defaultOpenGroupIds(groups)).toEqual(groups.map(g => g.id));
  });

  test('opens only the first two non-empty groups above 6 items', () => {
    const groups = groupSourceEntries([
      entry('statute'),
      entry('case-law'),
      entry('admin-rule'),
      entry('wpam'),
      entry('gov-pub'),
      entry('faq'),
      entry('news'),
    ]);
    expect(defaultOpenGroupIds(groups)).toEqual(['statutes', 'case-law']);
  });

  test('"first two" skips groups that have no members', () => {
    const groups = groupSourceEntries([
      entry('wpam'),
      entry('wpam'),
      entry('wpam'),
      entry('faq'),
      entry('faq'),
      entry('gov-pub'),
      entry('gov-pub'),
    ]);
    // No statutes / case law / admin rules present, so WPAM and Gov. Pubs open.
    expect(defaultOpenGroupIds(groups)).toEqual(['wpam', 'gov-pubs']);
  });
});
