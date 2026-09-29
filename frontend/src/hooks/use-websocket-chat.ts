/**
 * Hook wrapping useValidatedWebSocket specifying UI behavior around
 * basic session creation and messaging (e.g., how queries are updated
 * on receipt of message fragments from the server).
 */

import { useCallback, useMemo, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import type { MessageUnion } from '@messages/websocket-interface';
import type { ConnectionState, Query } from '../stores/types';
import { ChatError } from '@/components/errors/chat-error';
import { useChatError } from '@/components/errors/use-chat-error';
import { getSessionHistory } from '@/api/chat-api';
import { inFlightQueryIds, isInFlight, recoverAnswer } from '@/lib/stream-recovery';

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
  sendMessage: (message: string, suppressTopicShift?: boolean) => Promise<void>;
}

export function useWebSocketChat(
  options: UseWebSocketChatOptions
): UseWebSocketChatReturn {
  const { handleError } = useChatError();
  const queryClient = useQueryClient();
  const setConnectionState = useChatStore(state => state.setConnectionState);
  const updateQueryStatus = useChatStore(state => state.updateQueryStatus);
  const appendQueryResponse = useChatStore(state => state.appendQueryResponse);
  const updateQueryResponse = useChatStore(state => state.updateQueryResponse);
  const updateQueryResources = useChatStore(
    state => state.updateQueryResources
  );
  const setQueryFlowchart = useChatStore(state => state.setQueryFlowchart);
  const setQueryError = useChatStore(state => state.setQueryError);
  const setChatState = useChatStore(state => state.setChatState);
  const addQuery = useChatStore(state => state.addQuery);
  const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);
  const setSessionId = useChatStore(state => state.setSessionId);
  const replaceQueryId = useChatStore(state => state.replaceQueryId);
  const appendAgentTraceEvent = useChatStore(
    state => state.appendAgentTraceEvent
  );

  // Track the pending optimistic ID so handleSuccessfulSend can replace it
  const pendingQueryIdRef = useRef<string | null>(null);

  // Track streaming state for timing logs
  const streamStartRef = useRef(0);
  const fragmentCountRef = useRef(0);

  // rAF-based fragment buffer: accumulate tokens and flush once per frame
  const fragmentBufferRef = useRef<{ queryId: string; text: string }>({ queryId: '', text: '' });
  const rafIdRef = useRef<number>(0);

  const flushFragments = useCallback(() => {
    rafIdRef.current = 0;
    const { queryId, text } = fragmentBufferRef.current;
    if (text && queryId) {
      appendQueryResponse(queryId, text);
      fragmentBufferRef.current = { queryId: '', text: '' };
    }
  }, [appendQueryResponse]);

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
              console.log(`[WS Timing] documents received | count=${message.content.documents.length}`);
              updateQueryResources(
                message.queryId,
                message.content.documents.map(document => ({
                  type: 'document',
                  data: document,
                }))
              );
              break;

            case 'faq':
              console.log(`[WS Timing] faqs received | count=${message.content.faqs.length}`);
              updateQueryResources(
                message.queryId,
                message.content.faqs.map(faq => ({
                  type: 'faq',
                  data: faq,
                }))
              );
              break;

            case 'flowchart':
              console.log(
                `[WS Timing] flowchart received | id=${message.content.flowchartId}`
              );
              setQueryFlowchart(message.queryId, message.content);
              break;

            case 'fragment':
              fragmentCountRef.current++;
              // Buffer fragments and flush once per animation frame
              fragmentBufferRef.current.queryId = message.queryId;
              fragmentBufferRef.current.text += message.content.fragment;
              if (!rafIdRef.current) {
                rafIdRef.current = requestAnimationFrame(flushFragments);
              }
              break;

            case 'answer-event':
              if (message.event === 'start') {
                streamStartRef.current = performance.now();
                fragmentCountRef.current = 0;
                updateQueryStatus(message.queryId, 'streaming');
                setChatState('streaming');
              } else if (message.event === 'stop') {
                // Flush any buffered fragments before finalizing
                if (rafIdRef.current) {
                  cancelAnimationFrame(rafIdRef.current);
                  rafIdRef.current = 0;
                }
                const { queryId: bufQid, text: bufText } = fragmentBufferRef.current;
                if (bufText && bufQid) {
                  appendQueryResponse(bufQid, bufText);
                  fragmentBufferRef.current = { queryId: '', text: '' };
                }
                const streamDuration = performance.now() - streamStartRef.current;
                console.log(
                  `[WS Timing] STREAM STOP | duration=${streamDuration.toFixed(1)}ms | fragments=${fragmentCountRef.current} | queryId=${message.queryId}`
                );
                // The stop event carries the complete persisted answer. If the
                // fragments we assembled differ (one was dropped or rejected, or
                // the server regenerated after a failed stream), take the
                // server's text so the answer on screen is never truncated.
                if (message.answer) {
                  const shown = useChatStore.getState().queries[message.queryId]?.response.content ?? '';
                  if (shown !== message.answer) {
                    console.warn(
                      `[WS] Answer reconciled from stop event | shown=${shown.length} chars, final=${message.answer.length} chars`
                    );
                    updateQueryResponse(message.queryId, message.answer);
                  }
                }
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

            case 'agent-event':
              appendAgentTraceEvent(message.queryId, {
                kind: message.kind,
                turn: message.turn,
                seq: message.seq,
                timestamp: message.timestamp,
                payload: message.payload,
                devPayload: message.devPayload,
              });
              break;

            case 'choices':
              useChatStore.getState().setQueryChoices(
                message.queryId,
                message.content.choices,
                {
                  question: message.content.question,
                  axis: message.content.axis,
                  kind: message.content.kind,
                }
              );
              break;

            case 'suggestion':
              useChatStore.getState().setQuerySuggestion(
                message.queryId,
                message.content.kind
              );
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
      setQueryFlowchart,
      updateQueryStatus,
      appendQueryResponse,
      setChatState,
      setQueryError,
      appendAgentTraceEvent,
      handleError,
      queryClient,
      replaceQueryId,
      flushFragments,
      updateQueryResponse,
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

  // When the socket drops while an answer is on its way, the rest of the
  // stream is lost, but the Lambda keeps going and persists the full answer.
  // Poll the session history for it and swap it in (Task 62).
  const recoveringRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (connectionState !== 'closed') return;
    const store = useChatStore.getState();
    const historySessionId = store.sessionId;
    if (!historySessionId) return;

    for (const queryId of inFlightQueryIds(store.queries)) {
      if (recoveringRef.current.has(queryId)) continue;
      recoveringRef.current.add(queryId);
      console.warn(`[WS] Connection lost mid-answer; recovering ${queryId} from history`);
      recoverAnswer({
        queryId,
        fetchHistory: () => getSessionHistory(historySessionId),
        stillWaiting: () => isInFlight(useChatStore.getState().queries[queryId]),
      }).then(answer => {
        recoveringRef.current.delete(queryId);
        if (!isInFlight(useChatStore.getState().queries[queryId])) return;
        if (answer) {
          updateQueryResponse(queryId, answer);
          updateQueryStatus(queryId, 'completed');
        } else {
          const text = 'The connection dropped before this answer finished. Please ask again.';
          setQueryError(queryId, { message: text, userMessage: text, retryable: true });
          updateQueryStatus(queryId, 'failed');
        }
        setChatState('idle');
        queryClient.invalidateQueries({ queryKey: ['chat', 'sessions'] });
      });
    }
  }, [connectionState, updateQueryResponse, updateQueryStatus, setQueryError, setChatState, queryClient]);

  // Track send time for roundtrip measurement
  const sendTimestampRef = useRef(0);

  // Wrap sendMessage to optimistically add the query to the store immediately
  const optimisticSendMessage = useCallback(
    async (message: string, suppressTopicShift?: boolean) => {
      sendTimestampRef.current = performance.now();
      console.log(`[WS Timing] SEND | message="${message.slice(0, 60)}${message.length > 60 ? '...' : ''}"`);
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

      await sendMessage(message, suppressTopicShift);
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
