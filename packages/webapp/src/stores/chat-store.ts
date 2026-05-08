import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  ChatError,
  ChatState,
  ChatStore,
  ConnectionState,
  Query,
  QueryError,
  ResourceItem,
  QueryStatus,
  SessionStatus,
  TraceEvent,
} from './types';

function traceStepKey(event: TraceEvent) {
  const turn = event.metadata?.turn;
  const turnKey =
    typeof turn === 'number' || typeof turn === 'string' ? String(turn) : 'initial';

  if (event.toolName) {
    return `tool:${event.toolName}:${turnKey}`;
  }

  return `event:${event.event.replace(/_(start|complete)$/, '')}`;
}

export const useChatStore = create<ChatStore>()(
  immer((set, get) => ({
    // Initial application state
    sessionStatus: 'idle' as SessionStatus,
    connectionState: 'closed' as ConnectionState,
    chatState: 'idle' as ChatState,
    currentQueryId: null,
    queries: {},
    queryOrder: [],
    errors: [],
    draftMessage: '',
    sessionId: null,
    sessionCache: {},

    // Session management
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

    replaceQueryId: (oldId: string, newId: string) =>
      set(state => {
        const query = state.queries[oldId];
        if (!query) return;
        query.queryId = newId;
        state.queries[newId] = query;
        delete state.queries[oldId];
        const idx = state.queryOrder.indexOf(oldId);
        if (idx !== -1) state.queryOrder[idx] = newId;
        if (state.currentQueryId === oldId) state.currentQueryId = newId;
      }),

    updateQueryResponse: (queryId: string, content: string) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].response.content = content;
        }
      }),

    appendQueryResponse: (queryId: string, fragment: string) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].response.content += fragment;
        }
      }),

    updateQueryResources: (queryId: string, resources: ResourceItem[]) =>
      set(state => {
        if (state.queries[queryId] && resources) {
          state.queries[queryId].resources = [
            ...(state.queries[queryId].resources || []),
            ...resources,
          ];
        }
      }),

    appendQueryTrace: (queryId, event) =>
      set(state => {
        if (state.queries[queryId]) {
          const traceEvents = state.queries[queryId].traceEvents || [];
          const incomingKey = traceStepKey(event);
          let pendingIndex = -1;

          for (let i = traceEvents.length - 1; i >= 0; i -= 1) {
            if (
              traceEvents[i].status === 'pending' &&
              traceStepKey(traceEvents[i]) === incomingKey
            ) {
              pendingIndex = i;
              break;
            }
          }

          if (pendingIndex >= 0) {
            traceEvents[pendingIndex] = event;
          } else {
            traceEvents.push(event);
          }

          state.queries[queryId].traceEvents = traceEvents;
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

    setThinkingDuration: (queryId: string, duration: number) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].thinkingDuration = duration;
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

    stashSession: (sessionId: string) =>
      set(state => {
        if (!sessionId || state.queryOrder.length === 0) return;
        state.sessionCache[sessionId] = {
          queries: state.queries,
          queryOrder: state.queryOrder,
          currentQueryId: state.currentQueryId,
          chatState: state.chatState,
        };
      }),

    restoreSession: (sessionId: string) => {
      const snapshot = get().sessionCache[sessionId];
      if (!snapshot) return false;
      set(state => {
        state.queries = snapshot.queries;
        state.queryOrder = snapshot.queryOrder;
        state.currentQueryId = snapshot.currentQueryId;
        state.chatState = snapshot.chatState;
        state.errors = [];
      });
      return true;
    },

    reset: () =>
      set(state => {
        state.sessionStatus = 'idle';
        state.chatState = 'idle';
        state.currentQueryId = null;
        state.queries = {};
        state.queryOrder = [];
        state.errors = [];
        state.draftMessage = '';
        state.sessionId = null;
      }),

    setSessionId: (sessionId: string | null) =>
      set(state => {
        state.sessionId = sessionId;
      }),
  }))
);
