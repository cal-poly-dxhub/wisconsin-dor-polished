'use client';

import { useChatStore } from '@/stores/chat-store';
import { forwardRef, useCallback, useEffect, useRef, useState } from 'react';
import { ChatMessage } from './chat-message';
import { ChevronDown } from 'lucide-react';

interface ChatContainerProps {
  variant?: 'default' | 'borderless' | 'narrow' | 'wide';
}

export const ChatContainer = forwardRef<HTMLDivElement, ChatContainerProps>(
  ({ variant = 'default' }, ref) => {
    const queries = useChatStore(state => state.queries);
    const queryOrder = useChatStore(state => state.queryOrder);
    const currentQueryId = useChatStore(state => state.currentQueryId);
    const setCurrentQueryId = useChatStore(state => state.setCurrentQueryId);

    // Use currentQueryId from store instead of selectedMessageId
    const selectedMessageId = currentQueryId;
    const setSelectedMessageId = setCurrentQueryId;

    const containerRef = useRef<HTMLDivElement>(null);
    const userScrolledUpRef = useRef(false);
    const [showScrollButton, setShowScrollButton] = useState(false);

    // Track the center-most message using scroll events (without auto-centering)
    const handleScroll = useCallback(() => {
      if (!containerRef.current) return;

      // Detect if user has scrolled away from the bottom
      const el = containerRef.current;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
      userScrolledUpRef.current = !atBottom;

      // Show scroll button when user scrolled up and content overflows
      const hasOverflow = el.scrollHeight > el.clientHeight;
      setShowScrollButton(!atBottom && hasOverflow);

      const containerRect = el.getBoundingClientRect();
      const containerCenter = containerRect.top + containerRect.height / 2;

      let closestToCenter: { id: string; distance: number } | null = null;
      let closestDistance = Infinity;

      const allMessageElements = el.querySelectorAll(
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

    const scrollToBottom = useCallback(() => {
      if (!containerRef.current) return;
      userScrolledUpRef.current = false;
      containerRef.current.scrollTo({
        top: containerRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }, []);

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
      // Use requestAnimationFrame to avoid calling setState synchronously
      requestAnimationFrame(() => {
        handleScroll();
      });
    }, [handleScroll, orderedQueries.length]);

    // A newly submitted question means the user wants to follow the next
    // answer. Reset the scrolled-up flag (handleScroll may have just flipped
    // it to true, because scrollHeight grew before scrollTop caught up) and
    // pin to the bottom so the streaming effect below can take over.
    useEffect(() => {
      userScrolledUpRef.current = false;
      if (containerRef.current) {
        containerRef.current.scrollTop = containerRef.current.scrollHeight;
      }
    }, [orderedQueries.length]);

    // Auto-scroll to bottom during streaming unless user scrolled up
    const currentQuery = currentQueryId ? queries[currentQueryId] : null;
    const streamContent = currentQuery?.response?.content;
    const queryStatus = currentQuery?.status;
    const agentTrace = currentQuery?.agentTrace;
    const isActive = queryStatus === 'streaming' || queryStatus === 'pending' || queryStatus === 'completed';

    useEffect(() => {
      if (!containerRef.current || !isActive || userScrolledUpRef.current) return;
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }, [streamContent, queryStatus, isActive, agentTrace]);

    return (
      <div className="relative h-full w-full">
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
            variant === 'borderless' || variant === 'narrow' || variant === 'wide'
              ? 'bg-transparent'
              : 'bg-card overflow-hidden rounded-lg border shadow-sm'
          }`}
        >
          <div className="space-y-10 py-6 pr-[4%] pl-[4%] pt-[4%]">
            {orderedQueries.map(query => (
              <ChatMessage
                key={query.queryId}
                queryId={query.queryId}
                query={query.query}
                response={query.response.content || ''}
                responseType="stream"
                status={query.status}
                timestamp={query.timestamp}
                streamingComplete={query.status === 'completed'}
                selected={query.queryId === selectedMessageId}
                items={query.resources || []}
              />
            ))}
          </div>
        </div>

        {/* Scroll to bottom button */}
        <button
          onClick={scrollToBottom}
          className={`absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center justify-center w-10 h-10 rounded-full bg-card/95 border-2 border-primary/20 shadow-xl hover:bg-card hover:border-primary/40 hover:shadow-2xl cursor-pointer backdrop-blur-sm transition-all duration-200 ease-out ${
            showScrollButton
              ? 'opacity-100 translate-y-0 pointer-events-auto'
              : 'opacity-0 translate-y-2 pointer-events-none'
          }`}
          aria-label="Scroll to bottom"
        >
          <ChevronDown className="h-5 w-5 text-foreground" />
        </button>
      </div>
    );
  }
);

ChatContainer.displayName = 'ChatContainer';
