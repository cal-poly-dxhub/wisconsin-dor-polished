/** @bun */
import { describe, expect, test } from 'bun:test';
import { inFlightQueryIds, persistedAnswer, recoverAnswer } from '../stream-recovery';
import type { Query } from '@/stores/types';

const q = (queryId: string, status: Query['status']): Query => ({
  queryId,
  query: 'q',
  type: 'outbound',
  timestamp: '',
  status,
  response: { type: 'stream', content: '' },
});

describe('stream recovery', () => {
  test('only real, unfinished queries are in flight', () => {
    const ids = inFlightQueryIds({
      a: q('a', 'streaming'),
      b: q('b', 'sent'),
      c: q('c', 'completed'),
      d: q('pending-1', 'sent'),
      e: q('e', 'failed'),
    });
    expect(ids.sort()).toEqual(['a', 'b']);
  });

  test('persistedAnswer finds the query and ignores empty answers', () => {
    const h = { messages: [{ queryId: 'x', answer: '' }, { queryId: 'y', answer: 'full' }] };
    expect(persistedAnswer(h, 'y')).toBe('full');
    expect(persistedAnswer(h, 'x')).toBeNull();
    expect(persistedAnswer(h, 'z')).toBeNull();
  });

  test('polls through failures until the answer is persisted', async () => {
    let calls = 0;
    const answer = await recoverAnswer({
      queryId: 'y',
      stillWaiting: () => true,
      sleep: async () => {},
      fetchHistory: async () => {
        calls++;
        if (calls === 1) throw new Error('offline');
        if (calls === 2) return { messages: [] };
        return { messages: [{ queryId: 'y', answer: 'the whole answer' }] };
      },
    });
    expect(answer).toBe('the whole answer');
    expect(calls).toBe(3);
  });

  test('stops as soon as the query no longer needs recovering', async () => {
    let calls = 0;
    const answer = await recoverAnswer({
      queryId: 'y',
      stillWaiting: () => calls < 1,
      sleep: async () => {},
      fetchHistory: async () => {
        calls++;
        return { messages: [] };
      },
    });
    expect(answer).toBeNull();
    expect(calls).toBe(1);
  });

  test('gives up at the deadline', async () => {
    const answer = await recoverAnswer({
      queryId: 'y',
      stillWaiting: () => true,
      timeoutMs: 0,
      fetchHistory: async () => ({ messages: [] }),
    });
    expect(answer).toBeNull();
  });
});
