/** @bun */
import { describe, test, expect } from 'bun:test';
import { chooseSourceTarget } from '../source-target';

describe('chooseSourceTarget', () => {
  test('PDF s3Key wins over sourceUrl (presigned URL carries #page anchor)', () => {
    const target = chooseSourceTarget({
      s3Key: 'raw/statutes-70/statutes-70.pdf',
      sourceUrl: 'https://docs.legis.wisconsin.gov/statutes/70',
    });
    expect(target).toEqual({ kind: 's3', s3Key: 'raw/statutes-70/statutes-70.pdf' });
  });

  test('non-PDF .txt s3Key yields to gov sourceUrl (flat text has no page anchor)', () => {
    // This is the advisory/news-page case: a .txt blob in S3 plus a clean
    // revenue.wi.gov page. The gov page is the better destination.
    const target = chooseSourceTarget({
      s3Key: 'raw/news_pages-assessor-news-2023-03-02/news_pages-assessor-news-2023-03-02.txt',
      sourceUrl: 'https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-03-02.aspx',
    });
    expect(target).toEqual({
      kind: 'url',
      url: 'https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-03-02.aspx',
    });
  });

  test('non-PDF s3Key with no sourceUrl still falls back to s3Key', () => {
    const target = chooseSourceTarget({ s3Key: 'raw/x/opinion.txt' });
    expect(target).toEqual({ kind: 's3', s3Key: 'raw/x/opinion.txt' });
  });

  test('sourceUrl only', () => {
    const target = chooseSourceTarget({ sourceUrl: 'https://scholar.google.com/x' });
    expect(target).toEqual({ kind: 'url', url: 'https://scholar.google.com/x' });
  });

  test('neither yields null', () => {
    expect(chooseSourceTarget({})).toBeNull();
  });
});
