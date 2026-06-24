'use client';

import { useEffect, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { useChatStore } from '@/stores/chat-store';
import type { AgentTraceEvent } from '@/stores/types';

interface RetrievalStep {
  id: string;
  title: string;
  subtitle: string;
  status: 'idle' | 'active' | 'complete';
  events: AgentTraceEvent[];
}

function deriveSteps(trace: AgentTraceEvent[]): RetrievalStep[] {
  const steps: RetrievalStep[] = [
    { id: 'faq', title: '1. FAQ Search', subtitle: 'Deterministic FAQ knowledge base lookup', status: 'idle', events: [] },
    { id: 'vector', title: '2. Vector Search', subtitle: 'Semantic similarity over embedded chunks', status: 'idle', events: [] },
    { id: 'graph', title: '3. Graph Expansion', subtitle: 'Traverse neighbors and authority chains', status: 'idle', events: [] },
    { id: 'specialized', title: '4. Specialized Retrieval', subtitle: 'Document search, case law, recency filtering', status: 'idle', events: [] },
    { id: 'synthesis', title: '5. Answer Synthesis', subtitle: 'Assemble evidence and generate response', status: 'idle', events: [] },
  ];

  const toolToStep: Record<string, string> = {
    faq_search: 'faq',
    vector_search: 'vector',
    get_neighbors: 'graph',
    get_authority_chain: 'graph',
    search_document: 'specialized',
    find_case_law: 'specialized',
    fetch_case_opinion: 'specialized',
    list_framework_docs: 'specialized',
    get_document: 'specialized',
    cite_documents: 'synthesis',
    answer: 'synthesis',
  };

  for (const event of trace) {
    if (event.kind === 'tool_call' || event.kind === 'tool_result') {
      const toolName = event.payload.toolName as string;
      const stepId = toolToStep[toolName];
      if (stepId) {
        const step = steps.find(s => s.id === stepId);
        if (step) step.events.push(event);
      }
    }
    if (event.kind === 'reasoning') {
      steps[4].events.push(event);
    }
  }

  const loopComplete = trace.find(e => e.kind === 'loop_complete');

  for (const step of steps) {
    if (step.events.length > 0) {
      const hasResult = step.events.some(e => e.kind === 'tool_result');
      const hasPending = step.events.some(
        e => e.kind === 'tool_call' && e.payload.status === 'pending'
      );
      step.status = hasResult || loopComplete ? 'complete' : hasPending ? 'active' : 'complete';
    }
  }

  if (loopComplete) {
    steps[4].status = 'complete';
  } else if (trace.some(e => e.kind === 'reasoning')) {
    steps[4].status = 'active';
  }

  return steps;
}

// --- Square visualization helpers ---

function ChunkSquares({ total, kept, label }: { total: number; kept: number; label?: string }) {
  const displayTotal = Math.min(total, 60);
  const displayKept = Math.min(kept, displayTotal);
  return (
    <div className="mt-3">
      {label && <p className="text-[10px] text-muted-foreground/70 mb-1.5">{label}</p>}
      <div className="flex flex-wrap gap-[3px]">
        {Array.from({ length: displayTotal }).map((_, i) => (
          <div
            key={i}
            className={`h-[10px] w-[10px] rounded-[2px] transition-colors duration-300 ${
              i < displayKept
                ? 'bg-foreground/70'
                : 'bg-foreground/15'
            }`}
          />
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground/60 mt-1.5">
        {kept} kept / {total} candidates
      </p>
    </div>
  );
}

function AuthoritySquares({ breakdown }: { breakdown: Record<string, number> }) {
  const AUTHORITY_LABELS: Record<string, string> = {
    '1': 'Constitution',
    '2': 'Statutes',
    '3': 'Case Law',
    '4': 'Admin Rules',
    '5': 'WPAM',
    '6': 'FAQs',
    '7': 'Gov Pubs',
    '8': 'IAAO',
    '9': 'USPAP',
  };
  const levels = Object.entries(breakdown)
    .sort(([a], [b]) => Number(a) - Number(b))
    .filter(([, count]) => count > 0);

  if (levels.length === 0) return null;

  return (
    <div className="mt-3 space-y-1.5">
      {levels.map(([level, count]) => (
        <div key={level} className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground/70 w-16 shrink-0 tabular-nums">
            {AUTHORITY_LABELS[level] ?? `L${level}`}
          </span>
          <div className="flex gap-[2px]">
            {Array.from({ length: Math.min(count, 20) }).map((_, i) => (
              <div
                key={i}
                className="h-[10px] w-[10px] rounded-[2px] bg-foreground/70"
                style={{ opacity: 1 - (Number(level) - 1) * 0.08 }}
              />
            ))}
          </div>
          <span className="text-[10px] text-muted-foreground/50">{count}</span>
        </div>
      ))}
    </div>
  );
}

function RelationshipSquares({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 6);

  if (entries.length === 0) return null;

  return (
    <div className="mt-3 space-y-1.5">
      {entries.map(([rel, count]) => (
        <div key={rel} className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground/70 w-20 shrink-0 truncate">
            {rel}
          </span>
          <div className="flex gap-[2px]">
            {Array.from({ length: Math.min(count, 15) }).map((_, i) => (
              <div
                key={i}
                className="h-[10px] w-[10px] rounded-[2px] bg-foreground/60"
              />
            ))}
          </div>
          <span className="text-[10px] text-muted-foreground/50">{count}</span>
        </div>
      ))}
    </div>
  );
}

function DiscoverySquares({ counts }: { counts: Record<string, number> }) {
  const DISCOVERY_COLORS: Record<string, string> = {
    'vector-search': 'bg-foreground/80',
    'graph-neighbor': 'bg-foreground/55',
    'search-document': 'bg-foreground/40',
    'opinion-fetched': 'bg-foreground/30',
    'opinion-backfill': 'bg-foreground/25',
    'fetched': 'bg-foreground/20',
    'framework-list': 'bg-foreground/15',
  };
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a);
  if (entries.length === 0) return null;

  return (
    <div className="mt-3 space-y-1.5">
      {entries.map(([tag, count]) => (
        <div key={tag} className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground/70 w-24 shrink-0 truncate">
            {tag}
          </span>
          <div className="flex gap-[2px]">
            {Array.from({ length: Math.min(count, 12) }).map((_, i) => (
              <div
                key={i}
                className={`h-[10px] w-[10px] rounded-[2px] ${DISCOVERY_COLORS[tag] ?? 'bg-foreground/50'}`}
              />
            ))}
          </div>
          <span className="text-[10px] text-muted-foreground/50">{count}</span>
        </div>
      ))}
    </div>
  );
}

// --- Step-specific visualizations ---

function getMetadataFromEvents(events: AgentTraceEvent[], toolName?: string) {
  const results = events.filter(e => e.kind === 'tool_result' && (!toolName || e.payload.toolName === toolName));
  const merged: Record<string, unknown> = {};
  for (const e of results) {
    const m = e.payload.metadata as Record<string, unknown> | undefined;
    if (m) Object.assign(merged, m);
  }
  return merged;
}

function FAQVisualization({ step }: { step: RetrievalStep }) {
  const m = getMetadataFromEvents(step.events, 'faq_search');
  const faqCount = typeof m.faqCount === 'number' ? m.faqCount : 0;
  const topScore = typeof m.topScore === 'number' ? m.topScore : 0;

  if (faqCount === 0) return <IdleSquares />;

  return (
    <div className="mt-3">
      <div className="flex gap-[3px]">
        {Array.from({ length: faqCount }).map((_, i) => (
          <div
            key={i}
            className={`h-[10px] w-[10px] rounded-[2px] ${
              i === 0 && topScore >= 0.7 ? 'bg-foreground/90' : 'bg-foreground/40'
            }`}
          />
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground/60 mt-1.5">
        {faqCount} FAQ{faqCount !== 1 ? 's' : ''} · top score {topScore.toFixed(2)}
        {topScore >= 0.7 && ' · high confidence'}
      </p>
    </div>
  );
}

function VectorVisualization({ step }: { step: RetrievalStep }) {
  const m = getMetadataFromEvents(step.events, 'vector_search');
  const preDedupCount = typeof m.preDedupCount === 'number' ? m.preDedupCount : 0;
  const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
  const authorityBreakdown = (m.authorityBreakdown as Record<string, number>) ?? {};
  const caseLawCount = typeof m.caseLawCount === 'number' ? m.caseLawCount : 0;

  if (chunkCount === 0 && preDedupCount === 0) return <IdleSquares />;

  return (
    <div>
      {preDedupCount > 0 && (
        <ChunkSquares
          total={preDedupCount}
          kept={chunkCount}
          label="Dedup + diversity filtering"
        />
      )}
      {Object.keys(authorityBreakdown).length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] text-muted-foreground/70 mb-1.5">By authority level</p>
          <AuthoritySquares breakdown={authorityBreakdown} />
        </div>
      )}
      {caseLawCount > 0 && (
        <p className="text-[10px] text-muted-foreground/60 mt-2">
          + {caseLawCount} case law citation{caseLawCount !== 1 ? 's' : ''} discovered
        </p>
      )}
    </div>
  );
}

function GraphVisualization({ step }: { step: RetrievalStep }) {
  const m = getMetadataFromEvents(step.events, 'get_neighbors');
  const relationshipCounts = (m.relationshipCounts as Record<string, number>) ?? {};
  const neighborCount = typeof m.neighborCount === 'number' ? m.neighborCount : 0;
  const chainEvents = step.events.filter(
    e => e.kind === 'tool_result' && e.payload.toolName === 'get_authority_chain'
  );
  const chainLength = chainEvents.length > 0
    ? (chainEvents[0].payload.metadata as Record<string, unknown>)?.chainLength as number ?? 0
    : 0;

  if (neighborCount === 0 && chainLength === 0) return <IdleSquares />;

  return (
    <div>
      {Object.keys(relationshipCounts).length > 0 && (
        <RelationshipSquares counts={relationshipCounts} />
      )}
      {Object.keys(relationshipCounts).length === 0 && neighborCount > 0 && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-[3px]">
            {Array.from({ length: Math.min(neighborCount, 30) }).map((_, i) => (
              <div key={i} className="h-[10px] w-[10px] rounded-[2px] bg-foreground/60" />
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground/60 mt-1.5">
            {neighborCount} neighbor{neighborCount !== 1 ? 's' : ''}
          </p>
        </div>
      )}
      {chainLength > 0 && (
        <div className="mt-2 flex items-center gap-1">
          {Array.from({ length: chainLength }).map((_, i) => (
            <div key={i} className="flex items-center gap-1">
              <div className="h-[10px] w-[10px] rounded-[2px] bg-foreground/60"
                style={{ opacity: 1 - i * 0.15 }}
              />
              {i < chainLength - 1 && (
                <span className="text-[8px] text-muted-foreground/40">→</span>
              )}
            </div>
          ))}
          <span className="text-[10px] text-muted-foreground/60 ml-1">
            authority chain
          </span>
        </div>
      )}
    </div>
  );
}

function SynthesisVisualization({ step, trace }: { step: RetrievalStep; trace: AgentTraceEvent[] }) {
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  const discoveryCounts = (loopComplete?.payload?.discoveryCounts as Record<string, number>) ?? {};
  const citedDocCount = typeof loopComplete?.payload?.citedDocCount === 'number'
    ? loopComplete.payload.citedDocCount : 0;

  if (step.status === 'idle') return <IdleSquares />;
  if (!loopComplete) {
    return (
      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground/70">
        <span className="h-2 w-2 rounded-full bg-foreground/30 animate-pulse" />
        <span>Generating answer...</span>
      </div>
    );
  }

  return (
    <div>
      {Object.keys(discoveryCounts).length > 0 && (
        <DiscoverySquares counts={discoveryCounts} />
      )}
      {citedDocCount > 0 && (
        <p className="text-[10px] text-muted-foreground mt-2 font-medium">
          {citedDocCount} source{citedDocCount !== 1 ? 's' : ''} cited in answer
        </p>
      )}
    </div>
  );
}

function SpecializedVisualization({ step }: { step: RetrievalStep }) {
  const results = step.events.filter(e => e.kind === 'tool_result');
  if (results.length === 0) return <IdleSquares />;

  return (
    <div className="mt-3 space-y-1.5">
      {results.map((e, i) => {
        const toolName = e.payload.toolName as string;
        const status = e.payload.status as string;
        return (
          <div key={i} className="flex items-center gap-2">
            <div className={`h-[10px] w-[10px] rounded-[2px] shrink-0 ${
              status === 'ok' || status === 'terminal' ? 'bg-foreground/70'
                : status === 'miss' ? 'border border-foreground/30'
                  : 'bg-destructive/50'
            }`} />
            <span className="text-[10px] text-muted-foreground truncate">
              {String(e.payload.summary ?? toolName)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function IdleSquares() {
  return (
    <div className="mt-3 flex gap-[3px]">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-[10px] w-[10px] rounded-[2px] bg-muted-foreground/10" />
      ))}
    </div>
  );
}

// --- Main panel ---

function StepPanel({ step, trace }: { step: RetrievalStep; trace: AgentTraceEvent[] }) {
  return (
    <div
      className={`retrieval-step-panel rounded-lg border p-5 transition-all duration-300 ${
        step.status === 'active'
          ? 'border-foreground/30 bg-card shadow-sm'
          : step.status === 'complete'
            ? 'border-border bg-card'
            : 'border-border/50 bg-muted/30'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className={`text-sm font-semibold tracking-tight ${
            step.status === 'idle' ? 'text-muted-foreground/60' : 'text-foreground'
          }`}>
            {step.title}
          </h3>
          <p className="text-[11px] text-muted-foreground mt-0.5">{step.subtitle}</p>
        </div>
        <StepIndicator status={step.status} />
      </div>

      {step.id === 'faq' && step.status !== 'idle' && <FAQVisualization step={step} />}
      {step.id === 'vector' && step.status !== 'idle' && <VectorVisualization step={step} />}
      {step.id === 'graph' && step.status !== 'idle' && <GraphVisualization step={step} />}
      {step.id === 'specialized' && step.status !== 'idle' && <SpecializedVisualization step={step} />}
      {step.id === 'synthesis' && <SynthesisVisualization step={step} trace={trace} />}
      {step.status === 'idle' && step.id !== 'synthesis' && <IdleSquares />}

      {step.status === 'active' && (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground/70">
          <span className="h-1.5 w-1.5 rounded-full bg-foreground/30 animate-pulse shrink-0" />
          <span>Processing...</span>
        </div>
      )}
    </div>
  );
}

function StepIndicator({ status }: { status: 'idle' | 'active' | 'complete' }) {
  if (status === 'idle') {
    return <div className="h-5 w-5 rounded-full border border-border/50 shrink-0" />;
  }
  if (status === 'active') {
    return (
      <div className="h-5 w-5 rounded-full border-2 border-foreground/50 flex items-center justify-center shrink-0">
        <div className="h-2 w-2 rounded-full bg-foreground/50 animate-pulse" />
      </div>
    );
  }
  return (
    <div className="h-5 w-5 rounded-full bg-foreground/10 flex items-center justify-center shrink-0">
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
        <path d="M2 5L4.5 7.5L8 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-foreground/70" />
      </svg>
    </div>
  );
}

function ElapsedBadge({ trace }: { trace: AgentTraceEvent[] }) {
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  if (!loopComplete) return null;
  const ms = loopComplete.payload.elapsedMs as number | undefined;
  if (!ms) return null;
  return (
    <span className="text-xs text-muted-foreground tabular-nums">
      {(ms / 1000).toFixed(1)}s total
    </span>
  );
}

export interface RetrievalModalProps {
  queryId: string;
  open: boolean;
  onClose: () => void;
}

export function RetrievalModal({ queryId, open, onClose }: RetrievalModalProps) {
  const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
  const query = useChatStore(s => s.queries[queryId]?.query);

  const steps = useMemo(() => deriveSteps(agentTrace ?? []), [agentTrace]);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />

          {/* Modal content */}
          <motion.div
            className="relative z-10 w-[calc(100vw-4rem)] max-w-5xl max-h-[calc(100vh-4rem)] overflow-y-auto rounded-xl border border-border bg-card shadow-2xl"
            initial={{ scale: 0.95, opacity: 0, y: 12 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0, y: 12 }}
            transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
          >
            {/* Header */}
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card/95 backdrop-blur-sm px-6 py-4">
              <div className="min-w-0">
                <h2 className="text-base font-semibold tracking-tight">Retrieval Pipeline</h2>
                {query && (
                  <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-md">
                    &ldquo;{query}&rdquo;
                  </p>
                )}
              </div>
              <div className="flex items-center gap-3">
                <ElapsedBadge trace={agentTrace ?? []} />
                <button
                  onClick={onClose}
                  className="rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
                  aria-label="Close"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Steps grid */}
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {steps.map(step => (
                <StepPanel key={step.id} step={step} trace={agentTrace ?? []} />
              ))}
            </div>

            {/* Legend */}
            <div className="px-6 pb-5 flex flex-wrap gap-4 text-[10px] text-muted-foreground/60">
              <span className="flex items-center gap-1.5">
                <span className="h-[10px] w-[10px] rounded-[2px] bg-foreground/70" /> Kept
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-[10px] w-[10px] rounded-[2px] bg-foreground/15" /> Filtered
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-[10px] w-[10px] rounded-[2px] border border-foreground/30" /> Miss
              </span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
