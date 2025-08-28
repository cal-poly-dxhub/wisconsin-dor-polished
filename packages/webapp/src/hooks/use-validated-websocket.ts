import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { WebSocket } from 'partysocket';
import { z } from 'zod';
import {
  MessageUnion,
  MessageHandler,
  WebSocketMessage,
  WebSocketMessageSchema,
} from '@messages/websocket-interface';

export interface UseValidatedWebSocketOptions {
  url: string;
  protocols?: string | string[];
  onValidationError?: (error: z.ZodError, rawMessage: unknown) => void;
  onConnectionError?: (event: Event) => void;
  onConnectionOpen?: (event: Event) => void;
  onConnectionClose?: (event: CloseEvent) => void;
}

export interface UseValidatedWebSocketReturn {
  connectionState: 'connecting' | 'open' | 'closing' | 'closed';
  isConnected: boolean;
  lastMessage: MessageUnion | null;
  sendMessage: (message: unknown) => void;
  close: () => void;
  reconnect: () => void;
  error: string | null;
}

export const useValidatedWebSocket = (
  messageHandler: MessageHandler,
  options: UseValidatedWebSocketOptions
): UseValidatedWebSocketReturn => {
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'open' | 'closing' | 'closed'
  >('connecting');
  const [lastMessage, setLastMessage] = useState<MessageUnion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const messageHandlerRef = useRef<MessageHandler>(messageHandler);

  useEffect(() => {
    messageHandlerRef.current = messageHandler;
  }, [messageHandler]);

  const handlersRef = useRef<{
    onValidationError?: (error: z.ZodError, rawMessage: unknown) => void;
    onConnectionError?: (event: Event) => void;
    onConnectionOpen?: (event: Event) => void;
    onConnectionClose?: (event: CloseEvent) => void;
  }>({
    onValidationError: options.onValidationError,
    onConnectionError: options.onConnectionError,
    onConnectionOpen: options.onConnectionOpen,
    onConnectionClose: options.onConnectionClose,
  });

  useEffect(() => {
    handlersRef.current = {
      onValidationError: options.onValidationError,
      onConnectionError: options.onConnectionError,
      onConnectionOpen: options.onConnectionOpen,
      onConnectionClose: options.onConnectionClose,
    };
  }, [
    options.onValidationError,
    options.onConnectionError,
    options.onConnectionOpen,
    options.onConnectionClose,
  ]);

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
      setError(null);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : 'Unknown validation error';
      setError(`Message validation failed: ${errorMessage}`);

      if (err instanceof z.ZodError && handlersRef.current.onValidationError) {
        handlersRef.current.onValidationError(err, event.data);
      }

      console.error(
        'WebSocket message validation failed.',
        '\n\nMessage recieved:',
        event.data,
        '\n\nError:',
        err
      );
    }
  }, []);

  const handleOpen = useCallback((event: Event) => {
    setConnectionState('open');
    setError(null);
    handlersRef.current.onConnectionOpen?.(event);
  }, []);

  const handleClose = useCallback((event: CloseEvent) => {
    setConnectionState('closed');
    handlersRef.current.onConnectionClose?.(event);
  }, []);

  const handleError = useCallback((event: Event) => {
    setError('WebSocket connection error occurred');
    handlersRef.current.onConnectionError?.(event);
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
    if (!options.url) {
      return null;
    }

    const ws = new WebSocket(options.url, [], wsOptions);
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
    options.url,
    wsOptions,
    handleOpen,
    handleClose,
    handleError,
    handleMessage,
  ]);

  const sendMessage = useCallback(
    (message: unknown) => {
      try {
        ws?.send(JSON.stringify(message));
      } catch (err) {
        console.error('Error sending message:', err);
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to send message';
        setError(`Send failed: ${errorMessage}`);
        throw err;
      }
    },
    [ws]
  );

  const close = useCallback(() => {
    ws?.close();
  }, [ws]);

  const reconnect = useCallback(() => {
    ws?.reconnect();
  }, [ws]);

  return {
    connectionState,
    isConnected: connectionState === 'open',
    lastMessage,
    sendMessage,
    close,
    reconnect,
    error,
  };
};
