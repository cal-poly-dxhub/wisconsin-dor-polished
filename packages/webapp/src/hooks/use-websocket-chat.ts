/**
 * Hook wrapping useValidatedWebSocket specifying UI behavior around
 * basic session creation and messaging (e.g., how queries are updated
 * on receipt of message fragments from the server).
 */

import { useCallback, useMemo, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import type { MessageUnion, AgentEventKind } from '@messages/websocket-interface';
import type { ConnectionState, Query, TraceEvent } from '../stores/types';
import { ChatError } from '@/components/errors/chat-error';
import { useChatError } from '@/components/errors/use-chat-error';

const TOOL_LABELS: Record<string, { pending: string; complete: string }> = {
  faq_search: { pending: 'Checking FAQs', complete: 'Checked FAQs' },
  refine_query: {
    pending: 'Reviewing conversation context',
    complete: 'Refined the search question',
  },
  vector_search: {
    pending: 'Searching the knowledge graph',
    complete: 'Searched the knowledge graph',
  },
  get_neighbors: {
    pending: 'Exploring related authorities',
    complete: 'Explored related authorities',
  },
  get_document: {
    pending: 'Fetching document details',
    complete: 'Fetched document details',
  },
  get_authority_chain: {
    pending: 'Tracing authority chain',
    complete: 'Traced authority chain',
  },
  list_framework_docs: {
    pending: 'Listing framework documents',
    complete: 'Listed framework documents',
  },
  fetch_case_opinion: {
    pending: 'Fetching court opinion',
    complete: 'Fetched court opinion',
  },
  answer: { pending: 'Preparing cited answer', complete: 'Prepared cited answer' },
};

// Phase events that are too noisy / redundant to show in the UI.
// These are emitted by the backend for logging but don't add value to the
// user-facing thinking train of thought.
const SUPPRESSED_PHASE_EVENTS = new Set([
  'agent_turn_start',
  'agent_turn_model_response',
  'agentic_retrieval_request_received',
  'agentic_retrieval_response_ready',
  'chat_history_loaded',
  'agent_turn_budget_warning_injected',
]);

function agentEventToTrace(
  kind: AgentEventKind,
  toolName: string | null,
  label: string,
  payload: Record<string, unknown>
): TraceEvent | null {
  switch (kind) {
    case 'loop_start':
      return null;
    case 'tool_call': {
      const labels = toolName ? TOOL_LABELS[toolName] : undefined;
      return {
        event: `${toolName ?? 'unknown'}_start`,
        label: labels?.pending ?? (label || `Running ${toolName ?? 'tool'}`),
        status: 'pending',
        toolName,
        metadata: payload,
      };
    }
    case 'tool_result': {
      const labels = toolName ? TOOL_LABELS[toolName] : undefined;
      return {
        event: `${toolName ?? 'unknown'}_complete`,
        label: labels?.complete ?? (label || `Completed ${toolName ?? 'tool'}`),
        status: (payload.status as string) === 'error' ? 'error' : 'complete',
        toolName,
        metadata: payload,
      };
    }
    case 'loop_complete':
      return {
        event: 'loop_complete',
        label: label || 'Retrieval complete',
        status: 'complete',
        metadata: {
          elapsed_ms: payload.elapsedMs,
          cited_doc_count: payload.citedDocCount,
          rag_document_count: payload.ragDocumentCount,
        },
      };
    case 'reasoning':
      return {
        event: 'reasoning',
        label: (payload.text as string) || 'Thinking...',
        status: 'complete',
      };
    case 'phase': {
      const devPayload = payload.devPayload as Record<string, unknown> | undefined;
      const backendEvent = (devPayload?.event as string) ?? '';
      if (SUPPRESSED_PHASE_EVENTS.has(backendEvent)) {
        return null;
      }
      return {
        event: 'phase',
        label: label || 'Processing',
        status: 'complete',
        metadata: payload,
      };
    }
  }
}

export interface UseWebSocketChatOptions {
  websocketUrl: string;
  connectionTimeout?: number;
  onConnectionStateChange?: (state: ConnectionState) => void;
  onError?: (error: string) => void;
}

export interface UseWebSocketChatReturn {
  connectionState: ConnectionState;
  isConnected: boolean;
  disconnect: () => void;
  reconnect: () => void;
  sendMessage: (message: string) => Promise<void>;
}

export function useWebSocketChat(
  options: UseWebSocketChatOptions
): UseWebSocketChatReturn {
  const { handleError } = useChatError();
  const queryClient = useQueryClient();
  const setConnectionState = useChatStore(state => state.setConnectionState);
  const updateQueryStatus = useChatStore(state => state.updateQueryStatus);
  const appendQueryResponse = useChatStore(state => state.appendQueryResponse);
  const updateQueryResources = useChatStore(
    state => state.updateQueryResources
  );
  const appendQueryTrace = useChatStore(state => state.appendQueryTrace);
  const setQueryError = useChatStore(state => state.setQueryError);
  const setChatState = useChatStore(state => state.setChatState);
  const addQuery = useChatStore(state => state.addQuery);
  const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);
  const setSessionId = useChatStore(state => state.setSessionId);
  const replaceQueryId = useChatStore(state => state.replaceQueryId);

  // Track the pending optimistic ID so handleSuccessfulSend can replace it
  const pendingQueryIdRef = useRef<string | null>(null);

  // Define UI actions for each message type
  const messageHandler = useCallback(
    (message: MessageUnion) => {
      try {
        if ('responseType' in message) {
          // Ignore messages for queries that aren't in the current session's store.
          // However, if the real queryId isn't in the store yet (because the HTTP
          // response hasn't returned to trigger replaceQueryId), eagerly perform
          // the replacement so the WebSocket message isn't dropped.
          const queryId = 'queryId' in message ? message.queryId : null;
          if (queryId && !useChatStore.getState().queries[queryId]) {
            const pendingId = pendingQueryIdRef.current;
            if (pendingId && useChatStore.getState().queries[pendingId]) {
              replaceQueryId(pendingId, queryId);
              pendingQueryIdRef.current = null;
            } else {
              return;
            }
          }

          switch (message.responseType) {
            case 'documents':
              updateQueryResources(
                message.queryId,
                message.content.documents.map(document => ({
                  type: 'document',
                  data: document,
                }))
              );
              break;

            case 'faq':
              updateQueryResources(
                message.queryId,
                message.content.faqs.map(faq => ({
                  type: 'faq',
                  data: faq,
                }))
              );
              break;

            case 'fragment':
              appendQueryResponse(message.queryId, message.content.fragment);
              break;

            case 'trace':
              appendQueryTrace(message.queryId, {
                event: message.content.event,
                label: message.content.label,
                status: message.content.status,
                toolName: message.content.toolName,
                metadata: message.content.metadata,
              });
              break;

            case 'agent-event': {
              const payload = message.payload ?? {};
              const toolName = (payload.toolName as string) ?? (payload.tool_name as string) ?? null;
              const label = (payload.label as string) ?? '';

              const traceEvent = agentEventToTrace(
                message.kind,
                toolName,
                label,
                { ...payload, turn: message.turn, devPayload: message.devPayload }
              );
              if (traceEvent) {
                appendQueryTrace(message.queryId, traceEvent);
              }
              break;
            }

            case 'answer-event':
              const { event } = message;
              if (event === 'start') {
                updateQueryStatus(message.queryId, 'streaming');
                setChatState('streaming');
              } else if (event === 'stop') {
                updateQueryStatus(message.queryId, 'completed');
                setChatState('idle');
                queryClient.invalidateQueries({ queryKey: ['chat', 'sessions'] });
              }
              break;

            case 'error':
              // Error messages sent back from the server are already user-facing.
              setQueryError(message.queryId || '', {
                message: message.content.error,
                userMessage: message.content.error,
                retryable: true,
              });
              if (message.queryId) {
                updateQueryStatus(message.queryId, 'failed');
              }
              break;
          }
        }
      } catch (error) {
        handleError(
          new ChatError(
            error instanceof Error ? error : new Error('Unknown error'),
            {
              userMessage:
                'An error occurred while processing a response from the server.',
              recoverable: true,
            }
          )
        );
      }
    },
    [
      updateQueryResources,
      updateQueryStatus,
      appendQueryResponse,
      appendQueryTrace,
      setChatState,
      setQueryError,
      handleError,
      queryClient,
      replaceQueryId,
    ]
  );

  // Replace optimistic query ID with the real one from the server
  const handleSuccessfulSend = useCallback(
    (queryId: string, _message: string) => {
      const pendingId = pendingQueryIdRef.current;
      if (pendingId) {
        replaceQueryId(pendingId, queryId);
        pendingQueryIdRef.current = null;
      }
    },
    [replaceQueryId]
  );

  // Create memoized WebSocket options
  const websocketOptions = useMemo(() => {
    return {
      urlBase: options.websocketUrl,
      onSuccessfulSend: handleSuccessfulSend,
    };
  }, [handleSuccessfulSend, options.websocketUrl]);

  // Defaults to no-ops if WebSocket URL is falsy
  const {
    connectionState,
    isConnected,
    close,
    reconnect,
    sendMessage,
    sessionId,
  } = useValidatedWebSocket(messageHandler, websocketOptions);

  // Keep store connection state updated
  useEffect(() => {
    setConnectionState(connectionState);
  }, [connectionState, setConnectionState]);

  // Keep store session ID updated
  useEffect(() => {
    setSessionId(sessionId);
  }, [sessionId, setSessionId]);

  // Wrap sendMessage to optimistically add the query to the store immediately
  const optimisticSendMessage = useCallback(
    async (message: string) => {
      const optimisticId = `pending-${Date.now()}`;
      pendingQueryIdRef.current = optimisticId;

      const query: Query = {
        query: message,
        queryId: optimisticId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'pending',
        response: {
          type: 'stream',
          content: '',
        },
      };
      addQuery(query);
      setCurrentQueryId(optimisticId);

      await sendMessage(message);
    },
    [sendMessage, addQuery, setCurrentQueryId]
  );

  return {
    connectionState,
    isConnected,
    disconnect: close,
    reconnect,
    sendMessage: optimisticSendMessage,
  };
}
