/**
 * Hook providing session creation, message sending, and message receipt
 * capabilities for a WebSocket endpoint.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useSendMessage, useCreateSession } from './api/chat';
import { WebSocket } from 'partysocket';
import {
  MessageUnion,
  MessageHandler,
  WebSocketMessage,
  WebSocketMessageSchema,
} from '@messages/websocket-interface';

export interface UseValidatedWebSocketOptions {
  urlBase: string;
  protocols?: string | string[];
  onSuccessfulSend: (queryId: string, queryMessage: string) => void;
}

export interface UseValidatedWebSocketReturn {
  connectionState: 'connecting' | 'open' | 'closing' | 'closed';
  isConnected: boolean;
  lastMessage: MessageUnion | null;
  sendMessage: (message: string) => void;
  close: () => void;
  reconnect: () => void;
  sessionId: string | null;
}

export const useValidatedWebSocket = (
  messageHandler: MessageHandler,
  options: UseValidatedWebSocketOptions
): UseValidatedWebSocketReturn => {
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

  const createSessionMutation = useCreateSession({
    onSuccess: data => {
      setSessionId(data.sessionId);
    },
    onError: error => {
      // TODO: raise an error here; propagate upward
      console.error('Error creating session', error);
    },
  });

  const sendMessageMutation = useSendMessage({
    onSuccess: (data, variables) => {
      options.onSuccessfulSend(data.queryId, variables.payload.message);
    },
    onError: (error, variables) => {
      // TODO: raise a proper error here
      console.error('Error sending message', error);
    },
  });
  useEffect(() => {
    // Create a session ID exactly once?
  }, [sessionId]);

  // Build WebSocket with route handlers for message, open, close, error

  const handleMessage = useCallback((event: MessageEvent) => {
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
      // TODO: raise an error here; propagate upward
      console.error(
        'WebSocket message validation failed.',
        '\n\nMessage recieved:',
        event.data,
        '\n\nError:',
        err
      );
    }
  }, []);

  const handleOpen = useCallback(
    (event: Event) => {
      setConnectionState('open');
    },
    [setConnectionState]
  );

  const handleClose = useCallback(
    (event: CloseEvent) => {
      setConnectionState('closed');
      setSessionId(null); // Session ID invalid on close
    },
    [setConnectionState, setSessionId]
  );

  const handleError = useCallback((event: Event) => {
    // TODO: raise an error here; propagate upward
    console.error('Error received over WebSocket:', event);
  }, []);

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

  const ws = useMemo(() => {
    if (!websocketUrl) {
      return null;
    }

    const ws = new WebSocket(websocketUrl, [], wsOptions);
    ws.addEventListener('open', handleOpen);
    ws.addEventListener('close', handleClose);
    ws.addEventListener('error', handleError);
    ws.addEventListener('message', handleMessage);

    const updateConnectionState = () => {
      switch (ws.readyState) {
        case WebSocket.CONNECTING:
          setConnectionState('connecting');
          break;
        case WebSocket.OPEN:
          setConnectionState('open');
          break;
        case WebSocket.CLOSING:
          setConnectionState('closing');
          break;
        case WebSocket.CLOSED:
          setConnectionState('closed');
          break;
      }
    };
    setInterval(updateConnectionState, 500);
    return ws;
  }, [
    wsOptions,
    handleOpen,
    handleClose,
    handleError,
    handleMessage,
    websocketUrl,
  ]);

  // Ensure a session exists, then send a message via mutation
  const sendMessage = useCallback(
    async (message: string) => {
      try {
        let localSessionId: string | null = sessionId; // prevent setSessionId race condition
        if (!localSessionId) {
          const res = await createSessionMutation.mutateAsync();
          setSessionId(res.sessionId);
          localSessionId = res.sessionId;
        }
        await sendMessageMutation.mutateAsync({
          sessionId: localSessionId,
          payload: { message },
        });
      } catch (err) {
        // TODO: raise an error here; propagate upward
        console.error('Error sending message:', err);
      }
    },
    [createSessionMutation, sendMessageMutation, sessionId]
  );

  const close = useCallback(() => {
    ws?.close();
    setSessionId(null);
  }, [ws]);

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
