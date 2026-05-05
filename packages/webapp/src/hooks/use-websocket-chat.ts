/**
 * Hook wrapping useValidatedWebSocket specifying UI behavior around
 * basic session creation and messaging (e.g., how queries are updated
 * on receipt of message fragments from the server).
 */

import { useCallback, useMemo, useEffect, useRef } from 'react';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import type { MessageUnion } from '@messages/websocket-interface';
import type { ConnectionState, Query } from '../stores/types';
import { ChatError } from '@/components/errors/chat-error';
import { useChatError } from '@/components/errors/use-chat-error';

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
            case 'agent-trace':
              appendQueryTrace(message.queryId, {
                event: message.content.event,
                label: message.content.label,
                status: message.content.status,
                toolName: message.content.toolName,
                metadata: message.content.metadata,
              });
              break;

            case 'answer-event':
              const { event } = message;
              if (event === 'start') {
                updateQueryStatus(message.queryId, 'streaming');
                setChatState('streaming');
              } else if (event === 'stop') {
                updateQueryStatus(message.queryId, 'completed');
                setChatState('idle');
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
    console.log('useEffect found sessionId update');
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
