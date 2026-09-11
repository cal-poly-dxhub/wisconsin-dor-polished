'use client';

import { useEffect, useState } from 'react';
import { http } from '@/lib/http';
import type { FixtureTraceEvent } from '../fixtures/demo-trace';

// The flat trace shape persisted per query on the ChatHistoryTable row (mirrors
// `TraceEvent` in use-activity-data.ts). Only `tool_result` + `loop_complete`
// events are ever stored, and every field sits at the top level — there is no
// `payload` wrapper.
export interface PersistedTraceEvent {
  kind: string;
  turn: number | null;
  ts: number;
  toolName?: string;
  status?: string;
  summary?: string;
  docIds?: string[];
  docTitles?: string[];
  metadata?: Record<string, unknown>;
  latencyMs?: number;
  terminalReason?: string;
  turnsUsed?: number;
  elapsedMs?: number;
  citedDocCount?: number;
  citedDocIds?: string[];
  discovery?: Record<string, number>;
}

interface ActivityDetailResponse {
  statusCode?: number;
  body?: string;
  item?: { trace?: PersistedTraceEvent[] | null; query?: string };
}

export interface PersistedTraceResult {
  events: FixtureTraceEvent[];
  query: string | null;
  loading: boolean;
  error: boolean;
}

// Re-shape the flat persisted trace into the nested `FixtureTraceEvent` stream
// the canvas's `buildTurns` consumes (it reads everything off `ev.payload.*`).
//
// The persisted trace is a strict subset of the live agent-event stream: it
// carries only `tool_result` + `loop_complete`, so the canvas's reasoning /
// token-usage / disambiguation / FAQ-transition panes have no source data and
// stay blank on replay — acceptable for post-hoc inspection. The rich per-tool
// metadata (scoreBuckets, docChunks, authorityBreakdown, statute/caselaw
// backfill, chunkScores, faqScores, …) IS fully persisted, so the vector-search
// and per-tool panes render for real.
//
// `buildTurns` only creates a pane when it first sees a `tool_call` (it then
// completes the pending pane on the matching `tool_result`). Since no tool_call
// is persisted, we synthesize one immediately before each tool_result so the
// pane materializes; the following result then fills it in.
export function toCanvasEvents(trace: PersistedTraceEvent[]): FixtureTraceEvent[] {
  const events: FixtureTraceEvent[] = [];
  let seq = 0;

  for (const ev of trace) {
    if (ev.kind === 'tool_result') {
      const toolName = ev.toolName ?? 'unknown';
      const metadata: Record<string, unknown> = { ...(ev.metadata ?? {}) };
      // The live emit folds per-tool latency into metadata; the persisted trace
      // stores it at the top level. Restore it so the panes can show timings.
      if (ev.latencyMs != null && metadata.latencyMs == null) {
        metadata.latencyMs = ev.latencyMs;
      }

      // Synthetic pending call so buildTurns creates the pane. `refine_query`
      // is intentionally skipped by buildTurns, so don't bother emitting it.
      if (toolName !== 'refine_query') {
        events.push({
          kind: 'tool_call',
          turn: ev.turn,
          seq: seq++,
          timestamp: ev.ts,
          payload: { toolName, summary: ev.summary ?? '' },
        });
      }

      events.push({
        kind: 'tool_result',
        turn: ev.turn,
        seq: seq++,
        timestamp: ev.ts,
        payload: { toolName, status: ev.status ?? 'ok', summary: ev.summary ?? '', metadata },
      });
      continue;
    }

    if (ev.kind === 'loop_complete') {
      events.push({
        kind: 'loop_complete',
        turn: ev.turn,
        seq: seq++,
        timestamp: ev.ts,
        payload: {
          terminalReason: ev.terminalReason,
          turnsUsed: ev.turnsUsed,
          elapsedMs: ev.elapsedMs,
          citedDocCount: ev.citedDocCount,
          citedDocIds: ev.citedDocIds,
          // Persisted under `discovery`; the canvas + panes read `discoveryCounts`.
          discoveryCounts: ev.discovery ?? {},
          // discoveryTitles is never persisted (fetched from Neptune only on the
          // live path), so the synthesis pane's title map stays empty.
        },
      });
      continue;
    }

    // Any other persisted kind (none expected today) — pass through nested so a
    // future addition doesn't silently vanish.
    events.push({
      kind: ev.kind as FixtureTraceEvent['kind'],
      turn: ev.turn,
      seq: seq++,
      timestamp: ev.ts,
      payload: {},
    });
  }

  return events;
}

// Fetches a persisted query's trace and adapts it for the canvas visualizer.
// Returns empty events (not an error) when queryId is null so callers can hold
// the hook unconditionally.
export function usePersistedTrace(queryId: string | null): PersistedTraceResult {
  const [events, setEvents] = useState<FixtureTraceEvent[]>([]);
  const [query, setQuery] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!queryId) {
      setEvents([]);
      setQuery(null);
      setLoading(false);
      setError(false);
      return;
    }

    const controller = new AbortController();

    async function run() {
      setLoading(true);
      setError(false);
      setEvents([]);
      setQuery(null);

      try {
        const response = await http
          .get(`admin/activity/${queryId}`, { signal: controller.signal })
          .json<ActivityDetailResponse>();

        const data =
          response.statusCode && response.body
            ? (JSON.parse(response.body) as { item?: ActivityDetailResponse['item'] })
            : response;

        if (controller.signal.aborted) return;

        const trace = data.item?.trace ?? [];
        setEvents(toCanvasEvents(trace));
        setQuery(data.item?.query ?? null);
        setError(trace.length === 0);
      } catch (err) {
        if (!controller.signal.aborted) {
          console.error('Failed to fetch persisted trace:', err);
          setError(true);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    run();
    return () => controller.abort();
  }, [queryId]);

  return { events, query, loading, error };
}
