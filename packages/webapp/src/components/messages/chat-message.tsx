'use client';

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';

import * as HoverCardPrimitive from '@radix-ui/react-hover-card';
import { AnimatedMarkdown } from 'flowtoken';
import { AnimatePresence, motion } from 'framer-motion';
import { Info } from 'lucide-react';
import { useMemo, useRef } from 'react';

import { DocumentList } from '../documents/document-list/document-list';
import { useBreakpoint } from '@/hooks/use-breakpoint';

import 'flowtoken/dist/styles.css';
import './chat-message.css';

export interface ChatMessageProps {
  queryId: string;
  query: string;
  response?: string;
  responseType?: 'stream';
  timestamp?: string;
  className?: string;
  streamingComplete?: boolean;
  selected?: boolean;
  documents?: Document[];
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
  documents: Document[];
}

export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
  sourceUrl?: string;
}

export function StreamResponse({
  content,
  className,
  streamingComplete,
}: StreamResponseProps) {
  return (
    <div className={`chat-response font-sans ${className || ''}`}>
      <AnimatedMarkdown
        content={content}
        animation={streamingComplete ? 'none' : 'blurAndSharpen'}
        animationDuration={streamingComplete ? '0s' : '1s'}
        animationTimingFunction="ease"
        sep="word"
      />
    </div>
  );
}

export function DocumentsStreamResponse({
  content,
  className,
  streamingComplete,
  documents,
}: DocumentsStreamResponseProps) {
  return (
    <div className={`chat-response font-sans ${className || ''}`}>
      {/* Documents List */}
      <div className="mt-6 mb-3">
        <DocumentList documents={documents} title="Referenced Documents" />
      </div>

      {/* Response Content */}
      <AnimatedMarkdown
        content={content}
        animation={streamingComplete ? 'none' : 'blurAndSharpen'}
        animationDuration={streamingComplete ? '0s' : '1s'}
        animationTimingFunction="ease"
        sep="word"
      />
    </div>
  );
}

function renderResponse(
  response: string,
  responseType: string,
  streamingComplete?: boolean,
  documents?: Document[],
  breakpoint?: string
) {
  // Show documents when there documents to show and the breakpoint is narrow
  const shouldShowDocuments =
    documents && documents.length > 0 && breakpoint === 'narrow';

  if (responseType === 'stream' && shouldShowDocuments) {
    return (
      <DocumentsStreamResponse
        content={response}
        streamingComplete={streamingComplete}
        documents={documents!}
      />
    );
  }

  // Otherwise show the plain response
  return (
    <StreamResponse content={response} streamingComplete={streamingComplete} />
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
  documents,
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
          documents,
          breakpoint
        )}
      </div>
    );
  }, [response, responseType, streamingComplete, documents, breakpoint]);

  const containerClassName = useMemo(
    () => `pl-4 font-sans ${className || ''}`,
    [className]
  );

  const messageContentClassName = useMemo(() => `mb-3`, []);

  const memoizedInfoIcon = useMemo(
    () => (
      <AnimatePresence initial={false}>
        {streamingComplete && (
          <motion.div
            initial={{ filter: 'blur(8px)', opacity: 0 }}
            animate={{ filter: 'blur(0px)', opacity: 1 }}
            exit={{ filter: 'blur(8px)', opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="chat-response-aligned mt-5"
          >
            <HoverCard>
              <HoverCardTrigger asChild>
                <Info className="text-muted-foreground hover:text-foreground h-4 w-4 cursor-pointer transition-colors" />
              </HoverCardTrigger>

              {/* Used the primitive portal because the container clips the hover card */}
              <HoverCardPrimitive.Portal>
                <HoverCardContent className="">
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
                      {documents && documents.length > 0 && (
                        <p>
                          <span className="font-medium">Documents:</span>{' '}
                          {documents.length}
                        </p>
                      )}
                    </div>
                  </div>
                </HoverCardContent>
              </HoverCardPrimitive.Portal>
            </HoverCard>
          </motion.div>
        )}
      </AnimatePresence>
    ),
    [streamingComplete, queryId, timestamp, documents]
  );

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
          <p className={`chat-query mb-4`}>{query}</p>

          {/* Response Paragraph */}
          {memoizedResponse}

          {/* Info icon with hover card displaying chat information */}
          {memoizedInfoIcon}
        </div>
      </div>
    </motion.div>
  );
}
