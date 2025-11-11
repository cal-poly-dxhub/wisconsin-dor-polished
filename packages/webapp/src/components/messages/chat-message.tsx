'use client';

import { AnimatedMarkdown } from 'flowtoken';
import { AnimatePresence, motion } from 'framer-motion';
import { Info, ThumbsUp, ThumbsDown } from 'lucide-react';
import { useAssignFeedback } from '@/hooks/api/chat';
import { useChatStore } from '@/stores/chat-store';
import { useMemo, useRef, useState } from 'react';

import { DocumentList } from '../documents/document-list/document-list';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { ResourceItem } from '@/stores/types';

import 'flowtoken/dist/styles.css';
import './chat-message.css';
import { Button } from '../ui/button';
import { ButtonGroup } from '../ui/button-group';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { FeedbackPopover } from './feedback-popover';

export interface ChatMessageProps {
  queryId: string;
  query: string;
  response?: string;
  responseType?: 'stream';
  timestamp?: string;
  className?: string;
  streamingComplete?: boolean;
  selected?: boolean;
  items?: ResourceItem[];
}

interface StreamResponseProps {
  content: string;
  className?: string;
  streamingComplete?: boolean;
}

interface DocumentsStreamResponseProps {
  content: string;
  className?: string;
  streamingComplete?: boolean;
  items: ResourceItem[];
}

export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
  sourceUrl?: string;
}

interface MessageOptionsBarProps {
  streamingComplete: boolean;
  queryId: string;
  timestamp: string;
  items: ResourceItem[];
}

export function StreamResponse({
  content,
  className,
  streamingComplete,
}: StreamResponseProps) {
  return (
    <div className={`chat-response font-sans ${className || ''}`}>
      <div className="markdown-container">
        <AnimatedMarkdown
          content={content}
          animation={streamingComplete ? 'none' : 'blurAndSharpen'}
          animationDuration={streamingComplete ? '0s' : '1s'}
          animationTimingFunction="ease"
          sep="word"
        />
      </div>
    </div>
  );
}

export function DocumentsStreamResponse({
  content,
  className,
  streamingComplete,
  items,
}: DocumentsStreamResponseProps) {
  return (
    <div className={`chat-response font-sans ${className || ''}`}>
      {/* Documents List */}
      <div className="mt-3">
        <DocumentList items={items} title="Referenced Documents" />
      </div>

      <div className="markdown-container">
        <AnimatedMarkdown
          content={content}
          animation={streamingComplete ? 'none' : 'blurAndSharpen'}
          animationDuration={streamingComplete ? '0s' : '1s'}
          animationTimingFunction="ease"
          sep="word"
        />
      </div>
    </div>
  );
}

function renderResponse(
  response: string,
  responseType: string,
  streamingComplete?: boolean,
  items?: ResourceItem[],
  breakpoint?: string
) {
  // Show documents when there documents to show and the breakpoint is narrow
  const shouldShowDocuments =
    items && items.length > 0 && breakpoint === 'narrow';

  if (responseType === 'stream' && shouldShowDocuments) {
    return (
      <DocumentsStreamResponse
        content={response}
        streamingComplete={streamingComplete}
        items={items!}
      />
    );
  }

  // Otherwise show the plain response
  return (
    <StreamResponse content={response} streamingComplete={streamingComplete} />
  );
}

function MessageOptionsBar({
  streamingComplete,
  queryId,
  timestamp,
  items,
}: MessageOptionsBarProps) {
  const [infoOpen, setInfoOpen] = useState(false);
  const [thumbsUpOpen, setThumbsUpOpen] = useState(false);
  const [thumbsDownOpen, setThumbsDownOpen] = useState(false);
  const assignFeedback = useAssignFeedback();

  const sessionId = useChatStore(state => state.sessionId);

  const handleFeedback = (thumbUp: boolean, feedback?: string) => {
    console.log('handleFeedback', thumbUp, feedback);

    if (!sessionId) {
      console.log('Found no session ID; skipping feedback assignment.');
      return;
    }

    console.log('Run feedback mutation');

    assignFeedback.mutate({
      sessionId: sessionId!,
      payload: { queryId, thumbUp, feedback },
    });
  };

  return (
    <AnimatePresence initial={false}>
      {streamingComplete && (
        <motion.div
          initial={{ filter: 'blur(8px)', opacity: 0 }}
          animate={{ filter: 'blur(0px)', opacity: 1 }}
          exit={{ filter: 'blur(8px)', opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="chat-response-aligned mt-8 flex items-center gap-2"
        >
          <ButtonGroup>
            <Popover open={infoOpen} onOpenChange={setInfoOpen}>
              <PopoverTrigger asChild>
                <Button variant="outline" size="icon" aria-label="Message Info">
                  <Info className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="start">
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">Message Details</h4>
                  <div className="space-y-1 text-xs">
                    <p>
                      <span className="font-medium">Query ID:</span> {queryId}
                    </p>
                    {timestamp && (
                      <p>
                        <span className="font-medium">Timestamp:</span>{' '}
                        {timestamp}
                      </p>
                    )}
                    {items && items.length > 0 && (
                      <p>
                        <span className="font-medium">Documents:</span>{' '}
                        {items.length}
                      </p>
                    )}
                  </div>
                </div>
              </PopoverContent>
            </Popover>
          </ButtonGroup>
          <ButtonGroup>
            <FeedbackPopover
              open={thumbsUpOpen}
              onOpenChange={setThumbsUpOpen}
              onSubmit={feedback => handleFeedback(true, feedback)}
            >
              <Button variant="outline" size="icon" aria-label="Thumbs Up">
                <ThumbsUp className="h-4 w-4" />
              </Button>
            </FeedbackPopover>
            <FeedbackPopover
              open={thumbsDownOpen}
              onOpenChange={setThumbsDownOpen}
              onSubmit={feedback => handleFeedback(false, feedback)}
            >
              <Button variant="outline" size="icon" aria-label="Thumbs Down">
                <ThumbsDown className="h-4 w-4" />
              </Button>
            </FeedbackPopover>
          </ButtonGroup>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function ChatMessage({
  queryId,
  query,
  response,
  responseType = 'stream',
  timestamp,
  className,
  streamingComplete,
  selected = true,
  items,
}: ChatMessageProps) {
  const messageRef = useRef<HTMLDivElement>(null);
  const breakpoint = useBreakpoint();

  const memoizedResponse = useMemo(() => {
    if (!response) return null;

    return (
      <div className="chat-response-aligned">
        {renderResponse(
          response,
          responseType,
          streamingComplete,
          items,
          breakpoint
        )}
      </div>
    );
  }, [response, responseType, streamingComplete, items, breakpoint]);

  const containerClassName = useMemo(
    () => `pl-4 font-sans ${className || ''}`,
    [className]
  );

  const messageContentClassName = useMemo(() => `mb-3`, []);

  return (
    <motion.div
      ref={messageRef}
      className={containerClassName}
      data-message-observe
      data-message-id={queryId}
      animate={{
        scale: selected ? 1 : 0.95,
        opacity: selected ? 1 : 0.25,
      }}
      transition={{
        duration: 0.2,
        ease: 'easeInOut',
      }}
    >
      {/* Message Content */}
      <div style={{ pointerEvents: selected ? 'auto' : 'none' }}>
        <div className={messageContentClassName}>
          {/* Query Paragraph */}
          <p className={`chat-query mb-2`}>{query}</p>

          {/* Response Paragraph */}
          {memoizedResponse}

          {/* Info icon with hover card displaying chat information */}
          <MessageOptionsBar
            streamingComplete={streamingComplete ?? false}
            queryId={queryId}
            timestamp={timestamp ?? 'No timestamp available'}
            items={items ?? []}
          />
        </div>
      </div>
    </motion.div>
  );
}
