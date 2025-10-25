/**
 * Hook wrapping useValidatedWebSocket specifying UI behavior around
 * basic session creation and messaging (e.g., how queries are updated
 * on receipt of message fragments from the server).
 */

import { useCallback, useMemo, useEffect } from 'react';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import type { MessageUnion } from '@messages/websocket-interface';
import type { ConnectionState, Query } from '../stores/types';

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
  sendMessage: (message: string) => void;
}

export function useWebSocketChat(
  options: UseWebSocketChatOptions
): UseWebSocketChatReturn {
  const setConnectionState = useChatStore(state => state.setConnectionState);
  const updateQueryStatus = useChatStore(state => state.updateQueryStatus);
  const appendQueryResponse = useChatStore(state => state.appendQueryResponse);
  const updateQueryResources = useChatStore(
    state => state.updateQueryResources
  );
  const setQueryError = useChatStore(state => state.setQueryError);
  const setChatState = useChatStore(state => state.setChatState);
  const addQuery = useChatStore(state => state.addQuery);
  const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);

  // Define UI actions for each message type
  const messageHandler = useCallback(
    (message: MessageUnion) => {
      try {
        if ('responseType' in message) {
          switch (message.responseType) {
            case 'documents':
              updateQueryResources(message.queryId, {
                type: 'documents',
                content: message.content,
              });
              break;

            case 'faq':
              updateQueryResources(message.queryId, {
                type: 'faq',
                content: message.content,
              });
              break;

            case 'fragment':
              appendQueryResponse(message.queryId, message.content.fragment);
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
              setQueryError(message.queryId, {
                message: message.content.message,
                userMessage: 'An error occurred while processing your request',
                retryable: true,
              });
              updateQueryStatus(message.queryId, 'failed');
              break;
          }
        }
      } catch (error) {
        // TODO: handle error raised using boundary method
        console.error('Error processing WebSocket message:', error);
      }
    },
    [
      updateQueryResources,
      setQueryError,
      updateQueryStatus,
      appendQueryResponse,
      setChatState,
    ]
  );

  // Store new query; set current query ID in UI store
  const handleSuccessfulSend = useCallback(
    (queryId: string, message: string) => {
      const query: Query = {
        query: message,
        queryId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'sent',
        response: {
          type: 'stream',
          content: '',
        },
      };
      addQuery(query);
      setCurrentQueryId(queryId);
    },
    [addQuery, setCurrentQueryId]
  );

  // Create memoized WebSocket options
  const websocketOptions = useMemo(() => {
    return {
      urlBase: options.websocketUrl,
      onSuccessfulSend: handleSuccessfulSend,
    };
  }, [handleSuccessfulSend, options.websocketUrl]);

  // Defaults to no-ops if WebSocket URL is falsy
  const { connectionState, isConnected, close, reconnect, sendMessage } =
    useValidatedWebSocket(messageHandler, websocketOptions);

  // Keep store connection state updated
  useEffect(() => {
    setConnectionState(connectionState);
  }, [connectionState, setConnectionState]);

  return {
    connectionState,
    isConnected,
    disconnect: close,
    reconnect,
    sendMessage,
  };
}
