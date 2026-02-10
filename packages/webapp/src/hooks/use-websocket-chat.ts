/**
 * Hook wrapping useValidatedWebSocket specifying UI behavior around
 * basic session creation and messaging (e.g., how queries are updated
 * on receipt of message fragments from the server).
 */

import { useCallback, useMemo, useEffect } from 'react';
import { useValidatedWebSocket } from './use-validated-websocket';
import { useChatStore } from '../stores/chat-store';
import type { MessageUnion, SourceDocument } from '@messages/websocket-interface';
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
  const setQueryError = useChatStore(state => state.setQueryError);
  const setChatState = useChatStore(state => state.setChatState);
  const addQuery = useChatStore(state => state.addQuery);
  const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);
  const setSessionId = useChatStore(state => state.setSessionId);

  /**
   * Parse FAQ content from document format back to FAQ format.
   *
   * Expected content format:
   * Q: {question}
   * A: {answer}
   *
   * @param document - Document with sourceId="faqs"
   * @returns FAQ object or null if parsing fails
   */
  const parseFAQFromDocument = useCallback((document: SourceDocument) => {
    try {
      // Parse the content to extract question and answer
      const content = document.content || '';
      const lines = content.split('\n');

      let question = '';
      const answerLines: string[] = [];
      let foundAnswer = false;

      for (const line of lines) {
        if (line.startsWith('Q:')) {
          question = line.substring(2).trim();
        } else if (line.startsWith('A:')) {
          foundAnswer = true;
          answerLines.push(line.substring(2).trim());
        } else if (foundAnswer) {
          answerLines.push(line);
        }
      }

      const answer = answerLines.join('\n').trim();

      if (!question || !answer) {
        console.warn('Failed to parse FAQ from document:', document);
        return null;
      }

      return {
        faqId: document.documentId,
        question,
        answer,
      };
    } catch (error) {
      console.error('Error parsing FAQ from document:', error, document);
      return null;
    }
  }, []);

  // Define UI actions for each message type
  const messageHandler = useCallback(
    (message: MessageUnion) => {
      try {
        if ('responseType' in message) {
          switch (message.responseType) {
            case 'documents':
              console.log(
                'Documents received from server:',
                message.content.documents
              );
              updateQueryResources(
                message.queryId,
                message.content.documents.map(document => {
                  // Check if this is an FAQ (sourceId="faqs")
                  if (document.sourceId === 'faqs') {
                    const faq = parseFAQFromDocument(document);
                    if (faq) {
                      return {
                        type: 'faq' as const,
                        data: faq,
                      };
                    }
                    // If parsing fails, fall through to document type
                    console.warn(
                      'FAQ parsing failed, treating as document:',
                      document
                    );
                  }

                  // Regular document
                  return {
                    type: 'document' as const,
                    data: document,
                  };
                })
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
        console.error(
          'Failed to process validated WebSocket message:',
          message,
          '\nProcessing error:',
          error
        );
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
      parseFAQFromDocument,
      updateQueryResources,
      updateQueryStatus,
      appendQueryResponse,
      setChatState,
      setQueryError,
      handleError,
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

  return {
    connectionState,
    isConnected,
    disconnect: close,
    reconnect,
    sendMessage,
  };
}
