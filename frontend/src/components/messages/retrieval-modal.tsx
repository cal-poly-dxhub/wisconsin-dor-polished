'use client';

import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { useChatStore } from '@/stores/chat-store';
import type { AgentTraceEvent } from '@/stores/types';

// --- Animation helpers ---

function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay, ease: [0.4, 0, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}

function StaggerSquare({ index, className, style }: { index: number; className: string; style?: React.CSSProperties }) {
  return (
    <motion.div
      className={className}
      style={style}
      initial={{ opacity: 0, scale: 0.6 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2, delay: index * 0.015, ease: [0.4, 0, 0.2, 1] }}
    />
  );
}

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
    <FadeIn>
      <div className="mt-3">
        {label && <p className="text-xs text-muted-foreground/70 mb-1.5">{label}</p>}
        <div className="flex flex-wrap gap-1">
          {Array.from({ length: displayTotal }).map((_, i) => (
            <StaggerSquare
              key={i}
              index={i}
              className={`h-3 w-3 rounded-[3px] ${
                i < displayKept
                  ? 'bg-foreground/70'
                  : 'bg-foreground/15'
              }`}
            />
          ))}
        </div>
        <p className="text-xs text-muted-foreground/60 mt-1.5">
          {kept} kept / {total} candidates
        </p>
      </div>
    </FadeIn>
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

  let globalIdx = 0;
  return (
    <div className="mt-3 space-y-1.5">
      {levels.map(([level, count], rowIdx) => (
        <motion.div
          key={level}
          className="flex items-center gap-2"
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: rowIdx * 0.04 }}
        >
          <span className="text-xs text-muted-foreground/70 w-16 shrink-0 tabular-nums">
            {AUTHORITY_LABELS[level] ?? `L${level}`}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: Math.min(count, 20) }).map((_, i) => {
              const idx = globalIdx++;
              return (
                <StaggerSquare
                  key={i}
                  index={idx}
                  className="h-3 w-3 rounded-[3px] bg-foreground/70"
                  style={{ opacity: 1 - (Number(level) - 1) * 0.08 }}
                />
              );
            })}
          </div>
          <span className="text-xs text-muted-foreground/50">{count}</span>
        </motion.div>
      ))}
    </div>
  );
}

function RelationshipSquares({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 6);

  if (entries.length === 0) return null;

  let globalIdx = 0;
  return (
    <div className="mt-3 space-y-1.5">
      {entries.map(([rel, count], rowIdx) => (
        <motion.div
          key={rel}
          className="flex items-center gap-2"
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: rowIdx * 0.04 }}
        >
          <span className="text-xs text-muted-foreground/70 w-20 shrink-0 truncate">
            {rel}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: Math.min(count, 15) }).map((_, i) => {
              const idx = globalIdx++;
              return (
                <StaggerSquare key={i} index={idx} className="h-3 w-3 rounded-[3px] bg-foreground/60" />
              );
            })}
          </div>
          <span className="text-xs text-muted-foreground/50">{count}</span>
        </motion.div>
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

  let globalIdx = 0;
  return (
    <div className="mt-3 space-y-1.5">
      {entries.map(([tag, count], rowIdx) => (
        <motion.div
          key={tag}
          className="flex items-center gap-2"
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: rowIdx * 0.04 }}
        >
          <span className="text-xs text-muted-foreground/70 w-24 shrink-0 truncate">
            {tag}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: Math.min(count, 12) }).map((_, i) => {
              const idx = globalIdx++;
              return (
                <StaggerSquare
                  key={i}
                  index={idx}
                  className={`h-3 w-3 rounded-[3px] ${DISCOVERY_COLORS[tag] ?? 'bg-foreground/50'}`}
                />
              );
            })}
          </div>
          <span className="text-xs text-muted-foreground/50">{count}</span>
        </motion.div>
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

const BAR_CHART_HEIGHT = 180;

function FAQVisualization({ step }: { step: RetrievalStep }) {
  const m = getMetadataFromEvents(step.events, 'faq_search');
  const faqCount = typeof m.faqCount === 'number' ? m.faqCount : 0;
  const topScore = typeof m.topScore === 'number' ? m.topScore : 0;
  const threshold = typeof m.faqScoreThreshold === 'number' ? m.faqScoreThreshold : 0.70;
  const faqScores = Array.isArray(m.faqScores) ? (m.faqScores as number[]) : [];

  if (faqCount === 0) return <IdleSquares />;

  const scores = faqScores.length > 0 ? faqScores : Array.from({ length: faqCount }, (_, i) => i === 0 ? topScore : 0);
  const ceilScore = Math.max(...scores, threshold) * 1.2;
  const thresholdPct = (threshold / ceilScore) * 100;

  return (
    <FadeIn>
      <div className="mt-4">
        <div className="relative w-full overflow-visible" style={{ height: BAR_CHART_HEIGHT }}>
          {/* Bars */}
          <div className="flex items-end gap-1.5 h-full w-full">
            {scores.map((score, i) => {
              const aboveThreshold = score >= threshold;
              const heightPct = (score / ceilScore) * 100;
              return (
                <motion.div
                  key={i}
                  className="relative flex-1 flex flex-col items-center justify-end h-full"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.2, delay: i * 0.04 }}
                >
                  <motion.div
                    className={`w-full rounded-t-[4px] ${
                      aboveThreshold ? 'bg-foreground/75' : 'bg-foreground/20'
                    }`}
                    initial={{ height: 0 }}
                    animate={{ height: `${heightPct}%` }}
                    transition={{ duration: 0.4, delay: i * 0.06, ease: [0.4, 0, 0.2, 1] }}
                  />
                  <span className="mt-1.5 text-[10px] text-muted-foreground/60 tabular-nums">
                    {score.toFixed(2)}
                  </span>
                </motion.div>
              );
            })}
          </div>

          {/* Threshold line — rendered after bars so it sits on top */}
          <motion.div
            className="absolute left-0 right-0 z-10 pointer-events-none"
            style={{ bottom: `${thresholdPct}%` }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4, delay: 0.3 }}
          >
            <div
              className="w-full"
              style={{
                height: '2px',
                backgroundImage: 'repeating-linear-gradient(to right, rgb(248 113 113 / 0.9), rgb(248 113 113 / 0.9) 6px, transparent 6px, transparent 12px)',
              }}
            />
            <span className="absolute top-1.5 right-0 text-[10px] text-red-400 font-medium tabular-nums">
              {threshold.toFixed(2)}
            </span>
          </motion.div>
        </div>
        <p className="text-xs text-muted-foreground/60 mt-3">
          {scores.filter(s => s >= threshold).length} above threshold · {faqCount} total
        </p>
      </div>
    </FadeIn>
  );
}

function ScoreBuckets({ buckets }: { buckets: Record<string, number> }) {
  const BUCKET_ORDER = ['0.9+', '0.8-0.9', '0.7-0.8', '<0.7'];
  const BUCKET_OPACITY: Record<string, string> = {
    '0.9+': 'bg-foreground/90',
    '0.8-0.9': 'bg-foreground/65',
    '0.7-0.8': 'bg-foreground/40',
    '<0.7': 'bg-foreground/20',
  };
  const entries = BUCKET_ORDER.filter(k => buckets[k] && buckets[k] > 0);
  if (entries.length === 0) return null;

  let globalIdx = 0;
  return (
    <div className="space-y-1.5">
          {entries.map((bucket, rowIdx) => (
            <motion.div
              key={bucket}
              className="flex items-center gap-2"
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: rowIdx * 0.04 }}
            >
              <span className="text-xs text-muted-foreground/70 w-14 shrink-0 tabular-nums">{bucket}</span>
              <div className="flex gap-1">
                {Array.from({ length: Math.min(buckets[bucket], 20) }).map((_, i) => {
                  const idx = globalIdx++;
                  return (
                    <StaggerSquare key={i} index={idx} className={`h-3 w-3 rounded-[3px] ${BUCKET_OPACITY[bucket]}`} />
                  );
                })}
              </div>
              <span className="text-xs text-muted-foreground/50">{buckets[bucket]}</span>
            </motion.div>
          ))}
    </div>
  );
}

type VectorBreakdownView = 'scores' | 'authority';

function VectorBreakdownToggle({ scoreBuckets, authorityBreakdown }: { scoreBuckets: Record<string, number>; authorityBreakdown: Record<string, number> }) {
  const [view, setView] = useState<VectorBreakdownView>('scores');
  const hasScores = Object.keys(scoreBuckets).length > 0;
  const hasAuthority = Object.keys(authorityBreakdown).length > 0;

  if (!hasScores && !hasAuthority) return null;

  return (
    <FadeIn delay={0.1}>
      <div className="mt-3">
        <select
          value={view}
          onChange={e => setView(e.target.value as VectorBreakdownView)}
          className="mb-3 text-xs bg-muted/40 border border-border/80 rounded-lg px-3 py-2.5 text-foreground/80 font-medium cursor-pointer shadow-sm focus:outline-none focus:ring-1 focus:ring-primary/30 transition-colors hover:bg-muted/60"
        >
          {hasScores && <option value="scores">Score distribution</option>}
          {hasAuthority && <option value="authority">Authority level</option>}
        </select>

        <div className="pt-1">
          {view === 'scores' && hasScores && <ScoreBuckets buckets={scoreBuckets} />}
          {view === 'authority' && hasAuthority && <AuthoritySquares breakdown={authorityBreakdown} />}
        </div>
      </div>
    </FadeIn>
  );
}

const DIVERSITY_CAP = 5;

function DocChunkGroups({ docChunks, preDedupCount, chunkCount }: { docChunks: Record<string, number>; preDedupCount: number; chunkCount: number }) {
  const entries = Object.entries(docChunks).sort(([, a], [, b]) => b - a);
  if (entries.length === 0) return null;

  return (
    <FadeIn>
      <div className="mt-3">
        <p className="text-xs text-muted-foreground/70 mb-3">
          {preDedupCount} candidates → {chunkCount} kept (cap {DIVERSITY_CAP}/doc)
        </p>
        <div className="space-y-4">
          {entries.map(([docId, count], groupIdx) => {
            const atCap = count >= DIVERSITY_CAP;
            const shortId = docId.length > 32 ? docId.slice(0, 32) + '…' : docId;
            return (
              <motion.div
                key={docId}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2, delay: groupIdx * 0.05 }}
              >
                <p className={`text-[11px] mb-2 truncate ${atCap ? 'text-foreground/70 font-medium' : 'text-muted-foreground/60'}`}>
                  {shortId}
                  {atCap && <span className="text-muted-foreground/50 font-normal"> (capped)</span>}
                </p>
                <div className="flex gap-1">
                  {Array.from({ length: count }).map((_, i) => (
                    <StaggerSquare
                      key={i}
                      index={i}
                      className={`h-3 w-3 rounded-[3px] ${atCap ? 'bg-foreground/70' : 'bg-foreground/45'}`}
                    />
                  ))}
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </FadeIn>
  );
}

function VectorVisualization({ step }: { step: RetrievalStep }) {
  const m = getMetadataFromEvents(step.events, 'vector_search');
  const preDedupCount = typeof m.preDedupCount === 'number' ? m.preDedupCount : 0;
  const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
  const authorityBreakdown = (m.authorityBreakdown as Record<string, number>) ?? {};
  const caseLawCount = typeof m.caseLawCount === 'number' ? m.caseLawCount : 0;
  const autoEnrichedCount = typeof m.autoEnrichedCount === 'number' ? m.autoEnrichedCount : 0;
  const scoreBuckets = (m.scoreBuckets as Record<string, number>) ?? {};
  const targetWpamYear = typeof m.targetWpamYear === 'number' ? m.targetWpamYear : null;
  const docChunks = (m.docChunks as Record<string, number>) ?? {};

  if (chunkCount === 0 && preDedupCount === 0) return <IdleSquares />;

  const hasDocChunks = Object.keys(docChunks).length > 0;

  return (
    <div>
      {hasDocChunks ? (
        <DocChunkGroups docChunks={docChunks} preDedupCount={preDedupCount} chunkCount={chunkCount} />
      ) : preDedupCount > 0 ? (
        <ChunkSquares total={preDedupCount} kept={chunkCount} label="Dedup + diversity filtering" />
      ) : null}
      {targetWpamYear && (
        <FadeIn delay={0.15}>
          <p className="text-xs text-muted-foreground/60 mt-2">
            Filtered to WPAM {targetWpamYear} edition
          </p>
        </FadeIn>
      )}
      <VectorBreakdownToggle scoreBuckets={scoreBuckets} authorityBreakdown={authorityBreakdown} />
      {autoEnrichedCount > 0 && (
        <FadeIn delay={0.25}>
          <p className="text-xs text-muted-foreground/60 mt-2">
            + {autoEnrichedCount} graph neighbor{autoEnrichedCount !== 1 ? 's' : ''} auto-enriched
          </p>
        </FadeIn>
      )}
      {caseLawCount > 0 && (
        <FadeIn delay={0.3}>
          <p className="text-xs text-muted-foreground/60 mt-1">
            + {caseLawCount} case law citation{caseLawCount !== 1 ? 's' : ''} discovered
          </p>
        </FadeIn>
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
        <FadeIn>
          <RelationshipSquares counts={relationshipCounts} />
        </FadeIn>
      )}
      {Object.keys(relationshipCounts).length === 0 && neighborCount > 0 && (
        <FadeIn>
          <div className="mt-3">
            <div className="flex flex-wrap gap-1">
              {Array.from({ length: Math.min(neighborCount, 30) }).map((_, i) => (
                <StaggerSquare key={i} index={i} className="h-3 w-3 rounded-[3px] bg-foreground/60" />
              ))}
            </div>
            <p className="text-xs text-muted-foreground/60 mt-1.5">
              {neighborCount} neighbor{neighborCount !== 1 ? 's' : ''}
            </p>
          </div>
        </FadeIn>
      )}
      {chainLength > 0 && (
        <FadeIn delay={0.1}>
          <div className="mt-2 flex items-center gap-1">
            {Array.from({ length: chainLength }).map((_, i) => (
              <motion.div
                key={i}
                className="flex items-center gap-1"
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2, delay: i * 0.08 }}
              >
                <div className="h-3 w-3 rounded-[3px] bg-foreground/60"
                  style={{ opacity: 1 - i * 0.15 }}
                />
                {i < chainLength - 1 && (
                  <span className="text-xs text-muted-foreground/40">→</span>
                )}
              </motion.div>
            ))}
            <span className="text-xs text-muted-foreground/60 ml-1">
              authority chain
            </span>
          </div>
        </FadeIn>
      )}
    </div>
  );
}

function SynthesisVisualization({ step, trace }: { step: RetrievalStep; trace: AgentTraceEvent[] }) {
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  const discoveryCounts = (loopComplete?.payload?.discoveryCounts as Record<string, number>) ?? {};
  const discoveryTitles = (loopComplete?.payload?.discoveryTitles as Record<string, string>) ?? {};
  const citedDocCount = typeof loopComplete?.payload?.citedDocCount === 'number'
    ? loopComplete.payload.citedDocCount : 0;

  if (step.status === 'idle') return <IdleSquares />;
  if (!loopComplete) {
    return (
      <motion.div
        className="mt-3 flex items-center gap-2 text-xs text-muted-foreground/70"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3 }}
      >
        <span className="h-2 w-2 rounded-full bg-foreground/30 animate-pulse" />
        <span>Generating answer...</span>
      </motion.div>
    );
  }

  const titleEntries = Object.entries(discoveryTitles).slice(0, 10);

  return (
    <FadeIn>
      <div>
        {Object.keys(discoveryCounts).length > 0 && (
          <DiscoverySquares counts={discoveryCounts} />
        )}
        {titleEntries.length > 0 && (
          <div className="mt-3">
            <p className="text-xs text-muted-foreground/70 mb-1.5">Cited sources</p>
            <div className="space-y-1">
              {titleEntries.map(([docId, title], i) => (
                <motion.div
                  key={docId}
                  className="flex items-center gap-2"
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.2, delay: i * 0.03 }}
                >
                  <div className="h-3 w-3 rounded-[3px] bg-foreground/70 shrink-0" />
                  <span className="text-xs text-muted-foreground truncate">{title}</span>
                </motion.div>
              ))}
            </div>
          </div>
        )}
        {citedDocCount > 0 && (
          <motion.p
            className="text-xs text-muted-foreground mt-2 font-medium"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3, delay: 0.2 }}
          >
            {citedDocCount} source{citedDocCount !== 1 ? 's' : ''} cited in answer
          </motion.p>
        )}
      </div>
    </FadeIn>
  );
}

function SpecializedVisualization({ step }: { step: RetrievalStep }) {
  const results = step.events.filter(e => e.kind === 'tool_result');
  if (results.length === 0) return <IdleSquares />;

  type CollapsedEntry = { summary: string; status: string; count: number; totalChunks: number; totalMs: number };
  const collapsed: CollapsedEntry[] = [];
  for (const e of results) {
    const status = e.payload.status as string;
    const summary = String(e.payload.summary ?? (e.payload.toolName as string));
    const meta = e.payload.metadata as Record<string, unknown> | undefined;
    const docId = meta?.['docId'] as string | undefined;
    const chunkCount = typeof meta?.['chunkCount'] === 'number' ? meta['chunkCount'] as number : 0;
    const elapsedMs = typeof meta?.['elapsedMs'] === 'number' ? meta['elapsedMs'] as number : 0;
    const prev = collapsed[collapsed.length - 1];
    if (prev && docId && prev.summary === summary) {
      prev.count += 1;
      prev.totalChunks += chunkCount;
      prev.totalMs += elapsedMs;
      continue;
    }
    collapsed.push({ summary, status, count: 1, totalChunks: chunkCount, totalMs: elapsedMs });
  }

  return (
    <div className="mt-3 space-y-1.5">
      {collapsed.map((entry, i) => (
        <motion.div
          key={i}
          className="flex items-center gap-2"
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: i * 0.04 }}
        >
          <div className={`h-3 w-3 rounded-[3px] shrink-0 ${
            entry.status === 'ok' || entry.status === 'terminal' ? 'bg-foreground/70'
              : entry.status === 'miss' ? 'border border-foreground/30'
                : 'bg-destructive/50'
          }`} />
          <span className="text-xs text-muted-foreground truncate">
            {entry.summary}
            {entry.count > 1 && (
              <span className="text-muted-foreground/60"> ×{entry.count} · {entry.totalChunks} chunks</span>
            )}
          </span>
        </motion.div>
      ))}
    </div>
  );
}

function IdleSquares() {
  return (
    <div className="mt-3 flex gap-1">
      {Array.from({ length: 8 }).map((_, i) => (
        <motion.div
          key={i}
          className="h-3 w-3 rounded-[3px] bg-muted-foreground/10"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2, delay: i * 0.02 }}
        />
      ))}
    </div>
  );
}

// --- Main panel ---

function StepPanel({ step, trace }: { step: RetrievalStep; trace: AgentTraceEvent[] }) {
  return (
    <div
      className={`retrieval-step-panel h-full rounded-lg border p-6 transition-all duration-300 ${
        step.status === 'active'
          ? 'border-foreground/30 bg-card shadow-sm'
          : step.status === 'complete'
            ? 'border-border bg-card'
            : 'border-border/50 bg-muted/30'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className={`text-base font-semibold tracking-tight ${
            step.status === 'idle' ? 'text-muted-foreground/60' : 'text-foreground'
          }`}>
            {step.title}
          </h3>
          <p className="text-sm text-muted-foreground mt-0.5">{step.subtitle}</p>
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
        <motion.div
          className="mt-2 flex items-center gap-2 text-xs text-muted-foreground/70"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-foreground/30 animate-pulse shrink-0" />
          <span>Processing...</span>
        </motion.div>
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
      <motion.div
        className="h-5 w-5 rounded-full border-2 border-foreground/50 flex items-center justify-center shrink-0"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        <div className="h-2 w-2 rounded-full bg-foreground/50 animate-pulse" />
      </motion.div>
    );
  }
  return (
    <motion.div
      className="h-5 w-5 rounded-full bg-foreground/10 flex items-center justify-center shrink-0"
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.2 }}
    >
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
        <path d="M2 5L4.5 7.5L8 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-foreground/70" />
      </svg>
    </motion.div>
  );
}

function ElapsedBadge({ trace }: { trace: AgentTraceEvent[] }) {
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  if (!loopComplete) return null;
  const ms = loopComplete.payload.elapsedMs as number | undefined;
  if (!ms) return null;
  return (
    <motion.span
      className="text-xs text-muted-foreground tabular-nums"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {(ms / 1000).toFixed(1)}s total
    </motion.span>
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

  const modalContent = (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 overflow-y-auto bg-background"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
          transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
        >
            {/* Header */}
            <div className="sticky top-0 z-10 border-b border-border bg-card/95 backdrop-blur-sm px-6 py-5">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-3">
                    <h2 className="text-lg font-semibold tracking-tight">Retrieval Pipeline</h2>
                    <ElapsedBadge trace={agentTrace ?? []} />
                  </div>
                  {query && (
                    <p className="mt-2 text-base text-foreground/80 font-medium leading-snug">
                      &ldquo;{query}&rdquo;
                    </p>
                  )}
                </div>
                <button
                  onClick={onClose}
                  className="rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer shrink-0"
                  aria-label="Close"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>

            {/* Steps grid */}
            <div className="p-6 grid grid-cols-[repeat(auto-fill,minmax(400px,1fr))] gap-4">
              {steps.map((step, i) => (
                <motion.div
                  key={step.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.25, delay: i * 0.05, ease: [0.4, 0, 0.2, 1] }}
                >
                  <StepPanel step={step} trace={agentTrace ?? []} />
                </motion.div>
              ))}
            </div>

            {/* Legend */}
            <div className="px-6 pb-6 flex flex-wrap gap-5 text-xs text-muted-foreground/60">
              <span className="flex items-center gap-2">
                <span className="h-3 w-3 rounded-[3px] bg-foreground/70" /> Kept
              </span>
              <span className="flex items-center gap-2">
                <span className="h-3 w-3 rounded-[3px] bg-foreground/15" /> Filtered
              </span>
              <span className="flex items-center gap-2">
                <span className="h-3 w-3 rounded-[3px] border border-foreground/30" /> Miss
              </span>
            </div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  if (typeof document === 'undefined') return null;
  return createPortal(modalContent, document.body);
}
