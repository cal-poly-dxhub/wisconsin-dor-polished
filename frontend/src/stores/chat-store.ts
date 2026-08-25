import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  AgentTraceEvent,
  ChatError,
  ChatState,
  ChatStore,
  ConnectionState,
  Query,
  QueryError,
  ResourceItem,
  QueryStatus,
  SessionStatus,
  SuggestionKind,
} from './types';

function traceStepKey(event: AgentTraceEvent): string {
  const turn = event.turn ?? 'initial';
  const toolName = event.payload.toolName;
  if (typeof toolName === 'string' && toolName) {
    return `tool:${toolName}:${turn}`;
  }
  return `kind:${event.kind}:${turn}`;
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
    switchingSession: false,
    suppressTopicShiftOnNextSend: false,

    // Session management
    setSessionStatus: (sessionStatus: SessionStatus) =>
      set(state => {
        state.sessionStatus = sessionStatus;
      }),

    setSwitchingSession: (switching: boolean) =>
      set(state => {
        state.switchingSession = switching;
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

    getQuery: (queryId: string) => get().queries[queryId],

    updateQueryStatus: (queryId: string, status: QueryStatus) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].status = status;
        }
      }),

    setQueryChoices: (queryId: string, choices: string[]) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].choices = choices;
        }
      }),

    setQuerySuggestion: (queryId: string, suggestion: SuggestionKind) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].suggestion = suggestion;
        }
      }),

    clearQuerySuggestion: (queryId: string) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].suggestion = undefined;
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

    appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) =>
      set(state => {
        const query = state.queries[queryId];
        if (!query) return;
        if (!query.agentTrace) query.agentTrace = [];
        if (query.agentTrace.some(e => e.seq === event.seq)) return;

        if (event.kind === 'tool_result') {
          const key = traceStepKey(event);
          for (let i = query.agentTrace.length - 1; i >= 0; i -= 1) {
            if (traceStepKey(query.agentTrace[i]) !== key) continue;
            if (query.agentTrace[i].payload.status === 'pending') {
              query.agentTrace[i] = event;
            }
            return;
          }
        }

        query.agentTrace.push(event);
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

    setSuppressTopicShiftOnNextSend: (value: boolean) =>
      set(state => {
        state.suppressTopicShiftOnNextSend = value;
      }),

    clearHistory: () =>
      set(state => {
        state.queries = {};
        state.queryOrder = [];
        state.errors = [];
        // Leaving the conversation drops any pending one-shot suppression.
        state.suppressTopicShiftOnNextSend = false;
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
        state.suppressTopicShiftOnNextSend = false;
      }),

    setSessionId: (sessionId: string | null) =>
      set(state => {
        state.sessionId = sessionId;
      }),
  }))
);
