/** @bun */
import { describe, test, expect } from 'bun:test';
import { formatTraceMetadata } from '../trace-metadata';

describe('formatTraceMetadata', () => {
  test('renders vector_search subtitle with counts + score + latency', () => {
    expect(
      formatTraceMetadata({
        chunkCount: 3,
        docCount: 2,
        neighborCount: 1,
        topScore: 0.84,
        latencyMs: 120,
      }),
    ).toBe('3 chunks · 2 docs · 1 neighbor · top score 0.84 · 120ms');
  });

  test('singularizes counts correctly', () => {
    expect(
      formatTraceMetadata({ chunkCount: 1, docCount: 1, citedDocCount: 1 }),
    ).toBe('1 chunk · 1 doc · 1 citation');
  });

  test('skips zero/missing fields', () => {
    expect(
      formatTraceMetadata({ chunkCount: 0, neighborCount: 2, topScore: 0 }),
    ).toBe('2 neighbors');
  });

  test('handles faq metadata', () => {
    expect(formatTraceMetadata({ faqCount: 2, topScore: 0.84 })).toBe(
      '2 FAQ hits · top score 0.84',
    );
  });

  test('returns empty string for undefined or non-object', () => {
    expect(formatTraceMetadata(undefined)).toBe('');
    expect(formatTraceMetadata(null)).toBe('');
    expect(formatTraceMetadata(42)).toBe('');
    expect(formatTraceMetadata({})).toBe('');
  });

  test('renders refined flag', () => {
    expect(formatTraceMetadata({ refined: true })).toBe('refined');
    expect(formatTraceMetadata({ refined: false })).toBe('');
  });
});
