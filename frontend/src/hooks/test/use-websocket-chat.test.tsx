/** @bun */
import { describe, test, expect, beforeEach, afterEach, mock } from 'bun:test';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useWebSocketChat } from '../use-websocket-chat';
import { useChatStore } from '../../stores/chat-store';
import type {
  MessageHandler,
  MessageUnion,
} from '@messages/websocket-interface';

// Set up DOM environment for React Testing Library
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'http://localhost',
});

global.document = dom.window.document;
(global as unknown as { window: typeof dom.window }).window = dom.window;
global.navigator = dom.window.navigator;

// Capture the sendMessage mock so tests can verify it was called
const mockWsSendMessage = mock(() => Promise.resolve());
const mockClose = mock(() => {});
const mockReconnect = mock(() => {});

// Mock the validated websocket hook — captures the messageHandler for tests
const mockUseValidatedWebSocket = mock(
  (_messageHandler?: MessageHandler, _options?: unknown) => {
    return {
      connectionState: 'open' as const,
      isConnected: true,
      lastMessage: null,
      sendMessage: mockWsSendMessage,
      close: mockClose,
      reconnect: mockReconnect,
      sessionId: 'test-session-123',
      error: null,
    };
  }
);

// Mock the modules
mock.module('../use-validated-websocket', () => ({
  useValidatedWebSocket: mockUseValidatedWebSocket,
}));

mock.module('../../components/errors/use-chat-error', () => ({
  useChatError: () => ({ handleError: mock(() => {}) }),
}));

describe('useWebSocketChat Hook Tests', () => {
  let mockMessageHandler: ((message: MessageUnion) => void) | null = null;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    useChatStore.getState().reset();
    mockWsSendMessage.mockClear();

    mockUseValidatedWebSocket.mockImplementation(
      (messageHandler?: MessageHandler) => {
        if (messageHandler) mockMessageHandler = messageHandler;

        return {
          connectionState: 'open' as const,
          isConnected: true,
          lastMessage: null,
          sendMessage: mockWsSendMessage,
          close: mockClose,
          reconnect: mockReconnect,
          sessionId: 'test-session-123',
          error: null,
        };
      }
    );
  });

  afterEach(() => {
    useChatStore.getState().reset();
  });

  test('should handle complete chat flow correctly', async () => {
    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (qc: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    const { result } = renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    expect(mockMessageHandler).toBeDefined();

    // Initial state
    let store = useChatStore.getState();
    expect(store.chatState).toBe('idle');
    expect(store.queries).toEqual({});
    expect(store.queryOrder).toEqual([]);

    // Send a message — creates optimistic query and calls WS sendMessage
    await act(async () => {
      await result.current.sendMessage('What is Wisconsin?');
    });

    expect(mockWsSendMessage).toHaveBeenCalledWith('What is Wisconsin?');

    // Optimistic query should be in the store
    store = useChatStore.getState();
    expect(store.queryOrder).toHaveLength(1);
    const optimisticId = store.queryOrder[0];
    expect(store.queries[optimisticId].query).toBe('What is Wisconsin?');
    expect(store.queries[optimisticId].status).toBe('pending');

    // Simulate server replacing optimistic ID via answer-event start
    const queryId = 'server-query-456';

    // First message with real queryId triggers eager replaceQueryId
    const startEvent: MessageUnion = {
      responseType: 'answer-event',
      event: 'start',
      queryId,
    };

    act(() => {
      mockMessageHandler!(startEvent);
    });

    store = useChatStore.getState();
    expect(store.queries[queryId]).toBeDefined();
    expect(store.queries[queryId].status).toBe('streaming');
    expect(store.chatState).toBe('streaming');

    // Simulate fragments
    const fragments = ['Hello', ' world', '!'];
    fragments.forEach(fragment => {
      act(() => {
        mockMessageHandler!({
          responseType: 'fragment',
          queryId,
          content: { fragment },
        });
      });
    });

    // Fragments are buffered until stop
    store = useChatStore.getState();
    expect(store.queries[queryId].response.content).toBe('');

    // Stop event flushes buffer
    act(() => {
      mockMessageHandler!({
        responseType: 'answer-event',
        event: 'stop',
        queryId,
      });
    });

    store = useChatStore.getState();
    expect(store.chatState).toBe('idle');
    expect(store.queries[queryId].status).toBe('completed');
    expect(store.queries[queryId].response.content).toBe('Hello world!');
  });

  test('should handle API send error gracefully', async () => {
    mockWsSendMessage.mockImplementationOnce(() =>
      Promise.reject(new Error('Network error'))
    );

    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (qc: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    const { result } = renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    // Send should not throw — the hook handles the error internally
    await act(async () => {
      try {
        await result.current.sendMessage('What is Wisconsin?');
      } catch {
        // Expected — sendMessage may propagate the rejection
      }
    });

    // The optimistic query was still added to the store
    const store = useChatStore.getState();
    expect(store.queryOrder).toHaveLength(1);
  });

  test('should handle WebSocket message processing error gracefully', async () => {
    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (qc: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    expect(mockMessageHandler).toBeDefined();

    // Add a query to the store so the message handler doesn't early-return
    const queryId = 'test-query-456';
    act(() => {
      useChatStore.getState().addQuery({
        query: 'hello',
        queryId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'pending',
        response: { type: 'stream', content: '' },
      });
    });

    // Simulate a malformed message — missing content.fragment
    const malformedMessage = {
      responseType: 'fragment',
      queryId,
    } as MessageUnion;

    // Should not crash
    act(() => {
      mockMessageHandler!(malformedMessage);
    });

    const store = useChatStore.getState();
    expect(store.queries[queryId]).toBeDefined();
  });

  test('routes agent-event messages into agentTrace store', async () => {
    const options = { websocketUrl: 'wss://test-websocket.example.com' };
    const createWrapper = (qc: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };
    renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    const queryId = 'q-agent-1';
    act(() => {
      useChatStore.getState().addQuery({
        query: 'hello',
        queryId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'pending',
        response: { type: 'stream', content: '' },
      });
    });

    const event1: MessageUnion = {
      responseType: 'agent-event',
      queryId,
      kind: 'loop_start',
      turn: null,
      seq: 1,
      timestamp: 1000,
      payload: { maxTurns: 10 },
      devPayload: {},
    };
    const event2: MessageUnion = {
      responseType: 'agent-event',
      queryId,
      kind: 'tool_call',
      turn: 1,
      seq: 2,
      timestamp: 1100,
      payload: { toolName: 'vector_search', summary: '"use value"' },
      devPayload: {},
    };
    // Duplicate seq — should be deduped.
    const event2dup: MessageUnion = { ...event2 };

    act(() => {
      mockMessageHandler!(event1);
      mockMessageHandler!(event2);
      mockMessageHandler!(event2dup);
    });

    const trace = useChatStore.getState().queries[queryId].agentTrace;
    expect(trace).toHaveLength(2);
    expect(trace?.[0].kind).toBe('loop_start');
    expect(trace?.[1].kind).toBe('tool_call');
    expect(trace?.[1].payload.toolName).toBe('vector_search');
  });

  test('routes choices message into query choices store', async () => {
    const options = { websocketUrl: 'wss://test-websocket.example.com' };
    const createWrapper = (qc: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };
    renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    const queryId = 'q-choices-1';
    act(() => {
      useChatStore.getState().addQuery({
        query: 'how is my property assessed?',
        queryId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'completed',
        response: { type: 'stream', content: 'Please select an option.' },
      });
    });

    const choicesMessage: MessageUnion = {
      responseType: 'choices',
      queryId,
      content: { choices: ['Residential', 'Commercial', 'Agricultural'] },
    };

    act(() => {
      mockMessageHandler!(choicesMessage);
    });

    const query = useChatStore.getState().queries[queryId];
    expect(query.choices).toEqual(['Residential', 'Commercial', 'Agricultural']);
  });
});
