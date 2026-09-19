'use client';

import { useState, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import type { FixtureTraceEvent } from './fixtures/demo-trace';
import { VectorSearchPane } from './components/vector-search-pane';
import { InitialVectorSearchPane } from './components/initial-vector-search-pane';
import { GetNeighborsPane } from './components/get-neighbors-pane';
import { SearchDocumentPane } from './components/search-document-pane';
import { GetSectionPane } from './components/get-section-pane';
import { ListSectionsPane } from './components/list-sections-pane';
import { DisambiguationPane } from './components/disambiguation-pane';
import { ScopeGatePane, type ScopeVerdict } from './components/scope-gate-pane';
import { AdequacyPane, type AdequacyVerdict } from './components/adequacy-pane';
import { PrepareAnswerPane } from './components/prepare-answer-pane';
import { FaqSearchPane } from './components/faq-search-pane';
import { ThinkingPane } from './components/thinking-pane';
import { PlaceholderPane } from './components/placeholder-pane';
import { GetDocumentPane } from './components/get-document-pane';
import { GetAuthorityChainPane } from './components/get-authority-chain-pane';
import { ListFrameworkDocsPane } from './components/list-framework-docs-pane';
import { FetchCaseOpinionPane } from './components/fetch-case-opinion-pane';
import { FindCaseLawPane } from './components/find-case-law-pane';
import { GetFlowchartPane, ListFlowchartsPane } from './components/flowchart-panes';
import { ListWorksheetsPane, GetWorksheetPane } from './components/worksheet-panes';
import { AutoRefinePane } from './components/auto-refine-pane';
import { AnswerStreamPane, type AnswerStreamData } from './components/answer-stream-pane';
import { RequestHeader, type RequestHeaderData } from './components/request-header';
import { TurnStatusBar } from './components/turn-usage-divider';

// --- Turn-grouped data model ---

interface ToolPane {
  id: string;
  toolName: string;
  callSummary: string;
  status: 'pending' | 'complete';
  metadata: Record<string, unknown>;
  // Carried straight off the `tool_result` payload (top level, NOT metadata —
  // metadata is filtered against an allowlist server-side, these are not).
  docIds?: string[];
  docTitles?: string[];
  resultSummary?: string;
  resultStatus?: string;
  // `devPayload.toolInput` from the matching `tool_call`. The only place some
  // tools' arguments survive (find_case_law's search_text, get_worksheet's
  // worksheet_id), because build_tool_call_summary has no branch for them.
  toolInput?: Record<string, unknown>;
}

interface TurnGroup {
  id: string;
  turn: number | null;
  label?: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
    cacheReadInputTokens: number;
    cacheWriteInputTokens: number;
    cumulativeTotal: number;
    bedrockLatencyMs?: number;
  };
  panes: ToolPane[];
  isWide?: boolean; // seeded vector_search spans 2 columns
}

function isSeededVectorSearch(metadata: Record<string, unknown>): boolean {
  return metadata.seeded === true;
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
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
      | { caseId: string; title: string; citation: string; summary: string; relevanceScore?: number; contentRole?: string; citedStubs?: string[] }[]
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

// Phases that are not panes of their own: the header strip and the Phase B
// hand-off marker. Pulled out in one pass so `buildTurns` stays about turns.
export interface TraceSummary extends RequestHeaderData {
  answerStreaming: boolean;
}

export function buildTraceSummary(events: FixtureTraceEvent[]): TraceSummary {
  const summary: TraceSummary = {
    received: false,
    historyTurns: null,
    answerStreaming: false,
  };
  for (const ev of events) {
    if (ev.kind !== 'phase') continue;
    const phase = str(ev.payload.phase);
    if (phase === 'request_received') summary.received = true;
    if (phase === 'history_loaded') {
      summary.historyTurns = num(ev.payload.historyTurns) ?? summary.historyTurns;
      summary.received = true;
    }
    if (phase === 'answer_streaming') summary.answerStreaming = true;
  }
  return summary;
}

export function buildTurns(events: FixtureTraceEvent[]): TurnGroup[] {
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

  // A pre-loop verdict only *short-circuits* when nothing follows it. With the
  // adequacy judge on and the scope gate off (ADEQUACY_JUDGE_ENABLED &&
  // !SCOPE_GATE_ENABLED), OUT_OF_SCOPE / DISAMBIGUATE are recorded and the
  // research loop runs anyway — so read it off the trace, don't assume.
  function loopRanAfter(index: number): boolean {
    for (let i = index + 1; i < events.length; i++) {
      const kind = events[i].kind;
      if (kind === 'tool_call' || kind === 'tool_result' || kind === 'loop_complete') return true;
    }
    return false;
  }

  events.forEach((ev, index) => {
    const phase = ev.kind === 'phase' ? str(ev.payload.phase) : '';

    // Pre-loop classifier verdict: proceed | disambiguate | out_of_scope | topic_shift
    if (phase === 'generality_classified') {
      const result = str(ev.payload.result, 'proceed');
      const scopeVerdict = result === 'out_of_scope' || result === 'topic_shift';
      const group = getOrCreateTurn(null, scopeVerdict ? 'Scope Gate' : 'Disambiguation');
      group.panes.push({
        id: `pane-${counter++}`,
        toolName: scopeVerdict ? '_scope_gate' : '_disambiguation',
        callSummary: str(ev.payload.label),
        status: 'complete',
        metadata: {
          result,
          label: ev.payload.label,
          shortCircuited: !loopRanAfter(index),
        },
      });
    }

    // Post-retrieval adequacy judge (between Phase A and Phase B)
    if (phase === 'adequacy_judged') {
      const group = getOrCreateTurn(null, 'Adequacy Judge');
      group.panes.push({
        id: `pane-${counter++}`,
        toolName: '_adequacy',
        callSummary: str(ev.payload.label),
        status: 'complete',
        metadata: {
          ...((ev.payload.metadata as Record<string, unknown>) ?? {}),
          result: ev.payload.result,
          label: ev.payload.label,
        },
      });
    }

    // FAQ transition → attach to existing faq_search pane
    if (phase === 'faq_transition') {
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
        cacheReadInputTokens: (ev.payload.cacheReadInputTokens as number) ?? 0,
        cacheWriteInputTokens: (ev.payload.cacheWriteInputTokens as number) ?? 0,
        cumulativeTotal: (ev.payload.cumulativeTotal as number) ?? 0,
        bedrockLatencyMs: ev.payload.bedrockLatencyMs as number | undefined,
      };
    }

    // Reasoning → own pane inside the turn group
    if (ev.kind === 'reasoning') {
      const text = str(ev.payload.text);
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
      const toolName = str(ev.payload.toolName, 'unknown');
      const turnNum = ev.turn ?? 0;
      const group = getOrCreateTurn(turnNum);
      group.panes.push({
        id: `pane-${counter++}`,
        toolName,
        callSummary: str(ev.payload.summary),
        status: 'pending',
        metadata: {},
        toolInput: (ev.devPayload?.toolInput as Record<string, unknown>) ?? undefined,
      });
      if (toolName === 'vector_search' && ev.turn === 0) {
        group.isWide = true;
      }
    }

    // Tool result → complete the pending pane in the turn group
    if (ev.kind === 'tool_result') {
      const toolName = str(ev.payload.toolName, 'unknown');
      const metadata = (ev.payload.metadata as Record<string, unknown>) || {};
      const docIds = (ev.payload.docIds as string[]) ?? [];
      const docTitles = (ev.payload.docTitles as string[]) ?? [];
      const resultSummary = str(ev.payload.summary);
      const resultStatus = str(ev.payload.status, 'ok');
      // Find the pending pane in this turn's group
      const turnNum = ev.turn ?? 0;
      const group = getOrCreateTurn(turnNum);
      const target = [...group.panes].reverse().find(
        (p) => p.toolName === toolName && p.status === 'pending'
      );
      if (target) {
        target.status = 'complete';
        target.metadata = metadata;
        target.docIds = docIds;
        target.docTitles = docTitles;
        target.resultSummary = resultSummary;
        target.resultStatus = resultStatus;
      } else {
        // No matching call: a result the backend synthesized rather than the
        // model requesting it — the turn-0 vector_search/FAQ seeds, the
        // flowchart router's seeded get_flowchart, the auto_refine stage.
        // Materialize a completed pane so the step is visible instead of
        // vanishing from the trace.
        group.panes.push({
          id: `pane-${counter++}`,
          toolName,
          callSummary: str(metadata.broadQuery) || resultSummary,
          status: 'complete',
          metadata,
          docIds,
          docTitles,
          resultSummary,
          resultStatus,
        });
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
  });
  return turns;
}

// --- Pane map ---
//
// One entry per tool the agent can call (see backend-tools.ts, mirroring
// agent_tools/definitions.py) plus the synthetic `auto_refine` stage and the
// canvas's own `_`-prefixed phase panes. Anything missing here degrades to
// PlaceholderPane, which test/pane-map.test.tsx guards against.

interface PaneContext {
  sendQuery: (q: string) => void;
  /** Clarification chips the backend sent for this query, if any. */
  choices: string[];
}

type PaneRenderer = (pane: ToolPane, ctx: PaneContext) => ReactNode;

export const TOOL_PANES: Record<string, PaneRenderer> = {
  _reasoning: (pane) => <ThinkingPane text={pane.callSummary} />,

  _disambiguation: (pane, ctx) => (
    <DisambiguationPane
      data={{
        result: (pane.metadata.result as 'disambiguate' | 'proceed') ?? 'proceed',
        label: str(pane.metadata.label),
        shortCircuited: pane.metadata.shortCircuited !== false,
        onSelect: (choice) => ctx.sendQuery(choice),
      }}
    />
  ),

  _scope_gate: (pane) => (
    <ScopeGatePane
      data={{
        verdict: (pane.metadata.result as ScopeVerdict) ?? 'out_of_scope',
        label: str(pane.metadata.label),
        shortCircuited: pane.metadata.shortCircuited !== false,
      }}
    />
  ),

  _adequacy: (pane, ctx) => {
    const verdict = str(pane.metadata.result, 'answer') as AdequacyVerdict;
    return (
      <AdequacyPane
        data={{
          verdict,
          label: str(pane.metadata.label),
          latencyMs: num(pane.metadata.latencyMs),
          clarificationOffered: pane.metadata.clarificationOffered === true,
          judgeModelId: str(pane.metadata.judgeModelId),
          // Not on the wire today (the Finding is internal) — rendered if the
          // backend ever starts emitting them.
          supported: str(pane.metadata.supported) || undefined,
          unsupported: str(pane.metadata.unsupported) || undefined,
          rationale: str(pane.metadata.rationale) || undefined,
          clarificationQuestion: str(pane.metadata.clarificationQuestion) || undefined,
          clarificationOptions: verdict === 'clarify' ? ctx.choices : undefined,
        }}
      />
    );
  },

  auto_refine: (pane) => (
    <AutoRefinePane
      data={{
        refined: pane.metadata.refined === true,
        refinedQuery: str(pane.metadata.refinedQuery),
        summary: pane.resultSummary ?? pane.callSummary,
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  vector_search: (pane) =>
    isSeededVectorSearch(pane.metadata) ? (
      <InitialVectorSearchPane data={vectorSearchPaneData(pane)} />
    ) : (
      <VectorSearchPane data={vectorSearchPaneData(pane)} />
    ),

  get_neighbors: (pane) => (
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
        query: str(pane.metadata.query),
        topK: (pane.metadata.topK as number) ?? 10,
        totalCandidates: (pane.metadata.totalCandidates as number) ?? 0,
      }}
    />
  ),

  search_document: (pane) => (
    <SearchDocumentPane
      data={{
        docId: str(pane.metadata.docId),
        docTitle: str(pane.metadata.docTitle) || pane.callSummary,
        query: pane.callSummary.replace(/^"(.+)" in .+$/, '$1'),
        chunkCount: (pane.metadata.chunkCount as number) ?? 0,
        keywordFallback: (pane.metadata.keywordFallback as boolean) ?? false,
        latencyMs: (pane.metadata.latencyMs as number) ?? 0,
      }}
    />
  ),

  list_sections: (pane) => (
    <ListSectionsPane
      data={{
        docTitle: str(pane.metadata.docTitle),
        sectionCount: (pane.metadata.sectionCount as number) ?? 0,
        sectionHeadings: (pane.metadata.sectionHeadings as string[]) ?? [],
        latencyMs: (pane.metadata.latencyMs as number) ?? 0,
      }}
    />
  ),

  get_section: (pane) => (
    <GetSectionPane
      data={{
        docTitle: str(pane.metadata.docTitle),
        heading: str(pane.metadata.heading),
        query: str(pane.metadata.query),
        filtered: (pane.metadata.filtered as boolean) ?? Boolean(pane.metadata.query),
        sectionChunkCount: (pane.metadata.sectionChunkCount as number) ?? 0,
        returnedChunkCount: (pane.metadata.returnedChunkCount as number) ?? 0,
        chunkScores: (pane.metadata.chunkScores as { chunkId: string; cosine: number; zScore: number | null; heading: string; included: boolean }[]) ?? [],
        latencyMs: (pane.metadata.latencyMs as number) ?? 0,
      }}
    />
  ),

  faq_search: (pane) => (
    <FaqSearchPane
      data={{
        query: pane.callSummary.replace(/^"|"$/g, ''),
        latencyMs: num(pane.metadata.latencyMs),
        faqCount: (pane.metadata.faqCount as number) ?? 0,
        topScore: (pane.metadata.topScore as number) ?? 0,
        faqScoreThreshold: (pane.metadata.faqScoreThreshold as number) ?? 0.7,
        faqScores: (pane.metadata.faqScores as number[]) ?? [],
        topFaqSnippet: str(pane.metadata.topFaqSnippet),
        transitionLabel: pane.metadata.transitionLabel as string | undefined,
      }}
    />
  ),

  prepare_answer: (pane) => (
    <PrepareAnswerPane
      data={{
        citedDocCount: (pane.metadata.citedDocCount as number) ?? 0,
        discoveryTitles: (pane.metadata.discoveryTitles as Record<string, string>) ?? {},
        turnsUsed: (pane.metadata.turnsUsed as number) ?? 0,
        elapsedMs: (pane.metadata.elapsedMs as number) ?? 0,
      }}
    />
  ),

  get_document: (pane) => (
    <GetDocumentPane
      data={{
        docId: str(pane.metadata.docId) || pane.callSummary,
        status: ((pane.metadata.documentCount as number) ?? 0) > 0 ? 'ok' : 'miss',
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  get_authority_chain: (pane) => (
    <GetAuthorityChainPane
      data={{
        chainLength: (pane.metadata.chainLength as number) ?? 0,
        latencyMs: num(pane.metadata.latencyMs),
        summary: pane.callSummary,
      }}
    />
  ),

  list_framework_docs: (pane) => (
    <ListFrameworkDocsPane
      data={{
        documentCount: (pane.metadata.documentCount as number) ?? 0,
        latencyMs: num(pane.metadata.latencyMs),
        summary: pane.callSummary,
      }}
    />
  ),

  find_case_law: (pane) => (
    <FindCaseLawPane
      data={{
        searchText:
          str(pane.toolInput?.search_text) || pane.callSummary.replace(/^"|"$/g, ''),
        statuteId: str(pane.toolInput?.statute_id) || undefined,
        cases: (pane.docIds ?? []).map((docId, i) => ({
          docId,
          title: (pane.docTitles ?? [])[i] ?? docId,
        })),
        status: pane.resultStatus === 'miss' ? 'miss' : 'ok',
        summary: pane.resultSummary ?? '',
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  fetch_case_opinion: (pane) => (
    <FetchCaseOpinionPane
      data={{
        citation: pane.callSummary || str(pane.toolInput?.citation),
        opinionChars: (pane.metadata.opinionChars as number) ?? 0,
        status: ((pane.metadata.opinionChars as number) ?? 0) > 0 ? 'ok' : 'miss',
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  list_worksheets: (pane) => (
    <ListWorksheetsPane
      data={{
        summary: pane.resultSummary ?? pane.callSummary,
        worksheetCount: num(pane.metadata.worksheetCount),
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  get_worksheet: (pane) => (
    <GetWorksheetPane
      data={{
        worksheetId:
          str(pane.metadata.worksheetId) || str(pane.toolInput?.worksheet_id) || pane.callSummary,
        sheet: str(pane.toolInput?.sheet) || undefined,
        sheetCount: num(pane.metadata.sheetCount),
        sheetNames: (pane.metadata.sheetNames as string[]) ?? undefined,
        status: pane.resultStatus === 'miss' ? 'miss' : 'ok',
        summary: pane.resultSummary ?? '',
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  list_flowcharts: (pane) => (
    <ListFlowchartsPane
      data={{
        summary: pane.resultSummary ?? pane.callSummary,
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),

  get_flowchart: (pane) => (
    <GetFlowchartPane
      data={{
        flowchartId: str(pane.metadata.flowchartId) || (pane.docIds ?? [])[0] || '',
        title: (pane.docTitles ?? [])[0] ?? pane.callSummary,
        seeded: pane.metadata.seeded === true,
        routerScore: num(pane.metadata.routerScore),
        routerMatchedOn: str(pane.metadata.routerMatchedOn) || undefined,
        wpamPage: str(pane.metadata.wpamPage) || undefined,
        sourceUrl: str(pane.metadata.sourceUrl) || undefined,
        summary: pane.resultSummary ?? '',
        latencyMs: num(pane.metadata.latencyMs),
      }}
    />
  ),
};

function renderToolPane(pane: ToolPane, ctx: PaneContext) {
  const renderer = TOOL_PANES[pane.toolName];
  if (renderer) return renderer(pane, ctx);
  return (
    <PlaceholderPane
      toolName={pane.toolName}
      summary={pane.callSummary}
      latencyMs={num(pane.metadata.latencyMs)}
    />
  );
}

function CollapsibleScopeGate({ turn, passed, ctx }: { turn: TurnGroup; passed: boolean; ctx: PaneContext }) {
  const [open, setOpen] = useState(false);
  const pane = turn.panes[0];
  const shortCircuited = pane?.metadata.shortCircuited !== false;

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200">
      <button
        type="button"
        onClick={() => setOpen(prev => !prev)}
        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-neutral-50 transition-colors cursor-pointer"
      >
        <span className={`text-xs font-bold uppercase tracking-wide ${passed ? 'text-green-700' : 'text-red-700'}`}>
          {passed ? 'Passed Pre-Loop Classifier' : `Classifier verdict: ${str(pane?.metadata.result, 'unknown').replace(/_/g, ' ')}`}
        </span>
        {!passed && (
          <span className="text-xs font-medium text-neutral-400">
            {shortCircuited ? 'loop short-circuited' : 'loop ran anyway'}
          </span>
        )}
        <div className="flex-1" />
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`transition-transform duration-200 text-neutral-400 ${open ? 'rotate-90' : ''}`}
        >
          <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      {open && (
        <div className="border-t border-neutral-100 divide-y divide-neutral-100">
          {turn.panes.map((p) => (
            <div key={p.id}>{renderToolPane(p, ctx)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function CollapsibleFaqTurn({ turn, ctx }: { turn: TurnGroup; ctx: PaneContext }) {
  const [open, setOpen] = useState(false);
  const faqPane = turn.panes.find(p => p.toolName === 'faq_search');
  const topScore = (faqPane?.metadata.topScore as number) ?? 0;
  const faqCount = (faqPane?.metadata.faqCount as number) ?? 0;
  const latencyMs = (faqPane?.metadata.latencyMs as number) ?? 0;
  const threshold = (faqPane?.metadata.faqScoreThreshold as number) ?? 0.7;
  const matched = topScore >= threshold;

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200">
      <button
        type="button"
        onClick={() => setOpen(prev => !prev)}
        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-neutral-50 transition-colors cursor-pointer"
      >
        <span className="text-xs font-bold uppercase tracking-wide text-neutral-500">FAQ Search</span>
        <span className={`text-xs font-medium ${matched ? 'text-green-700' : 'text-neutral-400'}`}>
          {matched ? `Match found · top ${topScore.toFixed(2)}` : `No match · top ${topScore.toFixed(2)}`}
        </span>
        <span className="text-xs text-neutral-300">{faqCount} result{faqCount === 1 ? '' : 's'}</span>
        {latencyMs > 0 && <span className="text-xs text-neutral-300">{latencyMs}ms</span>}
        <div className="flex-1" />
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`transition-transform duration-200 text-neutral-400 ${open ? 'rotate-90' : ''}`}
        >
          <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      {open && (
        <div className="border-t border-neutral-100 divide-y divide-neutral-100">
          {turn.panes.map((pane) => (
            <div key={pane.id}>{renderToolPane(pane, ctx)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

const PRE_GRID_PANES = new Set(['_disambiguation', '_scope_gate', 'faq_search']);

// The turn-grouped pane layout — the visual body of the canvas, decoupled from
// the page shell (nav bar / timing header) so it can be embedded anywhere that
// has a trace: the full-page canvas, or inline in the admin activity detail.
// `sendQuery` is used by the disambiguation pane's clarify buttons and the
// Phase B clarification chips; it defaults to a no-op for read-only replays.
// `answer` carries Phase B (streamed text, resources, chips, error) — omit it
// and the canvas renders the research loop alone, exactly as before.
export function CanvasView({
  events,
  sendQuery = () => {},
  answer,
}: {
  events: FixtureTraceEvent[];
  sendQuery?: (q: string) => void;
  answer?: Omit<AnswerStreamData, 'started'>;
}) {
  const turns = buildTurns(events);
  const summary = buildTraceSummary(events);
  const ctx: PaneContext = { sendQuery, choices: answer?.choices ?? [] };
  const showAnswer =
    answer != null &&
    (summary.answerStreaming ||
      answer.text.length > 0 ||
      answer.resources.length > 0 ||
      answer.choices.length > 0 ||
      Boolean(answer.error));

  return (
    <LayoutGroup>
      {/* Header strip: request_received / history_loaded */}
      {(summary.received || summary.historyTurns != null) && (
        <div className="px-4 pt-2">
          <RequestHeader data={{ received: summary.received, historyTurns: summary.historyTurns }} />
        </div>
      )}

      {/* Pre-grid: pre-loop classifier as collapsible full-width row */}
      <div className="px-4 pt-2">
        <AnimatePresence initial={false}>
          {turns
            .filter((turn) => turn.panes.some(p => p.toolName === '_disambiguation' || p.toolName === '_scope_gate'))
            .map((turn) => {
              const pane = turn.panes.find(p => p.toolName === '_disambiguation' || p.toolName === '_scope_gate');
              const passed = str(pane?.metadata.result) === 'proceed';
              return (
                <motion.div
                  key={turn.id}
                  layout="position"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{
                    layout: { duration: 0.4, ease: [0.4, 0, 0.2, 1] },
                    opacity: { duration: 0.35, delay: 0.15 },
                  }}
                  className="mb-2"
                >
                  <CollapsibleScopeGate turn={turn} passed={passed} ctx={ctx} />
                </motion.div>
              );
            })}
        </AnimatePresence>
      </div>

      {/* Pre-grid: FAQ Search as collapsible full-width row */}
      <div className="px-4">
        <AnimatePresence initial={false}>
          {turns
            .filter((turn) => turn.panes.some(p => p.toolName === 'faq_search'))
            .map((turn) => {
              const faqPanes = turn.panes.filter(p => p.toolName === 'faq_search');
              const faqTurn: TurnGroup = { ...turn, panes: faqPanes };
              return (
                <motion.div
                  key={`faq-${turn.id}`}
                  layout="position"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{
                    layout: { duration: 0.4, ease: [0.4, 0, 0.2, 1] },
                    opacity: { duration: 0.35, delay: 0.15 },
                  }}
                  className="mb-2"
                >
                  <CollapsibleFaqTurn turn={faqTurn} ctx={ctx} />
                </motion.div>
              );
            })}
        </AnimatePresence>
      </div>

      {/* Grid: everything else (with the pre-grid panes stripped out) */}
      <div
        className="px-4 pb-4 grid gap-4"
        style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
        }}
      >
        <AnimatePresence initial={false}>
          {turns
            .map((turn) => ({ ...turn, panes: turn.panes.filter(p => !PRE_GRID_PANES.has(p.toolName)) }))
            .filter((turn) => turn.panes.length > 0)
            .map((turn) => (
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
                turn.isWide ? 'col-span-3' : ''
              }`}
            >
              {/* Status bar */}
              <TurnStatusBar
                data={{
                  turn: turn.turn ?? 0,
                  label: turn.label,
                  inputTokens: turn.usage?.inputTokens,
                  outputTokens: turn.usage?.outputTokens,
                  cacheReadInputTokens: turn.usage?.cacheReadInputTokens,
                  cacheWriteInputTokens: turn.usage?.cacheWriteInputTokens,
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
                      {renderToolPane(pane, ctx)}
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* Phase B — the answer the user actually saw */}
      {showAnswer && answer && (
        <div className="px-4 pb-8">
          <motion.div
            layout="position"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ opacity: { duration: 0.35 } }}
            className="overflow-hidden rounded-lg border border-neutral-200"
          >
            <TurnStatusBar data={{ turn: 0, label: 'Phase B · Answer Streaming' }} />
            <AnswerStreamPane
              data={{ ...answer, started: summary.answerStreaming }}
              onChoice={sendQuery}
            />
          </motion.div>
        </div>
      )}
    </LayoutGroup>
  );
}
