'use client';

import { useChatStore } from '@/stores/chat-store';
import { forwardRef, useCallback, useEffect, useRef } from 'react';
import { ChatMessage } from './chat-message';
import Acrylic from 'react-acrylic';

interface ChatContainerProps {
  variant?: 'default' | 'borderless' | 'narrow' | 'wide';
}

export const ChatContainer = forwardRef<HTMLDivElement, ChatContainerProps>(
  ({ variant = 'default' }, ref) => {
    const { queries, queryOrder, setCurrentQueryId, currentQueryId } =
      useChatStore();

    // Use currentQueryId from store instead of selectedMessageId
    const selectedMessageId = currentQueryId;
    const setSelectedMessageId = setCurrentQueryId;

    const containerRef = useRef<HTMLDivElement>(null);

    // Track the center-most message using scroll events (without auto-centering)
    const handleScroll = useCallback(() => {
      if (!containerRef.current) return;

      const containerRect = containerRef.current.getBoundingClientRect();
      const containerCenter = containerRect.top + containerRect.height / 2;

      let closestToCenter: { id: string; distance: number } | null = null;
      let closestDistance = Infinity;

      // Get ALL message elements
      const allMessageElements = containerRef.current.querySelectorAll(
        '[data-message-observe]'
      );

      for (const element of allMessageElements) {
        const rect = element.getBoundingClientRect();
        const elementCenter = rect.top + rect.height / 2;
        const distanceFromCenter = Math.abs(elementCenter - containerCenter);
        const messageId = (element as HTMLElement).dataset.messageId;

        if (distanceFromCenter < closestDistance && messageId) {
          closestDistance = distanceFromCenter;
          closestToCenter = { id: messageId, distance: distanceFromCenter };
        }
      }

      if (closestToCenter) {
        setSelectedMessageId(closestToCenter.id);
      }
    }, [setSelectedMessageId]);

    // Add scroll event listener
    useEffect(() => {
      const container = containerRef.current;
      if (container) {
        container.addEventListener('scroll', handleScroll, { passive: true });
        return () => {
          container.removeEventListener('scroll', handleScroll);
        };
      } else {
        return () => {};
      }
    }, [handleScroll]);

    // Get ordered queries based on queryOrder
    const orderedQueries = queryOrder
      .map(queryId => queries[queryId])
      .filter(Boolean);

    useEffect(() => {
      handleScroll();
    }, [handleScroll, orderedQueries.length]);

    return (
      <div className="relative h-full w-full">
        {variant === 'wide' ? (
          <div
            ref={node => {
              if (typeof ref === 'function') {
                ref(node);
              } else if (ref) {
                ref.current = node;
              }
              containerRef.current = node;
            }}
            className={`scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 h-full overflow-y-auto overflow-hidden rounded-lg border shadow-sm`}
          >
            <Acrylic
              className="h-full w-full"
              tintColor="rgba(255, 255, 255, 0.1)"
              luminosity={0.1}
            >
              <div className="h-[50%]"></div>
              <div className="space-y-10 py-[5%] pt-[10%] pr-[4%] pl-[4%]">
                {orderedQueries.map(query => (
                  <ChatMessage
                    key={query.queryId}
                    queryId={query.queryId}
                    query={query.query}
                    response={query.response.content || ''}
                    responseType="stream"
                    timestamp={query.timestamp}
                    streamingComplete={query.status === 'completed'}
                    selected={query.queryId === selectedMessageId}
                    items={query.resources || []}
                  />
                ))}
              </div>
              <div className="h-[50%]"></div>
            </Acrylic>
          </div>
        ) : (
          <div
            ref={node => {
              if (typeof ref === 'function') {
                ref(node);
              } else if (ref) {
                ref.current = node;
              }
              containerRef.current = node;
            }}
            className={`scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 h-full overflow-y-auto ${
              variant === 'borderless' || variant === 'narrow'
                ? 'bg-transparent'
                : 'bg-card overflow-hidden rounded-lg border shadow-sm'
            }`}
          >
            <div className="h-[50%]"></div>
            <div className="space-y-10 py-[5%] pt-[10%] pr-[4%] pl-[4%]">
              {orderedQueries.map(query => (
                <ChatMessage
                  key={query.queryId}
                  queryId={query.queryId}
                  query={query.query}
                  response={query.response.content || ''}
                  responseType="stream"
                  timestamp={query.timestamp}
                  streamingComplete={query.status === 'completed'}
                  selected={query.queryId === selectedMessageId}
                  items={query.resources || []}
                />
              ))}
            </div>
            <div className="h-[50%]"></div>
          </div>
        )}
      </div>
    );
  }
);

ChatContainer.displayName = 'ChatContainer';
