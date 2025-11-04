/**
 * Hook providing session creation, message sending, and message receipt
 * capabilities for a WebSocket endpoint.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useChatStore } from '@/stores/chat-store';
import { useSendMessage, useCreateSession } from './api/chat';
import { WebSocket } from 'partysocket';
import {
  MessageUnion,
  MessageHandler,
  WebSocketMessage,
  WebSocketMessageSchema,
} from '@messages/websocket-interface';
import { ChatError } from '@/components/errors/chat-error';
import { useChatError } from '@/components/errors/use-chat-error';

export interface UseValidatedWebSocketOptions {
  urlBase: string;
  protocols?: string | string[];
  onSuccessfulSend: (queryId: string, queryMessage: string) => void;
}

export interface UseValidatedWebSocketReturn {
  connectionState: 'connecting' | 'open' | 'closing' | 'closed';
  isConnected: boolean;
  lastMessage: MessageUnion | null;
  sendMessage: (message: string) => Promise<void>;
  close: () => void;
  reconnect: () => void;
  sessionId: string | null;
}

export const useValidatedWebSocket = (
  messageHandler: MessageHandler,
  options: UseValidatedWebSocketOptions
): UseValidatedWebSocketReturn => {
  const { handleError } = useChatError();
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'open' | 'closing' | 'closed'
  >('connecting');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const websocketUrl = useMemo(() => {
    // TODO: useMemo or useEffect?
    return sessionId ? `${options.urlBase}?sessionId=${sessionId}` : '';
  }, [options.urlBase, sessionId]);

  const [lastMessage, setLastMessage] = useState<MessageUnion | null>(null);

  const messageHandlerRef = useRef<MessageHandler>(messageHandler);
  useEffect(() => {
    messageHandlerRef.current = messageHandler;
  }, [messageHandler]);

  const setChatState = useChatStore(s => s.setChatState);

  const createSessionMutation = useCreateSession({
    onSuccess: data => {
      setSessionId(data.sessionId);
    },
    onError: error => {
      handleError(
        new ChatError(error, {
          recoverable: true,
          userMessage:
            'An error occurred while creating a session. Please try again.',
        })
      );
      setChatState('idle'); // So user can retry
    },
  });

  // Sessions are acquired on mount and checked before message sending.
  const ensureSession = useCallback(async () => {
    if (sessionId) {
      return sessionId;
    } else {
      try {
        const { sessionId } = await createSessionMutation.mutateAsync();
        return sessionId;
      } catch (error) {
        // Error was displayed by session creation mutation.
        console.error('Error creating session', error);
        return null;
      }
    }
  }, [createSessionMutation, sessionId]);

  useEffect(() => {
    ensureSession();
  }, [ensureSession]);

  const sendMessageMutation = useSendMessage({
    onSuccess: (data, variables) => {
      options.onSuccessfulSend(data.queryId, variables.payload.message);
    },
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    onError: (error, variables) => {
      // Set chat state to idle so user can retry
      setChatState('idle');
      handleError(
        new ChatError(error, {
          recoverable: true,
          userMessage:
            'An error occurred while sending a message. Please try again.',
        })
      );
    },
  });

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        // Parse message
        const rawData: unknown = JSON.parse(event.data as string);
        const validatedMessage: WebSocketMessage =
          WebSocketMessageSchema.parse(rawData);
        const messageBody = validatedMessage.body;

        // Set previous message and trigger message callback
        setLastMessage(messageBody);
        messageHandlerRef.current(messageBody);
      } catch (err) {
        handleError(
          new ChatError(
            err instanceof Error ? err : new Error('Unknown error'),
            {
              recoverable: true,
              userMessage:
                'Could not validate message from server. Please try again.',
            }
          )
        );
      }
    },
    [handleError]
  );

  const handleOpen = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    (event: Event) => {
      setConnectionState('open');
    },
    [setConnectionState]
  );

  const handleClose = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    (event: CloseEvent) => {
      setConnectionState('closed');
      setSessionId(null); // Session ID invalid on close
    },
    [setConnectionState, setSessionId]
  );

  const handleConnectionError = useCallback(
    (event: Event) => {
      handleError(
        new ChatError(
          event instanceof Error ? event : new Error('Unknown error'),
          {
            recoverable: true,
            userMessage:
              'An error occurred while connecting to the server. Please try again.',
          }
        )
      );
    },
    [handleError]
  );

  const wsOptions = useMemo(() => {
    return {
      connectionTimeout: 4000,
      maxRetries: 10,
      debug: false,
      debugLogger: (...args: unknown[]) => {
        console.log('PartySocket debug:', ...args);
      },
    };
  }, []);

  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    if (!websocketUrl) {
      wsRef.current?.close();
      return;
    }

    wsRef.current = new WebSocket(websocketUrl, [], wsOptions);
    const ws = wsRef.current;
    ws.addEventListener('open', handleOpen);
    ws.addEventListener('close', handleClose);
    ws.addEventListener('error', handleConnectionError);
    ws.addEventListener('message', handleMessage);

    return () => {
      ws.removeEventListener('open', handleOpen);
      ws.removeEventListener('close', handleClose);
      ws.removeEventListener('error', handleConnectionError);
      ws.removeEventListener('message', handleMessage);
      ws.close();
    };
  }, [
    wsOptions,
    handleOpen,
    handleClose,
    handleConnectionError,
    handleMessage,
    websocketUrl,
  ]);

  // Ensure a session exists, then send a message via mutation
  const sendMessage = useCallback(
    async (message: string) => {
      const sessionId = await ensureSession();
      if (!sessionId) {
        // No-op. Session creation failed and error was handled by
        // session creation mutation.
        console.error('Session creation failed and message was aborted');
        return;
      }

      sendMessageMutation.mutate({
        sessionId,
        payload: { message },
      });
    },
    [sendMessageMutation, ensureSession]
  );

  const close = useCallback(() => {
    wsRef.current?.close();
    setSessionId(null);
  }, [wsRef]);

  const reconnect = useCallback(async () => {
    // Create a new session; the last session was cleared on disconnect.
    const res = await createSessionMutation.mutateAsync();
    setSessionId(res.sessionId);

    // This creates a new WebSocket via the ws = useMemo(() => { ... }).
  }, [setSessionId, createSessionMutation]);

  return {
    connectionState,
    isConnected: connectionState === 'open',
    lastMessage,
    sendMessage,
    close,
    reconnect,
    sessionId,
  };
};
