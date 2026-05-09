'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Info, ThumbsUp, ThumbsDown } from 'lucide-react';
import { useAssignFeedback } from '@/hooks/api/chat';
import { useChatStore } from '@/stores/chat-store';
import { memo, useEffect, useMemo, useRef, useState } from 'react';

import { DocumentList } from '../documents/document-list/document-list';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { ResourceItem, TraceEvent } from '@/stores/types';
import type { QueryStatus } from '@/stores/types';

import './chat-message.css';
import AnimatedMarkdown from './animated-markdown';
import { Button } from '../ui/button';
import { ButtonGroup } from '../ui/button-group';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { FeedbackPopover } from './feedback-popover';

export interface ChatMessageProps {
  queryId: string;
  query: string;
  response?: string;
  responseType?: 'stream';
  status?: QueryStatus;
  timestamp?: string;
  className?: string;
  streamingComplete?: boolean;
  selected?: boolean;
  items?: ResourceItem[];
  traceEvents?: TraceEvent[];
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
        <AnimatedMarkdown content={content} animate={!streamingComplete} />
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
        <AnimatedMarkdown content={content} animate={!streamingComplete} />
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

function mergeTraceMetadata(
  a?: Record<string, unknown> | null,
  b?: Record<string, unknown> | null
): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  const numericKeys = [
    'faq_count', 'faqCount',
    'chunk_count', 'chunkCount',
    'neighbor_count', 'neighborCount',
    'document_count', 'documentCount',
    'chain_length', 'chainLength',
    'cited_doc_count', 'citedDocCount',
    'rag_document_count', 'ragDocumentCount',
  ];
  for (const key of numericKeys) {
    const va = a?.[key];
    const vb = b?.[key];
    if (typeof va === 'number' || typeof vb === 'number') {
      merged[key] = ((typeof va === 'number' ? va : 0) + (typeof vb === 'number' ? vb : 0));
    }
  }
  const topA = a?.['top_score'] ?? a?.['topScore'];
  const topB = b?.['top_score'] ?? b?.['topScore'];
  if (typeof topA === 'number' || typeof topB === 'number') {
    merged['topScore'] = Math.max(
      typeof topA === 'number' ? topA : 0,
      typeof topB === 'number' ? topB : 0
    );
  }
  const elA = a?.['elapsed_ms'] ?? a?.['elapsedMs'];
  const elB = b?.['elapsed_ms'] ?? b?.['elapsedMs'];
  if (typeof elA === 'number' || typeof elB === 'number') {
    merged['elapsedMs'] = ((typeof elA === 'number' ? elA : 0) + (typeof elB === 'number' ? elB : 0));
  }
  return merged;
}

function formatTraceMetadata(metadata?: Record<string, unknown> | null) {
  if (!metadata) return '';

  const parts: string[] = [];
  const get = (snake: string, camel: string) =>
    metadata[snake] ?? metadata[camel];

  const addCount = (
    snake: string,
    camel: string,
    singular: string,
    plural = `${singular}s`
  ) => {
    const value = get(snake, camel);
    if (typeof value === 'number' && value > 0) {
      parts.push(`${value} ${value === 1 ? singular : plural}`);
    }
  };

  addCount('faq_count', 'faqCount', 'FAQ hit');
  addCount('chunk_count', 'chunkCount', 'chunk');
  addCount('neighbor_count', 'neighborCount', 'neighbor');
  addCount('document_count', 'documentCount', 'document');
  addCount('chain_length', 'chainLength', 'authority step');
  addCount('cited_doc_count', 'citedDocCount', 'citation');
  addCount('rag_document_count', 'ragDocumentCount', 'source');

  const topScore = get('top_score', 'topScore');
  if (typeof topScore === 'number') {
    parts.push(`top score ${topScore.toFixed(2)}`);
  }
  const elapsedMs = get('elapsed_ms', 'elapsedMs');
  if (typeof elapsedMs === 'number') {
    parts.push(`${(elapsedMs / 1000).toFixed(1)}s`);
  }
  if (metadata.refined === true) {
    parts.push('expanded follow-up');
  }

  return parts.join(' · ');
}

function parseTimestampMs(timestamp?: string) {
  if (!timestamp) return null;

  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? parsed : null;
}

function elapsedSecondsSince(startedAt: number) {
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
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

export const ChatMessage = memo(function ChatMessage({
  queryId,
  query,
  response,
  responseType = 'stream',
  status,
  timestamp,
  className,
  streamingComplete,
  selected = true,
  items,
  traceEvents,
}: ChatMessageProps) {
  const messageRef = useRef<HTMLDivElement>(null);
  const breakpoint = useBreakpoint();

  const isThinking = status === 'pending' || status === 'sending' || status === 'sent';
  const isStreaming = status === 'streaming';
  const hasCompleted = status === 'streaming' || status === 'completed';
  const showThinkingLabel = isThinking || hasCompleted;
  const hasResources = (items?.length ?? 0) > 0;
  const [stepsOpen, setStepsOpen] = useState(true);

  // Collapse thinking steps when LLM starts streaming
  useEffect(() => {
    if (isStreaming) {
      // Use queueMicrotask to avoid synchronous setState in effect
      queueMicrotask(() => setStepsOpen(false));
    }
  }, [isStreaming]);

  // Thinking timer: starts on pending, stops when streaming begins.
  // Anchor to the original query timestamp so replacing the optimistic ID
  // with the server query ID does not restart the visible timer.
  const [thinkingSeconds, setThinkingSeconds] = useState(() => {
    if (!isThinking) return 0;

    const startedAt = parseTimestampMs(timestamp);
    return startedAt ? elapsedSecondsSince(startedAt) : 0;
  });
  const thinkingStartRef = useRef<number | null>(null);
  const storedDuration = useChatStore(s => s.queries[queryId]?.thinkingDuration);

  useEffect(() => {
    if (isThinking) {
      const startedAt =
        thinkingStartRef.current ?? parseTimestampMs(timestamp) ?? Date.now();
      thinkingStartRef.current = startedAt;

      const updateElapsed = () => {
        setThinkingSeconds(elapsedSecondsSince(startedAt));
      };

      updateElapsed();
      const interval = setInterval(() => {
        updateElapsed();
      }, 1000);
      return () => clearInterval(interval);
    }

    if (thinkingStartRef.current) {
      const final = elapsedSecondsSince(thinkingStartRef.current);
      thinkingStartRef.current = null;
      setThinkingSeconds(final);
      if (storedDuration === undefined) {
        useChatStore.getState().setThinkingDuration(queryId, final);
      }
    }
    return undefined;
  }, [isThinking, queryId, storedDuration, timestamp]);

  const displaySeconds = storedDuration ?? thinkingSeconds;

  // Use streamed backend trace events when available; otherwise keep the
  // previous synthetic steps for non-GraphRAG responses after waiting has ended.
  // Collapses consecutive completed entries for the same tool into one line.
  const steps = useMemo(() => {
    if (traceEvents && traceEvents.length > 0) {
      const mapped = traceEvents.map(event => ({
        label: event.label,
        detail: formatTraceMetadata(event.metadata),
        done: event.status === 'complete',
        error: event.status === 'error',
        toolName: event.toolName ?? null,
        metadata: event.metadata,
      }));

      interface CollapsedStep {
        label: string;
        detail: string;
        done: boolean;
        error: boolean;
        toolName: string | null;
        metadata: Record<string, unknown> | null | undefined;
        count: number;
      }

      const collapsed: CollapsedStep[] = [];
      for (const step of mapped) {
        const prev = collapsed[collapsed.length - 1];
        if (
          prev &&
          step.toolName &&
          step.toolName === prev.toolName &&
          step.done &&
          prev.done
        ) {
          prev.count += 1;
          const merged = mergeTraceMetadata(prev.metadata, step.metadata);
          prev.detail = formatTraceMetadata(merged);
          prev.metadata = merged;
        } else {
          collapsed.push({ ...step, count: 1 });
        }
      }

      return collapsed.map(({ label, detail, done, error }) => ({
        label,
        detail,
        done,
        error,
      }));
    }

    if (isThinking) {
      return [];
    }

    const s: { label: string; done: boolean; detail?: string; error?: boolean }[] = [];
    const searchDone = hasResources || isStreaming || streamingComplete === true;
    s.push({ label: searchDone ? 'Searched knowledge base' : 'Searching knowledge base...', done: searchDone });
    if (hasResources) {
      s.push({ label: `Found ${items!.length} source${items!.length === 1 ? '' : 's'}`, done: true });
    }
    const genDone = isStreaming || streamingComplete === true;
    s.push({ label: genDone ? 'Generated response' : 'Generating response...', done: genDone });
    return s;
  }, [traceEvents, isThinking, hasResources, items, isStreaming, streamingComplete]);

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
    () => `font-sans ${className || ''}`,
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
        scale: 1,
        opacity: 1,
      }}
      transition={{
        duration: 0.2,
        ease: 'easeInOut',
      }}
    >
      {/* Message Content */}
      <div style={{ pointerEvents: selected ? 'auto' : 'none' }}>
        <div className={messageContentClassName}>
          {/* User Query - right-aligned bubble */}
          <div className="flex justify-end mb-4">
            <p className="chat-query rounded-2xl bg-muted px-4 py-2.5 text-sm inline-block max-w-[80%]">
              {query}
            </p>
          </div>

          {/* Thinking label with collapsible steps */}
          {showThinkingLabel && (
            <div className="mb-4" style={{ fontSize: 'clamp(0.9rem, 1vw + 0.5rem, 1.05rem)' }}>
              <button
                onClick={steps.length > 0 ? () => setStepsOpen(prev => !prev) : undefined}
                className={`flex items-center gap-1.5 text-muted-foreground transition-colors ${
                  steps.length > 0 ? 'cursor-pointer hover:text-foreground' : ''
                }`}
              >
                <span className={isThinking ? 'thinking-shimmer' : ''}>
                  {isThinking
                    ? `Thinking for ${thinkingSeconds}s...`
                    : displaySeconds > 0
                      ? `Thought for ${displaySeconds}s`
                      : 'Thought'}
                </span>
                {steps.length > 0 && (
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 12 12"
                    fill="none"
                    className={`transition-transform duration-200 ${stepsOpen ? 'rotate-90' : ''}`}
                  >
                    <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </button>
              <AnimatePresence>
                {steps.length > 0 && stepsOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15, ease: [0.4, 0, 0.2, 1] }}
                    className="overflow-hidden mt-2 text-muted-foreground"
                    style={{ fontSize: '0.85em' }}
                  >
                    <div className="relative ml-[3.5px] border-l border-muted-foreground/25 space-y-3 py-1">
                      {steps.map((step, i) => (
                        <motion.div
                          key={`${step.label}-${i}`}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.18, ease: 'easeOut' }}
                          className="flex items-start gap-2.5 -ml-[4px]"
                        >
                          <div
                            className={`mt-[0.45em] h-[7px] w-[7px] shrink-0 rounded-full transition-colors duration-500 ${
                              step.error
                                ? 'bg-destructive'
                                : step.done
                                  ? 'bg-muted-foreground'
                                  : 'border border-muted-foreground/50 bg-background'
                            }`}
                          />
                          <span>
                            <span>{step.label}</span>
                            {step.detail && (
                              <span className="block text-muted-foreground/70">
                                {step.detail}
                              </span>
                            )}
                          </span>
                        </motion.div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

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
});
