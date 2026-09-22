/** @bun */
import { describe, test, expect } from 'bun:test';
import { parseDocHref } from '../parse-doc-href';

describe('parseDocHref — single target (the only form older answers use)', () => {
  test('doc id with a page', () => {
    expect(parseDocHref('doc:statutes-70#page=38')).toEqual({
      docId: 'statutes-70',
      page: 38,
    });
  });

  test('doc id with no page', () => {
    expect(parseDocHref('doc:faq_pages-slf-useassmt')).toEqual({
      docId: 'faq_pages-slf-useassmt',
    });
  });

  test('an empty page fragment still yields the doc id', () => {
    expect(parseDocHref('doc:statutes-70#page=')).toEqual({ docId: 'statutes-70' });
  });

  test('a zero page is treated as no page', () => {
    expect(parseDocHref('doc:statutes-70#page=0')).toEqual({ docId: 'statutes-70' });
  });
});

describe('parseDocHref — dual target', () => {
  test('primary with a page, ref with a page (& separator)', () => {
    expect(parseDocHref('doc:case_law-hermann#page=3&ref=statutes-74#page=12')).toEqual({
      docId: 'case_law-hermann',
      page: 3,
      ref: { docId: 'statutes-74', page: 12 },
    });
  });

  test('primary with no page uses the # separator before ref', () => {
    expect(parseDocHref('doc:case_law-hermann#ref=statutes-74#page=12')).toEqual({
      docId: 'case_law-hermann',
      ref: { docId: 'statutes-74', page: 12 },
    });
  });

  test('ref may itself have no page', () => {
    expect(parseDocHref('doc:gov_publications-guide#page=7&ref=admin_rules-tax-18')).toEqual({
      docId: 'gov_publications-guide',
      page: 7,
      ref: { docId: 'admin_rules-tax-18' },
    });
  });

  test('neither target needs a page', () => {
    expect(parseDocHref('doc:faq_pages-x#ref=statutes-70')).toEqual({
      docId: 'faq_pages-x',
      ref: { docId: 'statutes-70' },
    });
  });
});

describe('parseDocHref — non-doc and malformed hrefs', () => {
  test('returns null for an external URL', () => {
    expect(parseDocHref('https://example.com')).toBeNull();
  });

  test('returns null for undefined / empty', () => {
    expect(parseDocHref(undefined)).toBeNull();
    expect(parseDocHref('')).toBeNull();
  });

  test('an unrecognized fragment still yields the bare doc id', () => {
    expect(parseDocHref('doc:statutes-70#section=70.32')).toEqual({
      docId: 'statutes-70',
    });
  });
});
