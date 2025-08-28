import { useCallback, useMemo, useState } from 'react';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import { useSendMessage, useCreateSession } from './api/chat';
import type { MessageUnion } from '@messages/websocket-interface';
import type { ConnectionState, Query } from '../stores/types';
import type { SendMessageRequest } from '../api/chat-api';

export interface UseWebSocketChatOptions {
  websocketUrl: string;
  connectionTimeout?: number;
  onConnectionStateChange?: (state: ConnectionState) => void;
  onError?: (error: string) => void;
}

export interface SendMessageParams {
  message: string;
}

export interface UseWebSocketChatReturn {
  connectionState: ConnectionState;
  isConnected: boolean;
  disconnect: () => void;
  reconnect: () => void;
  error: string | null;
  sendMessage: (params: SendMessageParams) => void;
  websocketUrl: string;
  sessionId: string | null;
}

export function useWebSocketChat(
  options: UseWebSocketChatOptions
): UseWebSocketChatReturn {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const websocketUrl = useMemo(() => {
    return sessionId ? `${options.websocketUrl}?sessionId=${sessionId}` : '';
  }, [options.websocketUrl, sessionId]);
  const setConnectionState = useChatStore(state => state.setConnectionState);
  const updateQueryStatus = useChatStore(state => state.updateQueryStatus);
  const appendQueryResponse = useChatStore(state => state.appendQueryResponse);
  const updateQueryResources = useChatStore(
    state => state.updateQueryResources
  );
  const setQueryError = useChatStore(state => state.setQueryError);
  const setChatState = useChatStore(state => state.setChatState);
  const addError = useChatStore(state => state.addError);
  const addQuery = useChatStore(state => state.addQuery);
  const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);

  const createSessionMutation = useCreateSession({
    onSuccess: data => {
      setSessionId(data.sessionId);
    },
    onError: error => {
      console.error('Error creating session', error);
      const errorMessage =
        error instanceof Error ? error.message : 'Failed to create session';

      addError({
        id: crypto.randomUUID(),
        type: 'api',
        message: errorMessage,
        userMessage: 'Failed to create session',
        retryable: true,
        timestamp: new Date(),
      });

      options.onError?.(errorMessage);
    },
  });

  const ensureSession = useCallback(async () => {
    const sessionId = await createSessionMutation.mutateAsync();
    return sessionId;
  }, [createSessionMutation]);

  const sendMessageMutation = useSendMessage({
    onSuccess: (data, variables) => {
      const newQuery: Query = {
        query: variables.payload.message,
        queryId: data.query_id,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'sent',
        response: {
          type: 'stream',
          content: '',
        },
      };

      addQuery(newQuery);
      setCurrentQueryId(data.query_id);
      setChatState('waiting_for_response');
    },
    onError: (error, variables) => {
      console.error('Error sending message', error);

      const errorMessage =
        error instanceof Error ? error.message : 'Failed to send message';

      addError({
        id: crypto.randomUUID(),
        type: 'api',
        message: errorMessage,
        userMessage: 'Failed to send message',
        retryable: true,
        timestamp: new Date(),
      });

      setChatState('idle');
      options.onError?.(errorMessage);
    },
  });

  const messageHandler = useCallback(
    (message: MessageUnion) => {
      try {
        if ('responseType' in message) {
          const { queryId } = message;

          switch (message.responseType) {
            case 'documents':
              updateQueryResources(queryId, {
                type: 'documents',
                content: message.content,
              });
              break;

            case 'faq':
              updateQueryResources(queryId, {
                type: 'faq',
                content: message.content,
              });
              break;

            case 'fragment':
              appendQueryResponse(queryId, message.content.fragment);
              break;

            case 'answer-event':
              const { event } = message;
              if (event === 'start') {
                updateQueryStatus(queryId, 'streaming');
                setChatState('streaming');
              } else if (event === 'stop') {
                updateQueryStatus(queryId, 'completed');
                setChatState('idle');
              }
              break;

            case 'error':
              setQueryError(queryId, {
                message: message.content.message,
                userMessage: 'An error occurred while processing your request',
                retryable: true,
              });
              updateQueryStatus(queryId, 'failed');
              break;
          }
        }
      } catch (error) {
        addError({
          id: crypto.randomUUID(),
          type: 'websocket',
          message:
            error instanceof Error
              ? error.message
              : 'Unknown message processing error',
          userMessage: 'Failed to process incoming message',
          retryable: false,
          timestamp: new Date(),
        });
      }
    },
    [
      updateQueryResources,
      setQueryError,
      updateQueryStatus,
      appendQueryResponse,
      setChatState,
      addError,
    ]
  );

  const handleConnectionStateChange = useCallback(
    (state: ConnectionState) => {
      setConnectionState(state);
      options.onConnectionStateChange?.(state);
    },
    [setConnectionState, options]
  );

  const handleError = useCallback(
    (event: Event) => {
      const errorMessage = 'WebSocket connection error occurred.';
      addError({
        id: crypto.randomUUID(),
        type: 'websocket',
        message: errorMessage,
        userMessage: 'Connection error occurred',
        retryable: true,
        timestamp: new Date(),
      });
      options.onError?.(errorMessage);
    },
    [addError, options]
  );

  const websocketOptions = useMemo(() => {
    return {
      url: websocketUrl,
      onConnectionOpen: handleConnectionStateChange.bind(null, 'open'),
      onConnectionClose: handleConnectionStateChange.bind(null, 'closed'),
      onConnectionError: handleError,
    };
  }, [handleConnectionStateChange, handleError, websocketUrl]);

  // Defaults to no-ops if WebSocket URL is falsy
  const { connectionState, isConnected, close, reconnect, error } =
    useValidatedWebSocket(messageHandler, websocketOptions);

  const sendMessage = useCallback(
    async (params: SendMessageParams) => {
      const { sessionId } = await ensureSession();
      if (!sessionId) {
        console.error('No session ID available for sending message; skipping.');
        return;
      }

      setChatState('sending');
      const request: SendMessageRequest = {
        message: params.message,
      };
      sendMessageMutation.mutate({
        sessionId,
        payload: request,
      });
    },
    [sendMessageMutation, setChatState, ensureSession]
  );

  return {
    connectionState,
    isConnected: isConnected && !!sessionId,
    disconnect: close,
    reconnect,
    error,
    sendMessage,
    websocketUrl,
    sessionId,
  };
}
