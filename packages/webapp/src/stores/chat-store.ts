import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  ChatError,
  ChatState,
  ChatStore,
  ConnectionState,
  Query,
  QueryError,
  QueryResources,
  QueryStatus,
  SessionStatus,
} from './types';

export const useChatStore = create<ChatStore>()(
  immer((set, get) => ({
    // Initial application state
    sessionId: null,
    sessionStatus: 'idle' as SessionStatus,
    connectionState: 'closed' as ConnectionState,
    chatState: 'idle' as ChatState,
    currentQueryId: null,
    queries: {},
    queryOrder: [],
    errors: [],
    draftMessage: '',

    // Session management
    setSessionId: (sessionId: string) =>
      set(state => {
        state.sessionId = sessionId;
      }),

    setSessionStatus: (sessionStatus: SessionStatus) =>
      set(state => {
        state.sessionStatus = sessionStatus;
      }),

    setConnectionState: (connectionState: ConnectionState) =>
      set(state => {
        state.connectionState = connectionState;
      }),

    setChatState: (chatState: ChatState) =>
      set(state => {
        state.chatState = chatState;
      }),

    setCurrentQueryId: (currentQueryId: string | null) =>
      set(state => {
        state.currentQueryId = currentQueryId;
      }),

    // Query management
    addQuery: (query: Query) =>
      set(state => {
        state.queries[query.queryId] = query;
        state.queryOrder.push(query.queryId);
      }),

    updateQueryResponse: (queryId: string, content: string) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].response.content = content;
        }
      }),

    updateQueryResources: (queryId: string, resources: QueryResources) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].resources = resources;
        }
      }),

    getQuery: (queryId: string) => get().queries[queryId],

    updateQueryStatus: (queryId: string, status: QueryStatus) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].status = status;
        }
      }),

    setQueryError: (queryId: string, error: QueryError) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].error = error;
        }
      }),

    clearQueryError: (queryId: string) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].error = undefined;
        }
      }),

    incrementQueryRetry: (queryId: string) =>
      set(state => {
        if (state.queries[queryId]) {
          const current = state.queries[queryId].retryCount || 0;
          state.queries[queryId].retryCount = current + 1;
        }
      }),

    // Error and UI management
    addError: (error: ChatError) =>
      set(state => {
        state.errors.push(error);
      }),

    removeError: (errorId: string) =>
      set(state => {
        state.errors = state.errors.filter((e: ChatError) => e.id !== errorId);
      }),

    clearErrors: () =>
      set(state => {
        state.errors = [];
      }),

    setDraftMessage: (draftMessage: string) =>
      set(state => {
        state.draftMessage = draftMessage;
      }),

    clearHistory: () =>
      set(state => {
        state.queries = {};
        state.queryOrder = [];
        state.errors = [];
      }),

    reset: () =>
      set(state => {
        state.sessionId = null;
        state.sessionStatus = 'idle';
        state.chatState = 'idle';
        state.currentQueryId = null;
        state.queries = {};
        state.queryOrder = [];
        state.errors = [];
        state.draftMessage = '';
      }),
  }))
);
