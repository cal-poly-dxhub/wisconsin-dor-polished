'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Info, ThumbsUp, ThumbsDown, FileText, Network } from 'lucide-react';
import { useAssignFeedback } from '@/hooks/api/chat';
import { useChatStore } from '@/stores/chat-store';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DocumentCard, type Document } from '../documents/document-card/document-card';
import { FAQCard } from '../documents/document-card/faq-card';
import { buildResolverUrl } from '@/lib/citation-resolver';
import { useDevTrace } from '@/hooks/use-dev-trace';
import { useSettingsStore } from '@/stores/settings-store';
import type { AgentTraceEvent, ResourceItem, FAQ } from '@/stores/types';
import type { QueryStatus } from '@/stores/types';

import './chat-message.css';
import AnimatedMarkdown from './animated-markdown';
import { formatTraceMetadata } from './trace-metadata';
import { Button } from '../ui/button';
import { ButtonGroup } from '../ui/button-group';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { FeedbackModal } from './feedback-modal';
import { RetrievalModal } from './retrieval-modal';

type TraceStep = {
  label: string;
  done: boolean;
  error?: boolean;
  // `missed` means a tool ran successfully but produced no result (e.g.,
  // get_document found no doc, fetch_case_opinion found no opinion). Distinct
  // from error because the graph/S3 call itself succeeded.
  missed?: boolean;
  detail?: string;
  devJson?: string;
};

const TOOL_VERBS: Record<string, string> = {
  vector_search: 'Searching knowledge graph for',
  search_document: 'Searching',
  get_neighbors: 'Exploring related documents from',
  get_document: 'Fetching document',
  faq_search: 'Checking FAQs for',
  fetch_case_opinion: 'Fetching opinion for',
  get_authority_chain: 'Tracing authority chain from',
  list_framework_docs: 'Listing framework documents for',
  refine_query: 'Refining query',
  answer: 'Answering',
};

function verbFor(toolName: string): string {
  return TOOL_VERBS[toolName] ?? toolName;
}

function parseTimestampMs(timestamp?: string): number | null {
  if (!timestamp) return null;
  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? parsed : null;
}

function elapsedSecondsSince(startedAt: number): number {
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
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
  const detail = formatTraceMetadata(event.payload.metadata);

  if (event.kind === 'reasoning') {
    const text = String(event.payload.text ?? '');
    return text ? { label: text, done: true, detail, devJson } : null;
  }
  if (event.kind === 'tool_call') {
    const name = String(event.payload.toolName ?? '');
    const summary = String(event.payload.summary ?? '');
    const label = summary ? `${verbFor(name)} ${summary}` : verbFor(name);
    return { label, done: false, detail, devJson };
  }
  if (event.kind === 'tool_result') {
    const summary = String(event.payload.summary ?? '');
    const status = String(event.payload.status ?? 'ok');
    const missed = status === 'miss';
    const done = status === 'ok' || status === 'terminal' || missed;
    const error = status === 'error';
    return { label: summary, done, error, missed, detail, devJson };
  }
  if (event.kind === 'phase') {
    const label = event.payload.label ? String(event.payload.label) : '';
    if (!label) return null;
    return { label, done: true, detail, devJson };
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

  type CollapsibleStep = TraceStep & {
    toolName: string | null;
    isCompletedResult: boolean;
    metadata: Record<string, unknown> | null;
  };
  const collapsed: CollapsibleStep[] = [];

  for (const event of visible) {
    const step = renderTraceStep(event, devMode);
    if (!step) continue;
    const toolName =
      event.kind === 'tool_call' || event.kind === 'tool_result'
        ? (event.payload.toolName as string) ?? null
        : null;
    const isCompletedResult =
      event.kind === 'tool_result' &&
      step.done === true &&
      !step.error &&
      !step.missed;
    const metadata = (event.payload.metadata as Record<string, unknown>) ?? null;

    const prev = collapsed[collapsed.length - 1];
    // Fold consecutive completed results for the same tool into one line.
    // The agent often issues 2-3 vector_searches in a row; without this, the
    // trace shows the same "Searched the knowledge graph" entry repeated
    // with partial counts. Merging sums the counts and keeps one row.
    // Skip search_document — each targets a different doc and should stay separate.
    if (
      prev &&
      prev.isCompletedResult &&
      isCompletedResult &&
      toolName &&
      toolName !== 'search_document' &&
      prev.toolName === toolName
    ) {
      const merged = mergeTraceMetadata(prev.metadata, metadata);
      prev.metadata = merged;
      prev.detail = formatTraceMetadata(merged);
      continue;
    }
    collapsed.push({ ...step, toolName, isCompletedResult, metadata });
  }

  return collapsed.map(({ label, done, error, missed, detail, devJson }) => ({
    label,
    done,
    error,
    missed,
    detail,
    devJson,
  }));
}

export interface ChatMessageProps {
  queryId: string;
  query: string;
  response?: string;
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
  docUrls?: Record<string, string>;
}


interface MessageOptionsBarProps {
  streamingComplete: boolean;
  queryId: string;
  timestamp: string;
  items: ResourceItem[];
}

const CHARS_PER_SECOND = 150;
const TICK_MS = 16;
const CHARS_PER_TICK = Math.max(1, Math.round((CHARS_PER_SECOND * TICK_MS) / 1000));

function useProgressiveReveal(content: string, streaming: boolean): string {
  const [visibleLen, setVisibleLen] = useState(() => streaming ? 0 : content.length);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number>(0);

  useEffect(() => {
    if (!streaming || !content) {
      return;
    }

    const target = content.length;

    function tick(now: number) {
      if (!lastTickRef.current) lastTickRef.current = now;
      const elapsed = now - lastTickRef.current;
      if (elapsed >= TICK_MS) {
        const ticks = Math.floor(elapsed / TICK_MS);
        setVisibleLen(prev => {
          const next = Math.min(prev + CHARS_PER_TICK * ticks, target);
          if (next >= target) return target;
          return next;
        });
        lastTickRef.current = now;
      }
      rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [content, streaming]);

  if (!streaming) return content;
  if (visibleLen >= content.length) return content;
  let end = visibleLen;
  while (end < content.length && content[end] !== ' ' && content[end] !== '\n') {
    end++;
    if (end - visibleLen > 20) { end = visibleLen; break; }
  }
  return content.slice(0, end);
}

export function StreamResponse({
  content,
  className,
  streamingComplete,
  docUrls,
}: StreamResponseProps) {
  const visibleContent = useProgressiveReveal(content, !streamingComplete);
  return (
    <div className={`chat-response font-sans ${className || ''}`}>
      <div className="markdown-container">
        <AnimatedMarkdown content={visibleContent} animate={false} docUrls={docUrls} />
      </div>
    </div>
  );
}

function InlineSources({ items, streamingComplete }: { items: ResourceItem[]; streamingComplete?: boolean }) {
  const [open, setOpen] = useState(true);

  if (!items.length || !streamingComplete) return null;

  const docCount = items.filter(i => i.type === 'document').length;
  const faqCount = items.filter(i => i.type === 'faq').length;
  const parts: string[] = [];
  if (docCount > 0) parts.push(`${docCount} document${docCount === 1 ? '' : 's'}`);
  if (faqCount > 0) parts.push(`${faqCount} FAQ${faqCount === 1 ? '' : 's'}`);

  return (
    <div className="mt-4">
      <button
        onClick={() => setOpen(prev => !prev)}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-[color,background-color,border-color] cursor-pointer mb-3"
      >
        <FileText className="h-3.5 w-3.5" />
        <span>Sources ({parts.join(', ')})</span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
        >
          <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      {open && (
        <div className="inline-sources-row grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-2.5">
          {items.map(item => {
            const key =
              item.type === 'document'
                ? `doc-${(item.data as Document).documentId}`
                : `faq-${(item.data as FAQ).faqId}`;
            return (
              <div key={key}>
                {item.type === 'document' ? (
                  <DocumentCard document={item.data as Document} />
                ) : (
                  <FAQCard faq={item.data as FAQ} />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
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

function MessageOptionsBar({
  streamingComplete,
  queryId,
  timestamp,
  items,
}: MessageOptionsBarProps) {
  const [infoOpen, setInfoOpen] = useState(false);
  const [feedbackModal, setFeedbackModal] = useState<'up' | 'down' | null>(null);
  const [submittedRating, setSubmittedRating] = useState<'up' | 'down' | null>(null);
  const [retrievalOpen, setRetrievalOpen] = useState(false);
  const assignFeedback = useAssignFeedback();
  const devMode = useDevTrace();
  const hasTrace = useChatStore(s => (s.queries[queryId]?.agentTrace?.length ?? 0) > 0);

  const sessionId = useChatStore(state => state.sessionId);

  const handleFeedback = (thumbUp: boolean, feedback?: string) => {
    if (!sessionId) return;

    assignFeedback.mutate({
      sessionId: sessionId!,
      payload: { queryId, thumbUp, feedback },
    });
    setSubmittedRating(thumbUp ? 'up' : 'down');
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
                <Button variant="outline" size="icon" className="cursor-pointer" aria-label="Message Info">
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
            {devMode && hasTrace && (
              <Button
                variant="outline"
                size="icon"
                className="cursor-pointer"
                aria-label="View Retrieval Pipeline"
                onClick={() => setRetrievalOpen(true)}
              >
                <Network className="h-4 w-4" />
              </Button>
            )}
          </ButtonGroup>
          <RetrievalModal queryId={queryId} open={retrievalOpen} onClose={() => setRetrievalOpen(false)} />
          <ButtonGroup>
            <Button
              variant={submittedRating === 'up' ? 'default' : 'outline'}
              size="icon"
              className="cursor-pointer"
              aria-label="Thumbs Up"
              onClick={() => setFeedbackModal('up')}
            >
              <ThumbsUp className="h-4 w-4" />
            </Button>
            <Button
              variant={submittedRating === 'down' ? 'default' : 'outline'}
              size="icon"
              className="cursor-pointer"
              aria-label="Thumbs Down"
              onClick={() => setFeedbackModal('down')}
            >
              <ThumbsDown className="h-4 w-4" />
            </Button>
          </ButtonGroup>

          <FeedbackModal
            open={feedbackModal !== null}
            onOpenChange={(open) => { if (!open) setFeedbackModal(null); }}
            type={feedbackModal ?? 'up'}
            onSubmit={(feedback) => handleFeedback(feedbackModal === 'up', feedback)}
            items={items}
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function ChatMessage({
  queryId,
  query,
  response,
  status,
  timestamp,
  className,
  streamingComplete,
  selected = true,
  items,
}: ChatMessageProps) {
  const messageRef = useRef<HTMLDivElement>(null);

  const isThinking =
    status === 'pending' || status === 'sending' || status === 'sent';
  const isStreaming = status === 'streaming';

  // Thinking timer anchored to the query's original timestamp so replacing
  // the optimistic ID with the server query ID does not restart it.
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
      const interval = setInterval(() => {
        setThinkingSeconds(elapsedSecondsSince(startedAt));
      }, 1000);
      return () => clearInterval(interval);
    }
    if (thinkingStartRef.current) {
      const final = elapsedSecondsSince(thinkingStartRef.current);
      thinkingStartRef.current = null;
      if (storedDuration === undefined) {
        useChatStore.getState().setThinkingDuration(queryId, final);
      }
    }
    return undefined;
  }, [isThinking, queryId, storedDuration, timestamp]);

  const displaySeconds = storedDuration ?? thinkingSeconds;

  const hasCompleted = status === 'streaming' || status === 'completed';
  const showThinkingLabel = isThinking || hasCompleted;
  const hasResources = (items?.length ?? 0) > 0;
  const detailedTrace = useSettingsStore(s => s.detailedTrace);
  const [stepsOpen, setStepsOpen] = useState(detailedTrace);
  const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
  const devTrace = useDevTrace();
  const [retrievalModalOpen, setRetrievalModalOpen] = useState(false);

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

  const [docUrls, setDocUrls] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    async function build() {
      const map: Record<string, string> = {};
      for (const item of items ?? []) {
        if (item.type === 'document') {
          const doc = item.data as Document;
          let url: string | undefined;
          // Match source-card logic: PDFs go through citation resolver
          // (presigned URL with #page=N), non-PDFs prefer public sourceUrl.
          const isPdf = !!doc.s3Key && /\.pdf$/i.test(doc.s3Key);
          if (doc.s3Key && (isPdf || !doc.sourceUrl)) {
            url = (await buildResolverUrl(doc.s3Key, doc.startPage)) ?? undefined;
          } else if (doc.sourceUrl) {
            url = doc.sourceUrl;
          }
          if (url) {
            map[doc.documentId] = url;
            // Also key by raw doc_id (strip the trailing -<7char hash>)
            // so inline citations written as doc:<raw-id> resolve correctly.
            const rawId = doc.documentId.replace(/-[a-f0-9]{7}$/, '');
            if (rawId !== doc.documentId) map[rawId] = url;
          }
        } else if (item.type === 'faq') {
          const faq = item.data as FAQ;
          if (faq.sourceUrl) {
            map[faq.faqId] = faq.sourceUrl;
          }
        }
      }
      if (!cancelled) setDocUrls(map);
    }
    build();
    return () => { cancelled = true; };
  }, [items]);

  const memoizedResponse = useMemo(() => {
    if (!response) return null;

    return (
      <div className="chat-response-aligned">
        <StreamResponse content={response} streamingComplete={streamingComplete} docUrls={docUrls} />
        <InlineSources items={items ?? []} streamingComplete={streamingComplete} />
      </div>
    );
  }, [response, streamingComplete, items, docUrls]);

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
                onClick={
                  steps.length > 0 ? () => setStepsOpen(prev => !prev) : undefined
                }
                className={`flex items-center gap-1.5 text-muted-foreground transition-[color,background-color,border-color] ${
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
                          className="flex flex-col gap-1 -ml-[4px]"
                        >
                          <div className="flex items-start gap-2.5">
                            <div
                              className={`mt-[0.45em] h-[7px] w-[7px] shrink-0 rounded-full transition-[color,background-color,border-color] duration-500 ${
                                step.error
                                  ? 'bg-destructive'
                                  : step.missed
                                    ? 'border border-muted-foreground/50 bg-background'
                                    : step.done
                                      ? 'bg-muted-foreground'
                                      : 'border border-muted-foreground/50 bg-background'
                              }`}
                            />
                            <span>
                              <span className={step.missed ? 'text-muted-foreground/70' : undefined}>
                                {step.label}
                              </span>
                              {step.detail && (
                                <span className="block text-muted-foreground/70">
                                  {step.detail}
                                </span>
                              )}
                            </span>
                          </div>
                          {step.devJson && (
                            <details className="ml-5 text-[0.75em] text-muted-foreground/80">
                              <summary className="cursor-pointer select-none">devPayload</summary>
                              <pre className="overflow-auto bg-muted/40 rounded p-2 mt-1 text-xs">
                                {step.devJson}
                              </pre>
                            </details>
                          )}
                        </motion.div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              {devTrace && agentTrace && agentTrace.length > 0 && (
                <button
                  onClick={() => setRetrievalModalOpen(true)}
                  className="mt-2 flex items-center gap-1.5 text-muted-foreground/70 hover:text-foreground transition-colors cursor-pointer"
                  style={{ fontSize: '0.8em' }}
                >
                  <Network className="h-3.5 w-3.5" />
                  <span>View live pipeline</span>
                </button>
              )}
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
      <RetrievalModal queryId={queryId} open={retrievalModalOpen} onClose={() => setRetrievalModalOpen(false)} />
    </motion.div>
  );
}
