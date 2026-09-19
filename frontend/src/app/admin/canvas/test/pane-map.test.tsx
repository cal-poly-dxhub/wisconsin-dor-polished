/** @bun */
import { describe, expect, test } from 'bun:test';
import { BACKEND_TOOL_NAMES, SYNTHETIC_TOOL_NAMES } from '../backend-tools';
import { TOOL_PANES, buildTurns, buildTraceSummary } from '../canvas-view';
import type { FixtureTraceEvent } from '../fixtures/demo-trace';

describe('canvas pane map', () => {
  // The guard this file exists for: a tool added to
  // backend/lambdas/agentic_retrieval/agent_tools/definitions.py (and mirrored
  // into backend-tools.ts) with no pane would otherwise degrade silently to
  // PlaceholderPane's "Coming soon" on the admin canvas.
  test('every backend tool has a pane', () => {
    const missing = BACKEND_TOOL_NAMES.filter((name) => !(name in TOOL_PANES));
    expect(missing).toEqual([]);
  });

  test('synthetic pipeline stages that emit tool_result have a pane', () => {
    const missing = SYNTHETIC_TOOL_NAMES.filter((name) => !(name in TOOL_PANES));
    expect(missing).toEqual([]);
  });

  test('pane map has no entry for tools the backend no longer exposes', () => {
    // `refine_query` became the auto_refine stage; `clarify` is executable but
    // absent from TOOL_DEFINITIONS, so the model can never call it.
    expect(TOOL_PANES.refine_query).toBeUndefined();
    expect(TOOL_PANES.clarify).toBeUndefined();
    expect(TOOL_PANES.cite_documents).toBeUndefined();
  });
});

function ev(
  kind: FixtureTraceEvent['kind'],
  turn: number | null,
  payload: Record<string, unknown>,
  devPayload?: Record<string, unknown>,
): FixtureTraceEvent {
  return { kind, turn, seq: 0, timestamp: 0, payload, devPayload };
}

describe('buildTurns', () => {
  test('materializes a backend-synthesized tool_result that has no tool_call', () => {
    // The flowchart router seeds a turn-0 get_flowchart result directly; the
    // auto_refine stage does the same. Neither is preceded by a tool_call.
    const turns = buildTurns([
      ev('tool_result', 0, {
        toolName: 'get_flowchart',
        status: 'success',
        summary: 'Seeded flowchart: Determining Agricultural Classification',
        docIds: ['flowcharts-ag-classification'],
        docTitles: ['Determining Agricultural Classification'],
        metadata: { seeded: true, flowchartId: 'flowcharts-ag-classification', routerScore: 0.66 },
      }),
      ev('tool_result', 0, {
        toolName: 'auto_refine',
        status: 'ok',
        summary: 'Refined to "…"',
        metadata: { refined: true, refinedQuery: 'refined text' },
      }),
    ]);

    const panes = turns.flatMap((t) => t.panes);
    expect(panes.map((p) => p.toolName).sort()).toEqual(['auto_refine', 'get_flowchart']);
    const flowchart = panes.find((p) => p.toolName === 'get_flowchart')!;
    expect(flowchart.status).toBe('complete');
    expect(flowchart.docTitles?.[0]).toBe('Determining Agricultural Classification');
  });

  test('keeps docIds, docTitles and the tool input on a matched call/result pair', () => {
    const turns = buildTurns([
      ev(
        'tool_call',
        3,
        { toolName: 'find_case_law', summary: '' },
        { toolInput: { search_text: 'Sausen v. Town of Black Creek' } },
      ),
      ev('tool_result', 3, {
        toolName: 'find_case_law',
        status: 'ok',
        summary: 'find_case_law complete',
        docIds: ['case-law-2014-wi-9'],
        docTitles: ['Sausen v. Town of Black Creek Board of Review'],
        metadata: { latencyMs: 138 },
      }),
    ]);

    const pane = turns[0].panes[0];
    expect(pane.status).toBe('complete');
    expect(pane.toolInput?.search_text).toBe('Sausen v. Town of Black Creek');
    expect(pane.docIds).toEqual(['case-law-2014-wi-9']);
  });

  test('adequacy_judged becomes its own pane carrying the verdict', () => {
    const turns = buildTurns([
      ev('phase', null, {
        phase: 'adequacy_judged',
        label: 'Sources diverge on a fact the user did not give',
        result: 'clarify',
        metadata: { latencyMs: 1080, clarificationOffered: true },
      }),
    ]);

    const pane = turns[0].panes[0];
    expect(pane.toolName).toBe('_adequacy');
    expect(pane.metadata.result).toBe('clarify');
    expect(pane.metadata.clarificationOffered).toBe(true);
  });

  test('a pre-loop verdict is only short-circuited when no tool events follow', () => {
    const verdict = ev('phase', null, {
      phase: 'generality_classified',
      label: 'Query is outside property tax scope',
      result: 'out_of_scope',
    });

    const stopped = buildTurns([verdict]);
    expect(stopped[0].panes[0].toolName).toBe('_scope_gate');
    expect(stopped[0].panes[0].metadata.shortCircuited).toBe(true);

    // Scope gate off (adequacy judge on): the verdict is recorded and the
    // research loop runs anyway.
    const continued = buildTurns([
      verdict,
      ev('tool_call', 0, { toolName: 'faq_search', summary: '"q"' }),
    ]);
    expect(continued[0].panes[0].metadata.shortCircuited).toBe(false);
  });
});

describe('buildTraceSummary', () => {
  test('reads the header phases and the Phase B hand-off', () => {
    const summary = buildTraceSummary([
      ev('phase', null, { phase: 'request_received' }),
      ev('phase', null, { phase: 'history_loaded', historyTurns: 2 }),
      ev('phase', null, { phase: 'answer_streaming' }),
    ]);
    expect(summary).toEqual({ received: true, historyTurns: 2, answerStreaming: true });
  });
});
