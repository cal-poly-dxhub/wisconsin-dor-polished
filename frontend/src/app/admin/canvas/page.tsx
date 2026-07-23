'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { ArrowRight } from 'lucide-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useVisualizerSocket } from '@/app/visualizer/hooks/use-visualizer-socket';
import { useCorpusManifest } from '@/app/visualizer/hooks/use-corpus-manifest';
import { DEMO_TRACE, type FixtureTraceEvent } from './fixtures/demo-trace';
import { useTiming } from './hooks/use-timing';
import { VectorSearchPane } from './components/vector-search-pane';
import { InitialVectorSearchPane } from './components/initial-vector-search-pane';
import { GetNeighborsPane } from './components/get-neighbors-pane';
import { SearchDocumentPane } from './components/search-document-pane';
import { GetSectionPane } from './components/get-section-pane';
import { ListSectionsPane } from './components/list-sections-pane';
import { DisambiguationPane } from './components/disambiguation-pane';
import { PrepareAnswerPane } from './components/prepare-answer-pane';
import { FaqSearchPane } from './components/faq-search-pane';
import { ThinkingPane } from './components/thinking-pane';
import { PlaceholderPane } from './components/placeholder-pane';
import { GetDocumentPane } from './components/get-document-pane';
import { GetAuthorityChainPane } from './components/get-authority-chain-pane';
import { ListFrameworkDocsPane } from './components/list-framework-docs-pane';
import { FetchCaseOpinionPane } from './components/fetch-case-opinion-pane';
import { ClarifyPane } from './components/clarify-pane';
import { TurnStatusBar } from './components/turn-usage-divider';

const queryClient = new QueryClient();

function fmtTime(ms: number): string {
  const total = Math.max(0, ms);
  const s = Math.floor(total / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  const cs = String(Math.floor((total % 1000) / 10)).padStart(2, '0');
  return `${mm}:${ss}.${cs}`;
}

// --- Turn-grouped data model ---

interface ToolPane {
  id: string;
  toolName: string;
  callSummary: string;
  status: 'pending' | 'complete';
  metadata: Record<string, unknown>;
}

interface TurnGroup {
  id: string;
  turn: number | null;
  label?: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
    cumulativeTotal: number;
    bedrockLatencyMs?: number;
  };
  panes: ToolPane[];
  isWide?: boolean; // seeded vector_search spans 2 columns
}

function isSeededVectorSearch(metadata: Record<string, unknown>): boolean {
  return metadata.seeded === true;
}

function vectorSearchPaneData(pane: ToolPane) {
  const m = pane.metadata;
  return {
    query: (m.broadQuery as string) || pane.callSummary.replace(/^"|"$/g, ''),
    latencyMs: (m.latencyMs as number) ?? 0,
    preDedupCount: (m.preDedupCount as number) ?? 0,
    chunkCount: (m.chunkCount as number) ?? 0,
    broadChunkCount: m.broadChunkCount as number | undefined,
    totalChunkCount: m.totalChunkCount as number | undefined,
    topK: m.topK as number | undefined,
    diversityCapPerDoc: m.diversityCapPerDoc as number | undefined,
    docCount: m.docCount as number | undefined,
    topScore: m.topScore as number | undefined,
    docChunks: (m.docChunks as Record<string, number>) ?? {},
    authorityBreakdown: m.authorityBreakdown as Record<string, number> | undefined,
    scoreBuckets: m.scoreBuckets as Record<string, number> | undefined,
    targetWpamYear: m.targetWpamYear as number | undefined,
    caseLawCount: m.caseLawCount as number | undefined,
    autoEnrichedCount: m.autoEnrichedCount as number | undefined,
    statuteBackfill: m.statuteBackfill as
      | { chunkId: string; docId: string; sourceRank: number }[]
      | undefined,
    caselawBackfill: m.caselawBackfill as
      | { caseId: string; title: string; citation: string; summary: string; relevanceScore?: number; contentRole?: string }[]
      | undefined,
    caselawBackfillMeta: m.caselawBackfillMeta as
      | { stubsSearched?: string[]; candidateCount?: number; fetchSaturated?: boolean; fetchK?: number; latencyMs?: number }
      | undefined,
    refinedQuery: m.refinedQuery as string | undefined,
    broadQuery: m.broadQuery as string | undefined,
    broadSkipped: m.broadSkipped as boolean | undefined,
    broadDiscovery: m.broadDiscovery as { docId: string; score: number }[] | undefined,
    broadDocChunks: m.broadDocChunks as Record<string, number> | undefined,
    broadFullDocChunks: m.broadFullDocChunks as Record<string, number> | undefined,
    broadPreDedupCount: m.broadPreDedupCount as number | undefined,
    broadKeptCount: m.broadKeptCount as number | undefined,
    broadAuthorityBreakdown: m.broadAuthorityBreakdown as Record<string, number> | undefined,
    broadScoreBuckets: m.broadScoreBuckets as Record<string, number> | undefined,
    broadTopScore: m.broadTopScore as number | undefined,
  };
}

function buildTurns(events: FixtureTraceEvent[]): TurnGroup[] {
  const turns: TurnGroup[] = [];
  const turnMap = new Map<number | string, TurnGroup>();
  let counter = 0;

  function getOrCreateTurn(turn: number | null, label?: string): TurnGroup {
    const key = turn ?? `special-${counter}`;
    let group = turnMap.get(key);
    if (!group) {
      group = {
        id: `turn-${counter++}`,
        turn,
        label,
        panes: [],
      };
      turnMap.set(key, group);
      turns.push(group);
    }
    return group;
  }

  for (const ev of events) {
    // Disambiguation is a special pre-loop event
    if (ev.kind === 'phase' && (ev.payload.phase as string) === 'generality_classified') {
      const group = getOrCreateTurn(null, 'Disambiguation');
      group.panes.push({
        id: `pane-${counter++}`,
        toolName: '_disambiguation',
        callSummary: (ev.payload.label as string) || '',
        status: 'complete',
        metadata: { result: ev.payload.result, label: ev.payload.label },
      });
    }

    // FAQ transition → attach to existing faq_search pane
    if (ev.kind === 'phase' && (ev.payload.phase as string) === 'faq_transition') {
      for (const g of [...turns].reverse()) {
        const faqPane = g.panes.find((p) => p.toolName === 'faq_search');
        if (faqPane) {
          faqPane.metadata = { ...faqPane.metadata, transitionLabel: ev.payload.label };
          break;
        }
      }
    }

    // Turn usage → attach to the turn group
    if (ev.kind === 'turn_usage') {
      const turnNum = (ev.turn as number) ?? (ev.payload.turn as number) ?? 0;
      const group = getOrCreateTurn(turnNum);
      group.usage = {
        inputTokens: (ev.payload.inputTokens as number) ?? 0,
        outputTokens: (ev.payload.outputTokens as number) ?? 0,
        cumulativeTotal: (ev.payload.cumulativeTotal as number) ?? 0,
        bedrockLatencyMs: ev.payload.bedrockLatencyMs as number | undefined,
      };
    }

    // Reasoning → own pane inside the turn group
    if (ev.kind === 'reasoning') {
      const text = (ev.payload.text as string) || '';
      if (text) {
        const turnNum = ev.turn ?? 0;
        const group = getOrCreateTurn(turnNum);
        group.panes.push({
          id: `pane-${counter++}`,
          toolName: '_reasoning',
          callSummary: text,
          status: 'complete',
          metadata: {},
        });
      }
    }

    // Tool call → add pending pane to the turn group
    if (ev.kind === 'tool_call') {
      const toolName = (ev.payload.toolName as string) || 'unknown';
      if (toolName === 'refine_query') continue;
      const turnNum = ev.turn ?? 0;
      const group = getOrCreateTurn(turnNum);
      group.panes.push({
        id: `pane-${counter++}`,
        toolName,
        callSummary: (ev.payload.summary as string) || '',
        status: 'pending',
        metadata: {},
      });
      if (toolName === 'vector_search' && ev.turn === 0) {
        group.isWide = true;
      }
    }

    // Tool result → complete the pending pane in the turn group
    if (ev.kind === 'tool_result') {
      const toolName = (ev.payload.toolName as string) || 'unknown';
      if (toolName === 'refine_query') continue;
      const metadata = (ev.payload.metadata as Record<string, unknown>) || {};
      // Find the pending pane in this turn's group
      const turnNum = ev.turn ?? 0;
      const group = getOrCreateTurn(turnNum);
      const target = [...group.panes].reverse().find(
        (p) => p.toolName === toolName && p.status === 'pending'
      );
      if (target) {
        target.status = 'complete';
        target.metadata = metadata;
      } else if (toolName === 'vector_search' && isSeededVectorSearch(metadata)) {
        group.panes.push({
          id: `pane-${counter++}`,
          toolName: 'vector_search',
          callSummary: (metadata.broadQuery as string) || (ev.payload.summary as string) || '',
          status: 'complete',
          metadata,
        });
        group.isWide = true;
      }
      if (toolName === 'vector_search' && isSeededVectorSearch(metadata)) {
        group.isWide = true;
      }
    }

    // Loop complete → finalize
    if (ev.kind === 'loop_complete') {
      for (const g of turns) {
        const prepPane = g.panes.find((p) => p.toolName === 'prepare_answer');
        if (prepPane) {
          prepPane.metadata = {
            ...prepPane.metadata,
            citedDocCount: ev.payload.citedDocCount,
            discoveryTitles: ev.payload.discoveryTitles,
            turnsUsed: ev.payload.turnsUsed,
            elapsedMs: ev.payload.elapsedMs,
          };
        }
      }
      for (const g of turns) {
        for (const p of g.panes) p.status = 'complete';
      }
    }
  }
  return turns;
}

function renderToolPane(pane: ToolPane, sendQuery: (q: string) => void) {
  if (pane.toolName === '_reasoning') {
    return <ThinkingPane text={pane.callSummary} />;
  }
  if (pane.toolName === '_disambiguation') {
    return (
      <DisambiguationPane
        data={{
          result: (pane.metadata.result as 'disambiguate' | 'proceed') ?? 'proceed',
          label: (pane.metadata.label as string) ?? '',
          onSelect: (choice) => sendQuery(choice),
        }}
      />
    );
  }
  if (pane.toolName === 'vector_search' && isSeededVectorSearch(pane.metadata)) {
    return <InitialVectorSearchPane data={vectorSearchPaneData(pane)} />;
  }
  if (pane.toolName === 'vector_search') {
    return <VectorSearchPane data={vectorSearchPaneData(pane)} />;
  }
  if (pane.toolName === 'get_neighbors') {
    return (
      <GetNeighborsPane
        data={{
          seedDoc: pane.callSummary,
          neighbors: ((pane.metadata.neighborEdges as {
            id?: string; title: string; relationship: string; rank?: number; score?: number | null;
          }[]) ?? []).slice(0, 10).map((e, i) => ({
            docId: e.id ?? ((pane.metadata.neighborTitles as string[]) ?? [])[i] ?? e.title,
            title: e.title,
            relationship: e.relationship,
            rank: e.rank ?? i + 1,
            score: e.score ?? undefined,
          })),
          latencyMs: (pane.metadata.latencyMs as number) ?? 0,
          ranked: (pane.metadata.ranked as boolean) ?? false,
          query: (pane.metadata.query as string) ?? '',
          topK: (pane.metadata.topK as number) ?? 10,
          totalCandidates: (pane.metadata.totalCandidates as number) ?? 0,
        }}
      />
    );
  }
  if (pane.toolName === 'search_document') {
    return (
      <SearchDocumentPane
        data={{
          docId: (pane.metadata.docId as string) ?? '',
          docTitle: (pane.metadata.docTitle as string) ?? pane.callSummary,
          query: pane.callSummary.replace(/^"(.+)" in .+$/, '$1'),
          chunkCount: (pane.metadata.chunkCount as number) ?? 0,
          keywordFallback: (pane.metadata.keywordFallback as boolean) ?? false,
          latencyMs: (pane.metadata.latencyMs as number) ?? 0,
        }}
      />
    );
  }
  if (pane.toolName === 'list_sections') {
    return (
      <ListSectionsPane
        data={{
          docTitle: (pane.metadata.docTitle as string) ?? '',
          sectionCount: (pane.metadata.sectionCount as number) ?? 0,
          sectionHeadings: (pane.metadata.sectionHeadings as string[]) ?? [],
          latencyMs: (pane.metadata.latencyMs as number) ?? 0,
        }}
      />
    );
  }
  if (pane.toolName === 'get_section') {
    return (
      <GetSectionPane
        data={{
          docTitle: (pane.metadata.docTitle as string) ?? '',
          heading: (pane.metadata.heading as string) ?? '',
          query: (pane.metadata.query as string) ?? '',
          filtered: (pane.metadata.filtered as boolean) ?? Boolean(pane.metadata.query),
          sectionChunkCount: (pane.metadata.sectionChunkCount as number) ?? 0,
          returnedChunkCount: (pane.metadata.returnedChunkCount as number) ?? 0,
          chunkScores: (pane.metadata.chunkScores as { chunkId: string; cosine: number; zScore: number | null; heading: string; included: boolean }[]) ?? [],
          latencyMs: (pane.metadata.latencyMs as number) ?? 0,
        }}
      />
    );
  }
  if (pane.toolName === 'faq_search') {
    return (
      <FaqSearchPane
        data={{
          query: pane.callSummary.replace(/^"|"$/g, ''),
          latencyMs: pane.metadata.latencyMs as number | undefined,
          faqCount: (pane.metadata.faqCount as number) ?? 0,
          topScore: (pane.metadata.topScore as number) ?? 0,
          faqScoreThreshold: (pane.metadata.faqScoreThreshold as number) ?? 0.7,
          faqScores: (pane.metadata.faqScores as number[]) ?? [],
          topFaqSnippet: (pane.metadata.topFaqSnippet as string) ?? '',
          transitionLabel: pane.metadata.transitionLabel as string | undefined,
        }}
      />
    );
  }
  if (pane.toolName === 'prepare_answer') {
    return (
      <PrepareAnswerPane
        data={{
          citedDocCount: (pane.metadata.citedDocCount as number) ?? 0,
          discoveryTitles: (pane.metadata.discoveryTitles as Record<string, string>) ?? {},
          turnsUsed: (pane.metadata.turnsUsed as number) ?? 0,
          elapsedMs: (pane.metadata.elapsedMs as number) ?? 0,
        }}
      />
    );
  }
  if (pane.toolName === 'get_document') {
    return (
      <GetDocumentPane
        data={{
          docId: (pane.metadata.docId as string) ?? pane.callSummary,
          status: ((pane.metadata.documentCount as number) ?? 0) > 0 ? 'ok' : 'miss',
          latencyMs: pane.metadata.latencyMs as number | undefined,
        }}
      />
    );
  }
  if (pane.toolName === 'get_authority_chain') {
    return (
      <GetAuthorityChainPane
        data={{
          chainLength: (pane.metadata.chainLength as number) ?? 0,
          latencyMs: pane.metadata.latencyMs as number | undefined,
          summary: pane.callSummary,
        }}
      />
    );
  }
  if (pane.toolName === 'list_framework_docs') {
    return (
      <ListFrameworkDocsPane
        data={{
          documentCount: (pane.metadata.documentCount as number) ?? 0,
          latencyMs: pane.metadata.latencyMs as number | undefined,
          summary: pane.callSummary,
        }}
      />
    );
  }
  if (pane.toolName === 'fetch_case_opinion') {
    return (
      <FetchCaseOpinionPane
        data={{
          citation: pane.callSummary,
          opinionChars: (pane.metadata.opinionChars as number) ?? 0,
          status: ((pane.metadata.opinionChars as number) ?? 0) > 0 ? 'ok' : 'miss',
          latencyMs: pane.metadata.latencyMs as number | undefined,
        }}
      />
    );
  }
  if (pane.toolName === 'clarify') {
    return (
      <ClarifyPane
        data={{
          summary: pane.callSummary,
          latencyMs: pane.metadata.latencyMs as number | undefined,
        }}
      />
    );
  }
  return (
    <PlaceholderPane
      toolName={pane.toolName}
      summary={pane.callSummary}
      latencyMs={pane.metadata.latencyMs as number | undefined}
    />
  );
}

export default function CanvasPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <CanvasShell />
    </QueryClientProvider>
  );
}

function CanvasShell() {
  // --- Fixture playback ---
  const [fixtureEvents, setFixtureEvents] = useState<FixtureTraceEvent[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const scheduleFixture = useCallback(() => {
    for (const t of timerRef.current) clearTimeout(t);
    timerRef.current = [];

    const baseTs = DEMO_TRACE[0]?.timestamp ?? Date.now();
    for (const ev of DEMO_TRACE) {
      const delay = ev.timestamp - baseTs;
      const timer = setTimeout(() => {
        const nowBase = Date.now() - delay;
        setFixtureEvents((prev) => [
          ...prev,
          { ...ev, timestamp: nowBase + delay },
        ]);
      }, delay);
      timerRef.current.push(timer);
    }
  }, []);

  const playFixture = useCallback(() => {
    setFixtureEvents([]);
    setTimeout(scheduleFixture, 0);
  }, [scheduleFixture]);

  // Auto-play fixture on mount
  const mountedRef = useRef(false);
  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;
    scheduleFixture();
    return () => { for (const t of timerRef.current) clearTimeout(t); };
  }, [scheduleFixture]);

  // --- Live query via WebSocket ---
  const { manifest } = useCorpusManifest();
  const {
    traceEvents: liveTraceEvents,
    sendQuery,
    isRunning,
  } = useVisualizerSocket(manifest);

  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [queryInput, setQueryInput] = useState('');

  const handleSubmit = useCallback(() => {
    const q = queryInput.trim();
    if (!q || isRunning) return;
    for (const t of timerRef.current) clearTimeout(t);
    timerRef.current = [];
    setFixtureEvents([]);
    setMode('live');
    sendQuery(q);
    setQueryInput('');
  }, [queryInput, isRunning, sendQuery]);

  const handleReplay = useCallback(() => {
    setMode('fixture');
    playFixture();
  }, [playFixture]);

  const events: FixtureTraceEvent[] = mode === 'live'
    ? liveTraceEvents.map((ev) => ({
        kind: ev.kind as FixtureTraceEvent['kind'],
        turn: ev.turn ?? null,
        seq: ev.seq,
        timestamp: ev.timestamp,
        payload: ev.payload,
      }))
    : fixtureEvents;

  const timing = useTiming(events);
  const turns = buildTurns(events);

  return (
    <div className="relative h-full w-full overflow-auto bg-white">
      {/* Top nav bar */}
      <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-neutral-200 bg-white px-4 py-2.5">
        <div className="flex w-full max-w-sm items-center rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="mr-2 text-sm font-semibold text-neutral-400">Q</span>
          <input
            type="text"
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
            placeholder="Ask a property-tax question…"
            className="flex-1 bg-transparent text-sm text-neutral-700 outline-none placeholder:text-neutral-400"
            disabled={isRunning}
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isRunning || !queryInput.trim()}
            className="ml-2 flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-full bg-neutral-900 text-white disabled:opacity-30"
          >
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>

        <button
          type="button"
          onClick={handleReplay}
          className="shrink-0 cursor-pointer rounded-full border border-neutral-200 px-4 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50"
        >
          Replay
        </button>

        <div className="flex-1" />

        <div className="shrink-0 flex items-center gap-3">
          <span className="font-mono text-xl font-semibold tabular-nums text-neutral-900">
            {fmtTime(timing.totalMs)}
          </span>
          <span className="text-xs text-neutral-500">
            {timing.currentTurn != null ? `Turn ${timing.currentTurn}` : timing.done ? 'Done' : 'Idle'}
          </span>
        </div>
      </div>

      {/* Turn-grouped pane grid */}
      <LayoutGroup>
        <div
          className="pt-2 px-4 pb-8 grid gap-4"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
          }}
        >
          <AnimatePresence initial={false}>
            {turns.map((turn) => (
              <motion.div
                key={turn.id}
                layout="position"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{
                  layout: { duration: 0.4, ease: [0.4, 0, 0.2, 1] },
                  opacity: { duration: 0.35, delay: 0.15 },
                }}
                className={`overflow-hidden rounded-lg border border-neutral-200 ${
                  turn.isWide ? 'col-span-2' : ''
                }`}
              >
                {/* Status bar */}
                <TurnStatusBar
                  data={{
                    turn: turn.turn ?? 0,
                    label: turn.label,
                    inputTokens: turn.usage?.inputTokens,
                    outputTokens: turn.usage?.outputTokens,
                    cumulativeTotal: turn.usage?.cumulativeTotal,
                    bedrockLatencyMs: turn.usage?.bedrockLatencyMs,
                  }}
                />

                {/* Tool panes stacked vertically */}
                <div className="divide-y divide-neutral-100">
                  <AnimatePresence initial={false}>
                    {turn.panes.map((pane) => (
                      <motion.div
                        key={pane.id}
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        transition={{ opacity: { duration: 0.3 }, height: { duration: 0.3 } }}
                      >
                        {renderToolPane(pane, sendQuery)}
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </LayoutGroup>
    </div>
  );
}
