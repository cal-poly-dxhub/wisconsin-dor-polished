'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useVisualizerSocket } from './hooks/use-visualizer-socket';
import { usePersistedTrace } from './hooks/use-persisted-trace';
import {
  DEMO_ANSWER,
  DEMO_CHOICES,
  DEMO_RESOURCES,
  DEMO_TRACE,
  type FixtureTraceEvent,
} from './fixtures/demo-trace';
import { useTiming } from './hooks/use-timing';
import { CanvasView } from './canvas-view';
import type { ResourceItem } from '@/stores/types';

const queryClient = new QueryClient();

// Fixture Phase B playback: the demo answer types out in chunks once the
// trace reaches `answer_streaming`, then the chips land.
const FIXTURE_FRAGMENT_CHARS = 96;
const FIXTURE_FRAGMENT_MS = 26;

function fmtTime(ms: number): string {
  const total = Math.max(0, ms);
  const s = Math.floor(total / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  const cs = String(Math.floor((total % 1000) / 10)).padStart(2, '0');
  return `${mm}:${ss}.${cs}`;
}

export default function CanvasPage() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* useSearchParams (read in CanvasShell for ?queryId=) requires a Suspense
          boundary under the App Router. */}
      <Suspense fallback={null}>
        <CanvasShell />
      </Suspense>
    </QueryClientProvider>
  );
}

function CanvasShell() {
  // Post-baked replay: when ?queryId=… is present, render a persisted query's
  // stored trace instead of the fixture/live stream. Read once on mount — this
  // page is a full-bleed inspector opened via a fresh navigation, not a route
  // that swaps queryIds in place.
  const searchParams = useSearchParams();
  const persistedQueryId = searchParams.get('queryId');
  const {
    events: persistedEvents,
    query: persistedQuery,
    answer: persistedAnswer,
    loading: persistedLoading,
    error: persistedError,
  } = usePersistedTrace(persistedQueryId);

  // --- Fixture playback ---
  const [fixtureEvents, setFixtureEvents] = useState<FixtureTraceEvent[]>([]);
  const [fixtureAnswer, setFixtureAnswer] = useState('');
  const [fixtureAnswerDone, setFixtureAnswerDone] = useState(false);
  const [fixtureChoices, setFixtureChoices] = useState<string[]>([]);
  const [fixtureResources, setFixtureResources] = useState<ResourceItem[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const scheduleFixture = useCallback(() => {
    for (const t of timerRef.current) clearTimeout(t);
    timerRef.current = [];

    const baseTs = DEMO_TRACE[0]?.timestamp ?? Date.now();
    let lastDelay = 0;
    for (const ev of DEMO_TRACE) {
      const delay = ev.timestamp - baseTs;
      lastDelay = Math.max(lastDelay, delay);
      const timer = setTimeout(() => {
        const nowBase = Date.now() - delay;
        setFixtureEvents((prev) => [
          ...prev,
          { ...ev, timestamp: nowBase + delay },
        ]);
      }, delay);
      timerRef.current.push(timer);
    }

    // Phase B: resource cards land first, then the answer streams, then the
    // clarification chips — the order the backend sends them.
    timerRef.current.push(
      setTimeout(() => setFixtureResources(DEMO_RESOURCES), lastDelay + 120)
    );
    for (let offset = 0; offset < DEMO_ANSWER.length; offset += FIXTURE_FRAGMENT_CHARS) {
      const end = offset + FIXTURE_FRAGMENT_CHARS;
      timerRef.current.push(
        setTimeout(
          () => setFixtureAnswer(DEMO_ANSWER.slice(0, end)),
          lastDelay + 300 + (offset / FIXTURE_FRAGMENT_CHARS) * FIXTURE_FRAGMENT_MS
        )
      );
    }
    const answerMs =
      lastDelay + 300 + Math.ceil(DEMO_ANSWER.length / FIXTURE_FRAGMENT_CHARS) * FIXTURE_FRAGMENT_MS;
    timerRef.current.push(setTimeout(() => setFixtureAnswerDone(true), answerMs));
    timerRef.current.push(setTimeout(() => setFixtureChoices(DEMO_CHOICES), answerMs + 200));
  }, []);

  const resetFixture = useCallback(() => {
    setFixtureEvents([]);
    setFixtureAnswer('');
    setFixtureAnswerDone(false);
    setFixtureChoices([]);
    setFixtureResources([]);
  }, []);

  const playFixture = useCallback(() => {
    resetFixture();
    setTimeout(scheduleFixture, 0);
  }, [resetFixture, scheduleFixture]);

  // Auto-play fixture on mount — skipped when replaying a persisted query.
  const mountedRef = useRef(false);
  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;
    if (persistedQueryId) return;
    scheduleFixture();
    return () => { for (const t of timerRef.current) clearTimeout(t); };
  }, [scheduleFixture, persistedQueryId]);

  // --- Live query via WebSocket ---
  const {
    traceEvents: liveTraceEvents,
    answerText,
    answerComplete,
    resourceItems,
    choices,
    suggestion,
    error: liveError,
    sendQuery,
    isRunning,
  } = useVisualizerSocket();

  const [mode, setMode] = useState<'fixture' | 'live' | 'persisted'>(
    persistedQueryId ? 'persisted' : 'fixture'
  );
  const [queryInput, setQueryInput] = useState('');

  const runQuery = useCallback(
    (q: string) => {
      const trimmed = q.trim();
      if (!trimmed || isRunning) return;
      for (const t of timerRef.current) clearTimeout(t);
      timerRef.current = [];
      resetFixture();
      setMode('live');
      sendQuery(trimmed);
    },
    [isRunning, resetFixture, sendQuery]
  );

  const handleSubmit = useCallback(() => {
    runQuery(queryInput);
    setQueryInput('');
  }, [queryInput, runQuery]);

  const handleReplay = useCallback(() => {
    setMode('fixture');
    playFixture();
  }, [playFixture]);

  const events: FixtureTraceEvent[] =
    mode === 'persisted'
      ? persistedEvents
      : mode === 'live'
        ? liveTraceEvents.map((ev) => ({
            kind: ev.kind as FixtureTraceEvent['kind'],
            turn: ev.turn ?? null,
            seq: ev.seq,
            timestamp: ev.timestamp,
            payload: ev.payload,
            devPayload: ev.devPayload,
          }))
        : fixtureEvents;

  // Phase B state, by mode. Persisted replays only have the stored answer
  // text (the detail endpoint returns no resources or chips).
  const answer =
    mode === 'persisted'
      ? {
          text: persistedAnswer ?? '',
          streaming: false,
          complete: Boolean(persistedAnswer),
          resources: [] as ResourceItem[],
          choices: [] as string[],
          error: null,
        }
      : mode === 'live'
        ? {
            text: answerText,
            streaming: isRunning,
            complete: answerComplete,
            resources: resourceItems,
            choices,
            error: liveError,
            suggestion,
          }
        : {
            text: fixtureAnswer,
            streaming: fixtureAnswer.length > 0 && !fixtureAnswerDone,
            complete: fixtureAnswerDone,
            resources: fixtureResources,
            choices: fixtureChoices,
            error: null,
          };

  const timing = useTiming(events);

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
          Replay demo
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

      {/* Persisted-replay banner — shows which stored query is being inspected
          and calls out that live-only panes (reasoning, token usage,
          disambiguation) are blank because that data isn't persisted. */}
      {mode === 'persisted' && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          <span className="font-semibold uppercase tracking-wide">Replay</span>
          {persistedLoading ? (
            <span>Loading stored trace…</span>
          ) : persistedError ? (
            <span className="text-red-700">No stored trace found for this query.</span>
          ) : (
            <>
              {persistedQuery && <span className="text-amber-800">&ldquo;{persistedQuery}&rdquo;</span>}
              <span className="text-amber-700/70">
                Reasoning, token usage, phase panes &amp; delivered resources are blank — not
                captured in stored traces.
              </span>
            </>
          )}
        </div>
      )}

      {/* Turn-grouped pane layout + Phase B */}
      <CanvasView events={events} sendQuery={runQuery} answer={answer} />
    </div>
  );
}
