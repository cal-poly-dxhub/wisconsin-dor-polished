/** @bun */
import { describe, test, expect, beforeEach, afterEach, mock } from 'bun:test';
import { renderHook, act, waitFor } from '@testing-library/react';
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

// Mock the validated websocket hook
const mockUseValidatedWebSocket = mock(() => {
  return {
    connectionState: 'connecting',
    isConnected: false,
    lastMessage: null,
    sendMessage: mock(() => {}),
    close: mock(() => {}),
    reconnect: mock(() => {}),
    error: null,
  };
});

// Mock the chat API functions with controllable promises
let resolveSendMessage: (value: { query_id: string }) => void;
let rejectSendMessage: (error: Error) => void;
let resolveCreateSession: (value: { sessionId: string }) => void;
let rejectCreateSession: (error: Error) => void;

const mockSendMessage = mock(
  () =>
    new Promise((resolve, reject) => {
      resolveSendMessage = resolve;
      rejectSendMessage = reject;
    })
);

const mockCreateSession = mock(
  () =>
    new Promise((resolve, reject) => {
      resolveCreateSession = resolve;
      rejectCreateSession = reject;
    })
);

// Mock the modules
mock.module('../use-validated-websocket', () => ({
  useValidatedWebSocket: mockUseValidatedWebSocket,
}));

mock.module('../../api/chat-api', () => ({
  sendMessage: mockSendMessage,
  createSession: mockCreateSession,
}));

// Mock crypto.randomUUID
Object.defineProperty(global, 'crypto', {
  value: {
    randomUUID: mock(() => 'test-uuid-123'),
  },
});

describe('useWebSocketChat Hook Tests', () => {
  let mockMessageHandler: ((message: MessageUnion) => void) | null = null;
  let mockClose: ReturnType<typeof mock>;
  let mockReconnect: ReturnType<typeof mock>;
  let queryClient: QueryClient;

  beforeEach(() => {
    // Create a new QueryClient for each test
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    // Reset the chat store to initial state
    const store = useChatStore.getState();
    store.reset();

    mockClose = mock(() => {});
    mockReconnect = mock(() => {});

    // Setup validated websocket mock
    mockUseValidatedWebSocket.mockImplementation(
      (messageHandler: MessageHandler) => {
        mockMessageHandler = messageHandler;

        return {
          connectionState: 'connecting',
          isConnected: false,
          lastMessage: null,
          sendMessage: mock(() => {}),
          close: mockClose,
          reconnect: mockReconnect,
          error: null,
        };
      }
    );

    mockSendMessage.mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          resolveSendMessage = resolve;
          rejectSendMessage = reject;
        })
    );

    mockCreateSession.mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          resolveCreateSession = resolve;
          rejectCreateSession = reject;
        })
    );
  });

  afterEach(() => {
    // Reset the chat store to initial state
    const store = useChatStore.getState();
    store.reset();
  });

  test('should handle complete chat flow correctly', async () => {
    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (queryClient: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    const { result } = renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    // Verify message handler was captured
    expect(mockMessageHandler).toBeDefined();

    // Initial state verification
    let store = useChatStore.getState();
    expect(store.connectionState).toBe('closed');
    expect(store.chatState).toBe('idle');
    expect(store.queries).toEqual({});
    expect(store.queryOrder).toEqual([]);

    const sessionId = 'test-session-123';
    const queryId = 'test-query-456';

    // Step 1: Send a message (sendMessage now takes a plain string)
    await act(async () => {
      result.current.sendMessage('What is Wisconsin?');
    });

    // Wait for the session creation to be called and resolve it
    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalled();
    });

    // Resolve session creation
    resolveCreateSession({ sessionId });

    // Wait for the send message to be called
    await waitFor(() => {
      expect(mockSendMessage).toHaveBeenCalledWith(sessionId, {
        message: 'What is Wisconsin?',
      });
    });

    // Verify chat state is 'sending' while the API request is pending
    store = useChatStore.getState();
    expect(store.chatState).toBe('sending');

    // Manually resolve the API promise
    resolveSendMessage({
      query_id: queryId,
    });

    // Step 2: Wait for the mutation to complete and verify query was created
    await waitFor(() => {
      store = useChatStore.getState();
      expect(store.queries[queryId]).toBeDefined();
      expect(store.queries[queryId].query).toBe('What is Wisconsin?');
      expect(store.queries[queryId].queryId).toBe(queryId);
      expect(store.queries[queryId].type).toBe('outbound');
      expect(store.queries[queryId].status).toBe('sent');
      expect(store.currentQueryId).toBe(queryId);
      expect(store.chatState).toBe('waiting_for_response');
      expect(store.queryOrder).toContain(queryId);
    });

    // Step 3: Simulate streaming response via WebSocket
    const startEvent: MessageUnion = {
      responseType: 'answer-event',
      event: 'start',
      queryId,
    };

    act(() => {
      mockMessageHandler!(startEvent);
    });

    // Verify streaming state
    store = useChatStore.getState();
    expect(store.chatState).toBe('streaming');
    expect(store.queries[queryId].status).toBe('streaming');

    // Simulate message fragments
    const fragments = [
      'Hello',
      ' world',
      '! This is a',
      ' streaming',
      ' response.',
    ];

    fragments.forEach(fragment => {
      const fragmentMessage: MessageUnion = {
        responseType: 'fragment',
        queryId,
        content: {
          fragment,
        },
      };

      act(() => {
        mockMessageHandler!(fragmentMessage);
      });
    });

    // Verify the complete message was constructed in the store
    store = useChatStore.getState();
    const query = store.queries[queryId];
    expect(query).toBeDefined();
    expect(query.response.content).toBe(
      'Hello world! This is a streaming response.'
    );

    // State is streaming before the stop event
    expect(store.chatState).toBe('streaming');

    // Step 4: Simulate streaming completion
    const stopEvent: MessageUnion = {
      responseType: 'answer-event',
      event: 'stop',
      queryId,
    };

    act(() => {
      mockMessageHandler!(stopEvent);
    });

    // Verify final state
    store = useChatStore.getState();
    expect(store.chatState).toBe('idle');
    expect(store.queries[queryId].status).toBe('completed');
    expect(store.currentQueryId).toBe(queryId);

    // Verify the complete flow maintained proper state
    expect(Object.keys(store.queries)).toHaveLength(1);
    expect(store.queryOrder).toHaveLength(1);
    expect(store.errors).toHaveLength(0);
  });

  test('should handle API send error gracefully', async () => {
    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (queryClient: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    const { result } = renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    const sessionId = 'test-session-123';

    // Send a message
    await act(async () => {
      result.current.sendMessage('What is Wisconsin?');
    });

    // Wait for the session creation to be called and resolve it
    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalled();
    });

    // Resolve session creation
    resolveCreateSession({ sessionId });

    // Wait for the send message to be called
    await waitFor(() => {
      expect(mockSendMessage).toHaveBeenCalledWith(sessionId, {
        message: 'What is Wisconsin?',
      });
    });

    // Verify chat state is 'sending' while the API request is pending
    let store = useChatStore.getState();
    expect(store.chatState).toBe('sending');

    // Manually reject the API promise to simulate an error
    const apiError = new Error('Network error: Failed to send message');
    rejectSendMessage(apiError);

    // Wait for the state to reset to idle after the error
    await waitFor(() => {
      store = useChatStore.getState();
      expect(store.chatState).toBe('idle');
    });
  });

  test('should handle WebSocket message processing error gracefully', async () => {
    const options = {
      websocketUrl: 'wss://test-websocket.example.com',
    };

    const createWrapper = (queryClient: QueryClient) => {
      const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
      Wrapper.displayName = 'TestWrapper';
      return Wrapper;
    };

    const { result } = renderHook(() => useWebSocketChat(options), {
      wrapper: createWrapper(queryClient),
    });

    // Verify message handler was captured
    expect(mockMessageHandler).toBeDefined();

    const sessionId = 'test-session-123';
    const queryId = 'test-query-456';

    // First, send a message and resolve it successfully
    await act(async () => {
      result.current.sendMessage('What is Wisconsin?');
    });

    // Wait for the session creation to be called and resolve it
    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalled();
    });

    // Resolve session creation
    resolveCreateSession({ sessionId });

    // Wait for the send message to be called and resolve it
    await waitFor(() => {
      expect(mockSendMessage).toHaveBeenCalled();
    });

    // Resolve the send message
    resolveSendMessage({
      query_id: queryId,
    });

    // Wait for the query to be created
    await waitFor(() => {
      const store = useChatStore.getState();
      expect(store.queries[queryId]).toBeDefined();
    });

    // Simulate a malformed WebSocket message that will cause an error
    const malformedMessage = {
      responseType: 'fragment',
      queryId,
      // Missing 'content' property which will cause an error in processing
    } as MessageUnion;

    // Send the malformed message — error should be handled gracefully
    // (shown as toast, not crash)
    act(() => {
      mockMessageHandler!(malformedMessage);
    });

    // The hook should not crash; chat state remains consistent
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
});
