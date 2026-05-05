/** @bun */
import { describe, test, expect, beforeEach } from 'bun:test';
import { useChatStore } from '../chat-store';
import type { Query } from '../types';

describe('chat-store agentTrace', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  test('appendAgentTraceEvent creates array on first call', () => {
    const q: Query = {
      query: 'hello',
      queryId: 'q1',
      type: 'outbound',
      timestamp: new Date().toISOString(),
      status: 'pending',
      response: { type: 'stream', content: '' },
    };
    useChatStore.getState().addQuery(q);
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start',
      seq: 1,
      timestamp: 1,
      payload: { maxTurns: 10 },
    });
    expect(useChatStore.getState().queries.q1.agentTrace).toHaveLength(1);
    expect(useChatStore.getState().queries.q1.agentTrace?.[0].kind).toBe(
      'loop_start',
    );
  });

  test('appendAgentTraceEvent dedupes by seq', () => {
    const q: Query = {
      query: 'hello',
      queryId: 'q1',
      type: 'outbound',
      timestamp: new Date().toISOString(),
      status: 'pending',
      response: { type: 'stream', content: '' },
    };
    useChatStore.getState().addQuery(q);
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start',
      seq: 1,
      timestamp: 1,
      payload: {},
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'loop_start',
      seq: 1,
      timestamp: 1,
      payload: {},
    });
    expect(useChatStore.getState().queries.q1.agentTrace).toHaveLength(1);
  });

  test('appendAgentTraceEvent is a no-op for unknown queryId', () => {
    useChatStore.getState().appendAgentTraceEvent('nonexistent', {
      kind: 'loop_start',
      seq: 1,
      timestamp: 1,
      payload: {},
    });
    expect(useChatStore.getState().queries).toEqual({});
  });
});
