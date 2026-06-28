'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

// --- Card data model ---

interface PipelineCard {
  id: string;
  toolName: string;
  summary: string;
  callSummary: string;
  status: 'pending' | 'complete' | 'miss' | 'error';
  callPayload: Record<string, unknown>;
  resultPayload: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

type PipelineItem =
  | { type: 'card'; card: PipelineCard };

const TOOL_TITLES: Record<string, string> = {
  reasoning: 'Thinking',
  faq_search: 'FAQ Search',
  refine_query: 'Query Refinement',
  vector_search: 'Vector Search',
  search_document: 'Document Search',
  list_sections: 'List Sections',
  get_section: 'Get Section',
  get_document: 'Get Document',
  get_neighbors: 'Graph Neighbors',
  get_authority_chain: 'Authority Chain',
  list_framework_docs: 'Framework Documents',
  find_case_law: 'Case Law Search',
  fetch_case_opinion: 'Fetch Case Opinion',
  prepare_answer: 'Answer Synthesis',
  answer: 'Answer Synthesis',
  cite_documents: 'Cite Documents',
};

function stripQuotes(s: string): string {
  const t = s.trim();
  if (
    (t.startsWith('"') && t.endsWith('"')) ||
    (t.startsWith('\u201c') && t.endsWith('\u201d'))
  ) {
    return t.slice(1, -1);
  }
  return t;
}

function Emphasis({ children }: { children: React.ReactNode }) {
  return <span className="text-foreground font-semibold">{children}</span>;
}

function ActionLead({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-muted-foreground leading-relaxed mb-4">
      {children}
    </p>
  );
}

function CardContextLine({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const call = card.callSummary;

  switch (card.toolName) {
    case 'refine_query': {
      const refined = typeof m.refinedQuery === 'string' ? m.refinedQuery : '';
      const original = stripQuotes(call);
      if (refined && card.status === 'complete') {
        return (
          <ActionLead>
            Rewriting the query to <Emphasis>&ldquo;{refined}&rdquo;</Emphasis>
          </ActionLead>
        );
      }
      if (original) {
        return (
          <ActionLead>
            Rewriting the query <Emphasis>&ldquo;{original}&rdquo;</Emphasis>
          </ActionLead>
        );
      }
      return null;
    }
    case 'faq_search': {
      const query = stripQuotes(call);
      if (!query) return null;
      return (
        <ActionLead>
          Searching FAQs for <Emphasis>&ldquo;{query}&rdquo;</Emphasis>
        </ActionLead>
      );
    }
    case 'vector_search': {
      const query = stripQuotes(call);
      if (!query) return null;
      return (
        <ActionLead>
          Searching the knowledge graph for <Emphasis>&ldquo;{query}&rdquo;</Emphasis>
        </ActionLead>
      );
    }
    case 'search_document': {
      const docTitle = typeof m.docTitle === 'string' ? m.docTitle : '';
      const inMatch = call.match(/^"(.+)" in (.+)$/);
      if (inMatch) {
        return (
          <ActionLead>
            Searching <Emphasis>&ldquo;{inMatch[1]}&rdquo;</Emphasis> in{' '}
            <Emphasis>{inMatch[2]}</Emphasis>
          </ActionLead>
        );
      }
      if (docTitle && call) {
        return (
          <ActionLead>
            Searching <Emphasis>&ldquo;{stripQuotes(call)}&rdquo;</Emphasis> in{' '}
            <Emphasis>{docTitle}</Emphasis>
          </ActionLead>
        );
      }
      if (docTitle) {
        return (
          <ActionLead>
            Searching in <Emphasis>{docTitle}</Emphasis>
          </ActionLead>
        );
      }
      return null;
    }
    case 'list_sections': {
      const docTitle =
        (typeof m.docTitle === 'string' ? m.docTitle : '') || call;
      if (!docTitle) return null;
      return (
        <ActionLead>
          Listing sections in <Emphasis>{docTitle}</Emphasis>
        </ActionLead>
      );
    }
    case 'get_section': {
      const heading = typeof m.heading === 'string' ? m.heading : '';
      const docTitle = typeof m.docTitle === 'string' ? m.docTitle : '';
      const query = typeof m.query === 'string' ? m.query : '';
      if (heading || docTitle || query) {
        return (
          <ActionLead>
            Searching {heading && <Emphasis>{heading}</Emphasis>}
            {docTitle && <> in {docTitle}</>}
            {query && (
              <> for <Emphasis>&ldquo;{query}&rdquo;</Emphasis></>
            )}
          </ActionLead>
        );
      }
      const fromMatch = call.match(/^"(.+)" from (.+)$/);
      if (fromMatch) {
        return (
          <ActionLead>
            Searching <Emphasis>{fromMatch[1]}</Emphasis> in {fromMatch[2]}
          </ActionLead>
        );
      }
      return null;
    }
    case 'get_neighbors': {
      const source = call;
      if (!source) return null;
      return (
        <ActionLead>
          Exploring neighbors of <Emphasis>{source}</Emphasis>
        </ActionLead>
      );
    }
    case 'get_authority_chain': {
      const source = call;
      if (!source) return null;
      return (
        <ActionLead>
          Tracing authority from <Emphasis>{source}</Emphasis>
        </ActionLead>
      );
    }
    case 'get_document': {
      const target = call;
      if (!target) return null;
      return (
        <ActionLead>
          Fetching <Emphasis>{target}</Emphasis>
        </ActionLead>
      );
    }
    case 'list_framework_docs': {
      const framework = call;
      if (!framework) return null;
      return (
        <ActionLead>
          Listing documents in <Emphasis>{framework}</Emphasis>
        </ActionLead>
      );
    }
    case 'find_case_law': {
      const query = stripQuotes(call) || call;
      if (!query) return null;
      return (
        <ActionLead>
          Searching case law for <Emphasis>&ldquo;{query}&rdquo;</Emphasis>
        </ActionLead>
      );
    }
    case 'fetch_case_opinion': {
      const citation = call;
      if (!citation) return null;
      return (
        <ActionLead>
          Fetching opinion for <Emphasis>{citation}</Emphasis>
        </ActionLead>
      );
    }
    default:
      return null;
  }
}

function deriveItems(trace: AgentTraceEvent[]): PipelineItem[] {
  const items: PipelineItem[] = [];
  const pendingCalls = new Map<string, PipelineCard>();

  for (const event of trace) {
    if (event.kind === 'reasoning') {
      const text = String(event.payload.text ?? '');
      if (text) {
        const card: PipelineCard = {
          id: `thinking-${event.seq}`,
          toolName: 'reasoning',
          summary: text,
          callSummary: '',
          status: 'complete',
          callPayload: {},
          resultPayload: null,
          metadata: {},
        };
        items.push({ type: 'card', card });
      }
      continue;
    }

    if (event.kind === 'tool_call') {
      const toolName = String(event.payload.toolName ?? '');
      const summary = String(event.payload.summary ?? '');
      const card: PipelineCard = {
        id: `card-${event.seq}`,
        toolName,
        summary,
        callSummary: summary,
        status: 'pending',
        callPayload: event.payload,
        resultPayload: null,
        metadata: {},
      };
      pendingCalls.set(`${toolName}-${event.seq}`, card);
      items.push({ type: 'card', card });
      continue;
    }

    if (event.kind === 'tool_result') {
      const toolName = String(event.payload.toolName ?? '');
      const status = String(event.payload.status ?? 'ok');
      const metadata = (event.payload.metadata as Record<string, unknown>) ?? {};

      let matched = false;
      for (const [key, card] of pendingCalls) {
        if (card.toolName === toolName && card.status === 'pending') {
          card.status = status === 'ok' || status === 'terminal' ? 'complete' : status === 'miss' ? 'miss' : 'error';
          card.resultPayload = event.payload;
          card.metadata = metadata;
          if (event.payload.summary) card.summary = String(event.payload.summary);
          pendingCalls.delete(key);
          matched = true;
          break;
        }
      }

      if (!matched) {
        const card: PipelineCard = {
          id: `card-result-${event.seq}`,
          toolName,
          summary: String(event.payload.summary ?? ''),
          callSummary: '',
          status: status === 'ok' || status === 'terminal' ? 'complete' : status === 'miss' ? 'miss' : 'error',
          callPayload: {},
          resultPayload: event.payload,
          metadata,
        };
        items.push({ type: 'card', card });
      }
    }
  }

  return items;
}

// --- Visualization components per tool type ---

const BAR_CHART_HEIGHT = 160;

function FAQCardViz({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const faqCount = typeof m.faqCount === 'number' ? m.faqCount : 0;
  const topScore = typeof m.topScore === 'number' ? m.topScore : 0;
  const threshold = typeof m.faqScoreThreshold === 'number' ? m.faqScoreThreshold : 0.70;
  const faqScores = Array.isArray(m.faqScores) ? (m.faqScores as number[]) : [];
  const topFaqSnippet = typeof m.topFaqSnippet === 'string' ? m.topFaqSnippet : '';

  if (faqCount === 0 && card.status === 'complete') {
    return <p className="text-xs text-muted-foreground/60 mt-2">No FAQ matches</p>;
  }
  if (card.status === 'pending') return null;

  const scores = faqScores.length > 0 ? faqScores : Array.from({ length: faqCount }, (_, i) => i === 0 ? topScore : 0);
  const ceilScore = Math.max(...scores, threshold) * 1.2;
  const thresholdPct = (threshold / ceilScore) * 100;

  return (
    <FadeIn>
      <div>
        {topFaqSnippet && (
          <p className="text-xs text-foreground/70 mb-3 leading-relaxed line-clamp-2">
            {topFaqSnippet}
          </p>
        )}
        <div className="relative w-full overflow-visible" style={{ height: BAR_CHART_HEIGHT }}>
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

const DIVERSITY_CAP = 5;

const DOC_TYPE_COLORS: Record<string, { bg: string; label: string }> = {
  statutes: { bg: 'bg-blue-500', label: 'Statute' },
  'case-law': { bg: 'bg-purple-500', label: 'Case Law' },
  'admin-rules': { bg: 'bg-teal-500', label: 'Admin Rule' },
  wpam: { bg: 'bg-green-500', label: 'WPAM' },
  'faq-pages': { bg: 'bg-yellow-500', label: 'FAQ' },
  faq: { bg: 'bg-yellow-500', label: 'FAQ' },
  'gov-publications': { bg: 'bg-orange-500', label: 'Gov. Pub.' },
  'gov-pubs': { bg: 'bg-orange-500', label: 'Gov. Pub.' },
  iaao: { bg: 'bg-rose-500', label: 'IAAO' },
  uspap: { bg: 'bg-rose-400', label: 'USPAP' },
  constitution: { bg: 'bg-indigo-500', label: 'Constitution' },
};

function docTypeFromId(docId: string): { bg: string; label: string } {
  for (const [prefix, meta] of Object.entries(DOC_TYPE_COLORS)) {
    if (docId.startsWith(prefix)) return meta;
  }
  return { bg: 'bg-foreground/50', label: 'Other' };
}

function VectorCardViz({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const preDedupCount = typeof m.preDedupCount === 'number' ? m.preDedupCount : 0;
  const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
  const caseLawCount = typeof m.caseLawCount === 'number' ? m.caseLawCount : 0;
  const autoEnrichedCount = typeof m.autoEnrichedCount === 'number' ? m.autoEnrichedCount : 0;
  const targetWpamYear = typeof m.targetWpamYear === 'number' ? m.targetWpamYear : null;
  const docChunks = (m.docChunks as Record<string, number>) ?? {};

  if (chunkCount === 0 && preDedupCount === 0 && card.status !== 'pending') {
    return <p className="text-xs text-muted-foreground/60 mt-2">No results</p>;
  }
  if (card.status === 'pending') return null;

  const hasDocChunks = Object.keys(docChunks).length > 0;

  // Build flat grid of squares colored by doc type
  const squares: { bg: string; label: string }[] = [];
  if (hasDocChunks) {
    const entries = Object.entries(docChunks).sort(([, a], [, b]) => b - a);
    for (const [docId, count] of entries) {
      const meta = docTypeFromId(docId);
      for (let i = 0; i < count; i++) squares.push(meta);
    }
  }

  // Derive legend from what's actually shown
  const legendEntries: { bg: string; label: string }[] = [];
  const seenLabels = new Set<string>();
  for (const sq of squares) {
    if (!seenLabels.has(sq.label)) {
      seenLabels.add(sq.label);
      legendEntries.push(sq);
    }
  }

  return (
    <div>
      {hasDocChunks ? (
        <FadeIn>
          <div>
            <p className="text-xs text-muted-foreground/70 mb-3">
              {preDedupCount > 0 ? `${preDedupCount} candidates → ` : ''}{chunkCount} kept (cap {DIVERSITY_CAP}/doc)
            </p>
            <div className="flex flex-wrap gap-1">
              {squares.map((sq, i) => (
                <StaggerSquare
                  key={i}
                  index={i}
                  className={`h-3.5 w-3.5 rounded-[3px] ${sq.bg}`}
                />
              ))}
            </div>
            {legendEntries.length > 1 && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3">
                {legendEntries.map(entry => (
                  <span key={entry.label} className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
                    <span className={`h-2.5 w-2.5 rounded-[2px] ${entry.bg}`} />
                    {entry.label}
                  </span>
                ))}
              </div>
            )}
            {(() => {
              const topDocs = Object.entries(docChunks).sort(([, a], [, b]) => b - a).slice(0, 3);
              if (topDocs.length === 0) return null;
              return (
                <div className="mt-3 space-y-1">
                  {topDocs.map(([docId, count]) => {
                    const meta = docTypeFromId(docId);
                    const shortId = docId.length > 36 ? docId.slice(0, 36) + '…' : docId;
                    return (
                      <div key={docId} className="flex items-center gap-2">
                        <span className={`h-2.5 w-2.5 rounded-[2px] shrink-0 ${meta.bg}`} />
                        <span className="text-xs text-muted-foreground truncate">{shortId}</span>
                        <span className="text-xs text-muted-foreground/50 tabular-nums shrink-0">{count}</span>
                      </div>
                    );
                  })}
                  {Object.keys(docChunks).length > 3 && (
                    <p className="text-xs text-muted-foreground/50 italic pl-[18px]">
                      +{Object.keys(docChunks).length - 3} more sources
                    </p>
                  )}
                </div>
              );
            })()}
          </div>
        </FadeIn>
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

interface ChunkScore {
  chunkId: string;
  cosine: number;
  zScore: number | null;
  heading: string;
  subheading: string;
  startPage: number | null;
  endPage: number | null;
  included?: boolean;
}

function GetSectionViz({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const sectionChunkCount = typeof m.sectionChunkCount === 'number' ? m.sectionChunkCount : 0;
  const returnedChunkCount = typeof m.returnedChunkCount === 'number' ? m.returnedChunkCount : 0;
  const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
  const latencyMs = typeof m.latencyMs === 'number' ? m.latencyMs : 0;
  const chunkScores = Array.isArray(m.chunkScores) ? (m.chunkScores as ChunkScore[]) : [];

  if (card.status === 'pending') return null;

  const hasRanking = chunkScores.length > 0;
  const GRID_CELLS = 15;
  const maxZ = hasRanking
    ? Math.max(...chunkScores.map(c => c.zScore ?? 0), 1)
    : 1;

  return (
    <FadeIn>
      <div>
        {hasRanking ? (
          <div>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}>
              {Array.from({ length: GRID_CELLS }).map((_, i) => {
                const chunk = chunkScores[i];
                if (!chunk) {
                  return (
                    <motion.div
                      key={`empty-${i}`}
                      className="rounded-[4px] border border-dashed border-muted-foreground/20 bg-muted/30"
                      style={{ minHeight: '64px' }}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.2, delay: i * 0.02 }}
                    />
                  );
                }
                const z = chunk.zScore ?? 0;
                const saturation = Math.min(Math.max(z / maxZ, 0.15), 1);
                const included = chunk.included !== false;
                return (
                  <motion.div
                    key={chunk.chunkId || i}
                    className={`rounded-[4px] p-1.5 flex flex-col overflow-hidden ${
                      included
                        ? 'border border-foreground/20'
                        : 'border border-dashed border-muted-foreground/30'
                    }`}
                    style={{
                      minHeight: '64px',
                      backgroundColor: included
                        ? `hsl(142 60% 45% / ${saturation * 0.35})`
                        : 'hsl(0 0% 50% / 0.08)',
                    }}
                    initial={{ opacity: 0, scale: 0.6 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 0.2, delay: i * 0.03, ease: [0.4, 0, 0.2, 1] }}
                  >
                    <span className="text-[10px] font-semibold text-foreground/80 leading-none">
                      {i + 1}
                    </span>
                    <div className="mt-auto">
                      <span className="block text-xs tabular-nums text-foreground/70 font-medium leading-tight">
                        {chunk.cosine.toFixed(3)}
                      </span>
                      {chunk.zScore !== null && (
                        <span className={`block text-xs tabular-nums font-medium leading-tight ${
                          included ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground/60'
                        }`}>
                          z{chunk.zScore >= 0 ? '+' : ''}{chunk.zScore.toFixed(1)}
                        </span>
                      )}
                    </div>
                  </motion.div>
                );
              })}
            </div>
            <p className="text-sm font-medium text-foreground mt-3">
              {returnedChunkCount} of {sectionChunkCount} chunks passed z-score filter
            </p>
          </div>
        ) : (
          chunkCount > 0 && (
            <p className="text-xs text-muted-foreground/60 mt-1">
              {chunkCount} chunk{chunkCount !== 1 ? 's' : ''} retrieved
            </p>
          )
        )}
        {latencyMs > 0 && (
          <p className="text-xs text-muted-foreground/50 mt-1">{latencyMs}ms</p>
        )}
      </div>
    </FadeIn>
  );
}

function GraphNeighborsViz({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const neighborCount = typeof m.neighborCount === 'number' ? m.neighborCount : 0;
  const neighborEdges = Array.isArray(m.neighborEdges)
    ? (m.neighborEdges as { title: string; relationship: string }[])
    : [];
  const neighborTitles = Array.isArray(m.neighborTitles) ? (m.neighborTitles as string[]) : [];

  if (neighborCount === 0 && card.status !== 'pending') {
    return <p className="text-xs text-muted-foreground/60 mt-2">No neighbors found</p>;
  }
  if (card.status === 'pending') return null;

  const hasEdges = neighborEdges.length > 0;
  const items = hasEdges
    ? neighborEdges.map(e => ({ title: e.title, edge: e.relationship }))
    : neighborTitles.map(t => ({ title: t, edge: '' }));

  if (items.length === 0 && neighborCount > 0) {
    return (
      <FadeIn>
        <div className="mt-2">
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
    );
  }

  return (
    <motion.div
      className="flex-1 flex flex-col -mx-6 -mb-6"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
    >
      {neighborCount > items.length && (
        <p className="text-[10px] text-muted-foreground/50 px-6 mb-2">
          {items.length} of {neighborCount}
        </p>
      )}
      <div className="grid grid-cols-2 border-t border-border/60 flex-1">
        {items.map((item, i) => (
          <motion.div
            key={i}
            className="flex flex-col items-center justify-center px-2 py-3 border-border/60 [&:not(:nth-child(-n+2))]:border-t [&:nth-child(odd)]:border-r"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15, delay: i * 0.025 }}
          >
            <span className="text-xs text-muted-foreground text-center leading-tight line-clamp-2">
              {item.title}
            </span>
            {item.edge && (
              <span className="text-[11px] text-muted-foreground/50 uppercase tracking-wide mt-1">
                {item.edge}
              </span>
            )}
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}

function AuthorityChainViz({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const chainLength = typeof m.chainLength === 'number' ? m.chainLength : 0;

  if (chainLength === 0 && card.status !== 'pending') {
    return <p className="text-xs text-muted-foreground/60 mt-2">No chain found</p>;
  }
  if (card.status === 'pending') return null;

  return (
    <FadeIn>
      <div className="flex items-center gap-1">
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
          {chainLength} step{chainLength !== 1 ? 's' : ''}
        </span>
      </div>
    </FadeIn>
  );
}

function SynthesisViz({ trace }: { trace: AgentTraceEvent[] }) {
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  const discoveryCounts = (loopComplete?.payload?.discoveryCounts as Record<string, number>) ?? {};
  const discoveryTitles = (loopComplete?.payload?.discoveryTitles as Record<string, string>) ?? {};
  const citedDocCount = typeof loopComplete?.payload?.citedDocCount === 'number'
    ? loopComplete.payload.citedDocCount : 0;

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
      <div className="mt-2">
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

function GenericToolViz({ card }: { card: PipelineCard }) {
  if (card.status === 'pending') return null;
  const m = card.metadata;
  const parts: string[] = [];

  const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
  const docCount = typeof m.docCount === 'number' ? m.docCount : 0;
  const documentCount = typeof m.documentCount === 'number' ? m.documentCount : 0;
  const latencyMs = typeof m.latencyMs === 'number' ? m.latencyMs : (typeof m.elapsedMs === 'number' ? (m.elapsedMs as number) : 0);
  const opinionChars = typeof m.opinionChars === 'number' ? m.opinionChars : 0;

  if (chunkCount > 0) parts.push(`${chunkCount} chunk${chunkCount !== 1 ? 's' : ''}`);
  if (docCount > 0) parts.push(`${docCount} source${docCount !== 1 ? 's' : ''}`);
  if (documentCount > 0) parts.push(`${documentCount} document${documentCount !== 1 ? 's' : ''}`);
  if (opinionChars > 0) parts.push(`${opinionChars.toLocaleString()} chars`);
  if (latencyMs > 0) parts.push(`${latencyMs}ms`);

  if (parts.length === 0 && card.status === 'miss') {
    return <p className="text-xs text-muted-foreground/50 mt-2 italic">Not found</p>;
  }
  if (parts.length === 0) return null;

  return (
    <FadeIn>
      <p className="text-xs text-muted-foreground/60 mt-2">{parts.join(' · ')}</p>
    </FadeIn>
  );
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


function ScrollableItemList({ items, totalCount }: { items: string[]; totalCount: number }) {
  const overflow = totalCount > items.length;
  return (
    <div className="relative my-5 h-[150px] rounded-md border border-border/60 bg-muted/20">
      <div className="h-full overflow-y-auto">
        <div className="divide-y divide-border/40">
          {items.map((item, i) => (
            <motion.div
              key={i}
              className="px-3 py-1.5 text-xs text-muted-foreground truncate"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.15, delay: i * 0.02 }}
            >
              {item}
            </motion.div>
          ))}
          {overflow && (
            <div className="px-3 py-1.5 text-xs text-muted-foreground/50 italic">
              +{totalCount - items.length} more
            </div>
          )}
        </div>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-md bg-gradient-to-t from-muted/80 to-transparent" />
    </div>
  );
}

function ScrollableEdgeList({ edges, totalCount }: { edges: { title: string; relationship: string }[]; totalCount: number }) {
  const overflow = totalCount > edges.length;
  return (
    <div className="relative my-5 h-[150px] rounded-md border border-border/60 bg-muted/20">
      <div className="h-full overflow-y-auto">
        <div className="divide-y divide-border/40">
          {edges.map((edge, i) => (
            <motion.div
              key={i}
              className="flex items-center gap-2 px-3 py-1.5"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.15, delay: i * 0.02 }}
            >
              <span className="text-xs text-muted-foreground truncate flex-1">{edge.title}</span>
              {edge.relationship && (
                <span className="text-[10px] text-muted-foreground/50 font-medium uppercase tracking-wide shrink-0">
                  {edge.relationship}
                </span>
              )}
            </motion.div>
          ))}
          {overflow && (
            <div className="px-3 py-1.5 text-xs text-muted-foreground/50 italic">
              +{totalCount - edges.length} more
            </div>
          )}
        </div>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-md bg-gradient-to-t from-muted/80 to-transparent" />
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
    <div className="mt-2 space-y-1.5">
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
    <div className="space-y-1.5">
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


// --- Card visualization router ---

function CardVisualization({ card, trace }: { card: PipelineCard; trace: AgentTraceEvent[] }) {
  switch (card.toolName) {
    case 'faq_search':
      return <FAQCardViz card={card} />;
    case 'vector_search':
      return <VectorCardViz card={card} />;
    case 'get_section':
      return <GetSectionViz card={card} />;
    case 'get_neighbors':
      return <GraphNeighborsViz card={card} />;
    case 'get_authority_chain':
      return <AuthorityChainViz card={card} />;
    case 'answer':
    case 'prepare_answer':
    case 'cite_documents':
      return <SynthesisViz trace={trace} />;
    default:
      return <GenericToolViz card={card} />;
  }
}

// --- Card panel ---

function CardInfoContent({ card }: { card: PipelineCard }) {
  const m = card.metadata;
  const latencyMs = typeof m.latencyMs === 'number' ? m.latencyMs : 0;

  if (card.toolName === 'refine_query') {
    if (latencyMs > 0 && card.status === 'complete') {
      return (
        <FadeIn>
          <p className="text-xs text-muted-foreground/50">{latencyMs}ms</p>
        </FadeIn>
      );
    }
    return null;
  }

  if (card.toolName === 'list_sections') {
    const headings = Array.isArray(m.sectionHeadings) ? (m.sectionHeadings as string[]) : [];
    const totalSections = typeof m.sectionCount === 'number' ? (m.sectionCount as number) : headings.length;
    if (card.status !== 'complete') return null;
    return (
      <FadeIn>
        <div>
          {headings.length > 0 && (
            <ScrollableItemList items={headings} totalCount={totalSections} />
          )}
          {latencyMs > 0 && (
            <p className="text-xs text-muted-foreground/50 mt-2">{latencyMs}ms</p>
          )}
        </div>
      </FadeIn>
    );
  }

  if (card.toolName === 'search_document') {
    const chunkCount = typeof m.chunkCount === 'number' ? m.chunkCount : 0;
    if (card.status !== 'complete' && card.status !== 'miss') return null;
    return (
      <FadeIn>
        <div>
          {chunkCount > 0 ? (
            <p className="text-xs text-muted-foreground/60">
              {chunkCount} chunk{chunkCount !== 1 ? 's' : ''} matched
            </p>
          ) : card.status === 'miss' ? (
            <p className="text-xs text-muted-foreground/50 italic">No matches found</p>
          ) : null}
          {latencyMs > 0 && (
            <p className="text-xs text-muted-foreground/50 mt-1">{latencyMs}ms</p>
          )}
        </div>
      </FadeIn>
    );
  }

  return null;
}

function CardPanel({ card, trace, index }: { card: PipelineCard; trace: AgentTraceEvent[]; index: number }) {
  const title = TOOL_TITLES[card.toolName] ?? card.toolName;
  const isThinking = card.toolName === 'reasoning';
  const hasInfoContent = ['refine_query', 'list_sections', 'search_document'].includes(card.toolName);

  return (
    <div className="h-full p-6 flex flex-col">
      <div className="flex items-start justify-between gap-3 shrink-0">
        <div className="min-w-0">
          <h3 className={`text-base font-semibold tracking-tight ${
            card.status === 'miss' ? 'text-muted-foreground/60' : 'text-foreground'
          }`}>
            {title}
          </h3>
        </div>
        <CardIndicator status={card.status} index={index} />
      </div>

      <div className="flex-1 flex flex-col min-h-0 mt-4">
        {!isThinking && <CardContextLine card={card} />}

        {isThinking ? (
          <p className="text-sm text-muted-foreground/80 leading-relaxed">
            {card.summary}
          </p>
        ) : hasInfoContent ? (
          <CardInfoContent card={card} />
        ) : (
          <CardVisualization card={card} trace={trace} />
        )}

        {card.status === 'pending' && (
          <motion.div
            className="mt-3 flex items-center gap-2 text-xs text-muted-foreground/70"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3 }}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-foreground/30 animate-pulse shrink-0" />
            <span>Processing...</span>
          </motion.div>
        )}
      </div>
    </div>
  );
}

function ArrowConnector() {
  return (
    <div className="absolute top-1/2 -right-[13px] -translate-y-1/2 z-10">
      <div className="h-[22px] w-[22px] rounded-[4px] border border-border bg-background flex items-center justify-center">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M3 2L7 5L3 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground/60" />
        </svg>
      </div>
    </div>
  );
}

function DownArrowConnector() {
  return (
    <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 z-10">
      <div className="h-[22px] w-[22px] rounded-[4px] border border-border bg-background flex items-center justify-center">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 3L5 7L8 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground/60" />
        </svg>
      </div>
    </div>
  );
}

function LeftArrowConnector() {
  return (
    <div className="absolute top-1/2 left-0 -translate-x-1/2 -translate-y-1/2 z-10">
      <div className="h-[22px] w-[22px] rounded-[4px] border border-border bg-background flex items-center justify-center">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M7 2L3 5L7 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground/60" />
        </svg>
      </div>
    </div>
  );
}

function CardIndicator({ status, index }: { status: PipelineCard['status']; index: number }) {
  const num = index + 1;
  if (status === 'pending') {
    return (
      <motion.div
        className="h-6 w-6 rounded-full border-2 border-foreground/50 flex items-center justify-center shrink-0"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        <span className="text-[10px] font-semibold text-foreground/50 tabular-nums animate-pulse">{num}</span>
      </motion.div>
    );
  }
  if (status === 'miss') {
    return (
      <motion.div
        className="h-6 w-6 rounded-full border border-muted-foreground/40 flex items-center justify-center shrink-0"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        <span className="text-[10px] font-semibold text-muted-foreground/50 tabular-nums">{num}</span>
      </motion.div>
    );
  }
  if (status === 'error') {
    return (
      <motion.div
        className="h-6 w-6 rounded-full bg-destructive/20 flex items-center justify-center shrink-0"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        <span className="text-[10px] font-semibold text-destructive tabular-nums">{num}</span>
      </motion.div>
    );
  }
  return (
    <motion.div
      className="h-6 w-6 rounded-full bg-foreground/10 flex items-center justify-center shrink-0"
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.2 }}
    >
      <span className="text-[10px] font-semibold text-foreground/70 tabular-nums">{num}</span>
    </motion.div>
  );
}

// --- Grid with shared borders and arrow connectors ---

function PipelineGrid({ items, trace }: { items: PipelineItem[]; trace: AgentTraceEvent[] }) {
  const gridRef = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(1);

  const measure = useCallback(() => {
    const el = gridRef.current;
    if (!el) return;
    const style = getComputedStyle(el);
    const colCount = style.gridTemplateColumns.split(' ').length;
    setCols(colCount);
  }, []);

  useEffect(() => {
    measure();
    const el = gridRef.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, items.length]);

  const total = items.length;
  const totalRows = Math.ceil(total / cols);

  // Build snake-ordered grid positions
  // Even rows (0,2,4...): left-to-right. Odd rows (1,3,5...): right-to-left.
  const gridSlots = useMemo(() => {
    return items.map((item, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      const isReversedRow = row % 2 === 1;
      const gridCol = isReversedRow ? (cols - 1 - col) : col;
      return { item, index: i, row, gridCol, isReversedRow };
    });
  }, [items, cols]);

  return (
    <div
      ref={gridRef}
      className="border border-border rounded-lg overflow-visible grid grid-cols-[repeat(auto-fill,minmax(400px,1fr))]"
    >
      <AnimatePresence mode="popLayout">
        {gridSlots.map(({ item, index: i, row, gridCol, isReversedRow }) => {
          const isLast = i === total - 1;
          const isLastRow = row === totalRows - 1;

          // Border logic: vertical borders between cells in the same row
          const hasRightBorder = gridCol < cols - 1 && !(isLast && gridCol === cols - 1);
          const hasBottomBorder = !isLastRow;

          // Arrow logic for snake
          const nextI = i + 1;
          const nextRow = nextI < total ? Math.floor(nextI / cols) : -1;
          const sameRow = nextRow === row;

          let connector: React.ReactNode = null;
          if (!isLast) {
            if (sameRow) {
              connector = isReversedRow ? <LeftArrowConnector /> : <ArrowConnector />;
            } else {
              connector = <DownArrowConnector />;
            }
          }

          return (
            <motion.div
              key={item.card.id}
              className={`relative ${hasRightBorder ? 'border-r border-border' : ''} ${hasBottomBorder ? 'border-b border-border' : ''}`}
              style={{ gridColumn: gridCol + 1, gridRow: row + 1 }}
              layout
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.3, delay: i * 0.04, ease: [0.4, 0, 0.2, 1] }}
            >
              <CardPanel card={item.card} trace={trace} index={i} />
              {connector}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

// --- Elapsed badge ---

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

// --- Main modal ---

export interface RetrievalModalProps {
  queryId: string;
  open: boolean;
  onClose: () => void;
}

export function RetrievalModal({ queryId, open, onClose }: RetrievalModalProps) {
  const agentTrace = useChatStore(s => s.queries[queryId]?.agentTrace);
  const query = useChatStore(s => s.queries[queryId]?.query);

  const items = useMemo(() => deriveItems(agentTrace ?? []), [agentTrace]);

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

            {/* Dynamic cards grid — shared borders with arrow connectors */}
            <div className="p-6">
              <PipelineGrid items={items} trace={agentTrace ?? []} />
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
