/** @bun */
import { describe, test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  ALLOWED_METADATA_KEYS,
  formatTraceMetadata,
  sanitizeTraceMetadata,
} from '../trace-metadata';

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
    ).toBe('3 chunks · 2 sources · 1 graph neighbor enriched · top score 0.84 · 120ms');
  });

  test('singularizes counts correctly', () => {
    expect(
      formatTraceMetadata({ chunkCount: 1, docCount: 1, citedDocCount: 1 }),
    ).toBe('1 chunk · 1 source · 1 citation');
  });

  test('skips zero/missing fields', () => {
    expect(
      formatTraceMetadata({ chunkCount: 0, neighborCount: 2, topScore: 0 }),
    ).toBe('2 graph neighbors enriched');
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

  test('renders section, case, worksheet and flowchart counts', () => {
    expect(formatTraceMetadata({ sectionCount: 12 })).toBe('12 sections');
    expect(formatTraceMetadata({ caseCount: 1 })).toBe('1 case');
    expect(formatTraceMetadata({ worksheetCount: 4 })).toBe('4 worksheets');
    expect(formatTraceMetadata({ sheetCount: 3 })).toBe('3 sheets');
    expect(formatTraceMetadata({ flowchartCount: 6 })).toBe('6 flowcharts');
  });

  test('drops disallowed keys so raw text cannot reach the UI', () => {
    // `query` and `rawUserText` must never be rendered even if a backend
    // version accidentally sends them.
    expect(
      formatTraceMetadata({
        chunkCount: 3,
        query: 'how do I appeal my property tax',
        rawUserText: 'sensitive',
      }),
    ).toBe('3 chunks');
  });
});

describe('sanitizeTraceMetadata', () => {
  test('keeps allowed keys and drops the rest', () => {
    expect(
      sanitizeTraceMetadata({
        chunkCount: 3,
        topScore: 0.9,
        latencyMs: 120,
        query: 'nope',
        arbitraryKey: { nested: true },
      }),
    ).toEqual({ chunkCount: 3, topScore: 0.9, latencyMs: 120 });
  });

  test('returns empty object for non-object input', () => {
    expect(sanitizeTraceMetadata(undefined)).toEqual({});
    expect(sanitizeTraceMetadata(null)).toEqual({});
    expect(sanitizeTraceMetadata('string')).toEqual({});
  });
});

describe('ALLOWED_METADATA_KEYS', () => {
  test('is a subset of the backend allow-list (tracing/emitter.py)', () => {
    const emitter = readFileSync(
      join(import.meta.dir, '../../../../../backend/lambdas/agentic_retrieval/tracing/emitter.py'),
      'utf8',
    );
    const block = emitter.slice(emitter.indexOf('ALLOWED_METADATA_KEYS = frozenset('));
    const backend = new Set(
      [...block.slice(0, block.indexOf('\n)')).matchAll(/"(\w+)"/g)].map(m => m[1]),
    );
    expect(backend.size).toBeGreaterThan(20);
    const stale = [...ALLOWED_METADATA_KEYS].filter(k => !backend.has(k));
    expect(stale).toEqual([]);
  });
});
