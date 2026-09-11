'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useVisualizerSocket } from './hooks/use-visualizer-socket';
import { usePersistedTrace } from './hooks/use-persisted-trace';
import { useCorpusManifest } from './hooks/use-corpus-manifest';
import { DEMO_TRACE, type FixtureTraceEvent } from './fixtures/demo-trace';
import { useTiming } from './hooks/use-timing';
import { CanvasView } from './canvas-view';

const queryClient = new QueryClient();

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
    loading: persistedLoading,
    error: persistedError,
  } = usePersistedTrace(persistedQueryId);

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
  const { manifest } = useCorpusManifest();
  const {
    traceEvents: liveTraceEvents,
    sendQuery,
    isRunning,
  } = useVisualizerSocket(manifest);

  const [mode, setMode] = useState<'fixture' | 'live' | 'persisted'>(
    persistedQueryId ? 'persisted' : 'fixture'
  );
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
          }))
        : fixtureEvents;

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
                Reasoning, token usage &amp; disambiguation panes are blank — not captured in stored traces.
              </span>
            </>
          )}
        </div>
      )}

      {/* Turn-grouped pane layout */}
      <CanvasView events={events} sendQuery={sendQuery} />
    </div>
  );
}
