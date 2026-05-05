'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Info, ThumbsUp, ThumbsDown } from 'lucide-react';
import { useAssignFeedback } from '@/hooks/api/chat';
import { useChatStore } from '@/stores/chat-store';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DocumentList } from '../documents/document-list/document-list';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useDevTrace } from '@/hooks/use-dev-trace';
import type { AgentTraceEvent, ResourceItem } from '@/stores/types';
import type { QueryStatus } from '@/stores/types';

import './chat-message.css';
import AnimatedMarkdown from './animated-markdown';
import { Button } from '../ui/button';
import { ButtonGroup } from '../ui/button-group';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { FeedbackPopover } from './feedback-popover';

type TraceStep = { label: string; done: boolean; devJson?: string };

const TOOL_VERBS: Record<string, string> = {
  vector_search: 'Searching for',
  get_neighbors: 'Expanding graph from',
  get_document: 'Fetching document',
  faq_search: 'Checking FAQs for',
  fetch_case_opinion: 'Fetching opinion for',
  get_authority_chain: 'Walking authority chain from',
  list_framework_docs: 'Listing framework docs for',
  refine_query: 'Refining query',
  answer: 'Answering',
};

function verbFor(toolName: string): string {
  return TOOL_VERBS[toolName] ?? toolName;
}

function buildLegacySteps({
  hasResources,
  items,
  isStreaming,
  streamingComplete,
}: {
  hasResources: boolean;
  items?: ResourceItem[];
  isStreaming: boolean;
  streamingComplete?: boolean;
}): TraceStep[] {
  const steps: TraceStep[] = [];
  const searchDone = hasResources || isStreaming || streamingComplete === true;
  steps.push({
    label: searchDone ? 'Searched knowledge base' : 'Searching knowledge base...',
    done: searchDone,
  });
  if (hasResources && items) {
    steps.push({
      label: `Found ${items.length} source${items.length === 1 ? '' : 's'}`,
      done: true,
    });
  }
  const genDone = isStreaming || streamingComplete === true;
  steps.push({
    label: genDone ? 'Generated response' : 'Generating response...',
    done: genDone,
  });
  return steps;
}

function renderTraceStep(
  event: AgentTraceEvent,
  devMode: boolean
): TraceStep | null {
  const devJson =
    devMode &&
    event.devPayload &&
    Object.keys(event.devPayload).length > 0
      ? JSON.stringify(event.devPayload, null, 2)
      : undefined;

  if (event.kind === 'reasoning') {
    const text = String(event.payload.text ?? '');
    return text ? { label: text, done: true, devJson } : null;
  }
  if (event.kind === 'tool_call') {
    const name = String(event.payload.toolName ?? '');
    const summary = String(event.payload.summary ?? '');
    const label = summary ? `${verbFor(name)} ${summary}` : verbFor(name);
    return { label, done: false, devJson };
  }
  if (event.kind === 'tool_result') {
    const summary = String(event.payload.summary ?? '');
    const status = String(event.payload.status ?? 'ok');
    const done = status === 'ok' || status === 'terminal';
    return { label: summary, done, devJson };
  }
  return null;
}

function buildTraceSteps(
  trace: AgentTraceEvent[],
  devMode: boolean
): TraceStep[] {
  // Filter out loop_start and loop_complete — the outer "Thinking for Xs"
  // label already communicates start/end of the loop.
  const visible = trace.filter(
    e => e.kind !== 'loop_start' && e.kind !== 'loop_complete'
  );
  const steps: TraceStep[] = [];
  for (const event of visible) {
    const step = renderTraceStep(event, devMode);
    if (!step) continue;
    steps.push(step);
  }
  return steps;
}

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
  status,
  timestamp,
  className,
  streamingComplete,
  selected = true,
  items,
}: ChatMessageProps) {
  const messageRef = useRef<HTMLDivElement>(null);
  const breakpoint = useBreakpoint();

  // Thinking timer: starts on pending, stops when streaming begins
  const [thinkingSeconds, setThinkingSeconds] = useState(0);
  const thinkingStartRef = useRef<number | null>(null);
  const storedDuration = useChatStore(s => s.queries[queryId]?.thinkingDuration);

  useEffect(() => {
    if (status === 'pending' || status === 'sending') {
      thinkingStartRef.current = Date.now();
      const interval = setInterval(() => {
        if (thinkingStartRef.current) {
          setThinkingSeconds(Math.floor((Date.now() - thinkingStartRef.current) / 1000));
        }
      }, 1000);
      return () => clearInterval(interval);
    }
    if (thinkingStartRef.current) {
      const final = Math.floor((Date.now() - thinkingStartRef.current) / 1000);
      thinkingStartRef.current = null;
      queueMicrotask(() => {
        setThinkingSeconds(final);
        useChatStore.getState().setThinkingDuration(queryId, final);
      });
    }
    return undefined;
  }, [status, queryId]);

  const displaySeconds = storedDuration ?? thinkingSeconds;

  const isThinking = status === 'pending' || status === 'sending';
  const isStreaming = status === 'streaming';
  const hasCompleted = status === 'streaming' || status === 'completed';
  const showThinkingLabel = isThinking || hasCompleted;
  const hasResources = (items?.length ?? 0) > 0;
  const [stepsOpen, setStepsOpen] = useState(true);
  const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
  const devTrace = useDevTrace();

  const steps = useMemo<TraceStep[]>(() => {
    if (!agentTrace || agentTrace.length === 0) {
      return buildLegacySteps({
        hasResources,
        items,
        isStreaming,
        streamingComplete,
      });
    }
    return buildTraceSteps(agentTrace, devTrace);
  }, [agentTrace, devTrace, hasResources, items, isStreaming, streamingComplete]);

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
                onClick={() => setStepsOpen(prev => !prev)}
                className="flex items-center gap-1.5 text-muted-foreground cursor-pointer hover:text-foreground transition-colors"
              >
                <span className={isThinking ? 'thinking-shimmer' : ''}>
                  {isThinking
                    ? `Thinking for ${thinkingSeconds}s...`
                    : displaySeconds > 0
                      ? `Thought for ${displaySeconds}s`
                      : 'Thought'}
                </span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  fill="none"
                  className={`transition-transform duration-200 ${stepsOpen ? 'rotate-90' : ''}`}
                >
                  <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              <AnimatePresence>
                {stepsOpen && (
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
                        <div key={i} className="flex flex-col gap-1 -ml-[4px]">
                          <div className="flex items-center gap-2.5">
                            <div
                              className={`h-[7px] w-[7px] shrink-0 rounded-full transition-colors duration-500 ${
                                step.done
                                  ? 'bg-muted-foreground'
                                  : 'border border-muted-foreground/50 bg-background'
                              }`}
                            />
                            <span>{step.label}</span>
                          </div>
                          {step.devJson && (
                            <details className="ml-5 text-[0.75em] text-muted-foreground/80">
                              <summary className="cursor-pointer select-none">devPayload</summary>
                              <pre className="overflow-auto bg-muted/40 rounded p-2 mt-1 text-xs">
                                {step.devJson}
                              </pre>
                            </details>
                          )}
                        </div>
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
}
