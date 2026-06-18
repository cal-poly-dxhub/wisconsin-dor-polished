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

  test('tool_result replaces pending tool_call with same toolName+turn', () => {
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
      kind: 'tool_call',
      turn: 1,
      seq: 10,
      timestamp: 1,
      payload: { toolName: 'vector_search', status: 'pending' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_result',
      turn: 1,
      seq: 11,
      timestamp: 2,
      payload: {
        toolName: 'vector_search',
        status: 'ok',
        summary: 'Found 6 chunks',
      },
    });
    const trace = useChatStore.getState().queries.q1.agentTrace;
    expect(trace).toHaveLength(1);
    expect(trace?.[0].kind).toBe('tool_result');
    expect(trace?.[0].payload.status).toBe('ok');
  });

  test('duplicate tool_result on already-closed key is dropped', () => {
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
      kind: 'tool_call',
      turn: 1,
      seq: 10,
      timestamp: 1,
      payload: { toolName: 'vector_search', status: 'pending' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_result',
      turn: 1,
      seq: 11,
      timestamp: 2,
      payload: { toolName: 'vector_search', status: 'ok' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_result',
      turn: 1,
      seq: 12,
      timestamp: 3,
      payload: { toolName: 'vector_search', status: 'ok' },
    });
    const trace = useChatStore.getState().queries.q1.agentTrace;
    expect(trace).toHaveLength(1);
    expect(trace?.[0].seq).toBe(11);
  });

  test('tool_result with no matching pending call is appended', () => {
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
      kind: 'tool_result',
      turn: 1,
      seq: 10,
      timestamp: 1,
      payload: { toolName: 'vector_search', status: 'ok' },
    });
    const trace = useChatStore.getState().queries.q1.agentTrace;
    expect(trace).toHaveLength(1);
    expect(trace?.[0].kind).toBe('tool_result');
  });

  test('pairing is keyed by turn, so same tool across turns stays separate', () => {
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
      kind: 'tool_call',
      turn: 1,
      seq: 10,
      timestamp: 1,
      payload: { toolName: 'vector_search', status: 'pending' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_result',
      turn: 1,
      seq: 11,
      timestamp: 2,
      payload: { toolName: 'vector_search', status: 'ok' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_call',
      turn: 2,
      seq: 12,
      timestamp: 3,
      payload: { toolName: 'vector_search', status: 'pending' },
    });
    useChatStore.getState().appendAgentTraceEvent('q1', {
      kind: 'tool_result',
      turn: 2,
      seq: 13,
      timestamp: 4,
      payload: { toolName: 'vector_search', status: 'ok' },
    });
    const trace = useChatStore.getState().queries.q1.agentTrace;
    expect(trace).toHaveLength(2);
    expect(trace?.[0].turn).toBe(1);
    expect(trace?.[1].turn).toBe(2);
  });
});
