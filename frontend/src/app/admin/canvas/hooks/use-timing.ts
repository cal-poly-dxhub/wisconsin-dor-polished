'use client';

import { useEffect, useMemo, useState } from 'react';
import type { FixtureTraceEvent } from '../fixtures/demo-trace';

export interface TimingState {
  loopStartMs: number | null;
  turnStartMs: number | null;
  currentTurn: number | null;
  finalElapsedMs: number | null;
  done: boolean;
}

export function useTiming(events: FixtureTraceEvent[]) {
  const timing = useMemo<TimingState>(() => {
    const state: TimingState = {
      loopStartMs: null,
      turnStartMs: null,
      currentTurn: null,
      finalElapsedMs: null,
      done: false,
    };
    for (const ev of events) {
      if (ev.kind === 'loop_start') state.loopStartMs = ev.timestamp;
      if (ev.kind === 'loop_complete') {
        state.done = true;
        state.finalElapsedMs =
          typeof ev.payload.elapsedMs === 'number'
            ? ev.payload.elapsedMs
            : state.loopStartMs != null
              ? ev.timestamp - state.loopStartMs
              : 0;
      }
      if (ev.turn != null && ev.turn !== state.currentTurn) {
        state.currentTurn = ev.turn;
        state.turnStartMs = ev.timestamp;
      }
    }
    return state;
  }, [events]);

  const [now, setNow] = useState(() => Date.now());
  const running = timing.loopStartMs != null && !timing.done;

  useEffect(() => {
    if (!running) return;
    let raf = 0;
    const tick = () => { setNow(Date.now()); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running]);

  const totalMs = timing.done
    ? timing.finalElapsedMs ?? 0
    : timing.loopStartMs != null
      ? now - timing.loopStartMs
      : 0;

  const turnMs = timing.done || timing.turnStartMs == null ? 0 : now - timing.turnStartMs;

  return { ...timing, totalMs, turnMs };
}
