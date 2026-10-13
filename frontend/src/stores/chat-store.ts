import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  AgentTraceEvent,
  ChatState,
  ChatStore,
  ConnectionState,
  Query,
  QueryClarification,
  QueryError,
  ResourceItem,
  QueryStatus,
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
    connectionState: 'closed' as ConnectionState,
    chatState: 'idle' as ChatState,
    currentQueryId: null,
    queries: {},
    queryOrder: [],
    sessionId: null,
    sessionCache: {},
    switchingSession: false,

    // Session management
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

    updateQueryStatus: (queryId: string, status: QueryStatus) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].status = status;
        }
      }),

    setQueryChoices: (
      queryId: string,
      choices: string[],
      clarification?: QueryClarification
    ) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].choices = choices;
          // Keep the question/axis next to the options so the turn can be
          // rendered as one clarification block.
          state.queries[queryId].clarification = clarification;
        }
      }),

    setQueryFlowchart: (queryId, flowchart) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].flowchart = flowchart;
        }
      }),

    setQueryError: (queryId: string, error: QueryError) =>
      set(state => {
        if (state.queries[queryId]) {
          state.queries[queryId].error = error;
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

    clearHistory: () =>
      set(state => {
        state.queries = {};
        state.queryOrder = [];
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
      });
      return true;
    },

    reset: () =>
      set(state => {
        state.chatState = 'idle';
        state.currentQueryId = null;
        state.queries = {};
        state.queryOrder = [];
        state.sessionId = null;
      }),

    setSessionId: (sessionId: string | null) =>
      set(state => {
        state.sessionId = sessionId;
      }),
  }))
);
