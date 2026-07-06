'use client';

import { useCallback, useRef, type MutableRefObject } from 'react';
import type { TraceEvent } from './use-visualizer-socket';
import type { GridManifest } from './use-corpus-manifest';
import type { ResourceItem } from '@/stores/types';
import {
  FIXTURE_QUERY,
  FIXTURE_TURN_GAP_MS,
  FIXTURE_ANSWER,
  ZONING_AGRICULTURAL_FIXTURE,
  type FixtureStep,
} from '../fixtures/zoning-agricultural-trace';
import {
  VECTOR_ONLY_QUERY,
  VECTOR_SEARCH_ONLY_FIXTURE,
} from '../fixtures/vector-search-only';
import fixtureResources from '../fixtures/zoning-agricultural-resources.json';

const FIXTURE_FRAGMENT_CHARS = 96;
const FIXTURE_FRAGMENT_MS = 28;

function scheduleAnswerPlayback(
  answer: string,
  timers: ReturnType<typeof setTimeout>[],
  runId: number,
  runIdRef: MutableRefObject<number>,
  onAnswerStart?: () => void,
  onAnswerFragment?: (fragment: string) => void,
  onAnswerComplete?: () => void
) {
  if (!answer || !onAnswerFragment) return;

  const startTimer = setTimeout(() => {
    if (runId !== runIdRef.current) return;
    onAnswerStart?.();

    let offset = 0;
    const pushNext = () => {
      if (runId !== runIdRef.current) return;
      if (offset >= answer.length) {
        onAnswerComplete?.();
        return;
      }
      const fragment = answer.slice(offset, offset + FIXTURE_FRAGMENT_CHARS);
      offset += FIXTURE_FRAGMENT_CHARS;
      onAnswerFragment(fragment);
      const timer = setTimeout(pushNext, FIXTURE_FRAGMENT_MS);
      timers.push(timer);
    };

    pushNext();
  }, 500);

  timers.push(startTimer);
}

function turnGapMs(
  lastTurn: number | null,
  nextTurn: number | null | undefined
): number {
  if (nextTurn == null || lastTurn == null) return 0;
  return nextTurn > lastTurn ? FIXTURE_TURN_GAP_MS : 0;
}

/**
 * Build a map from docId → real tile IDs from the manifest.
 * Used to substitute synthetic fixture chunk IDs with real ones.
 */
function buildDocTileMap(manifest: GridManifest | null): Map<string, string[]> {
  const map = new Map<string, string[]>();
  if (!manifest) return map;
  for (const tile of manifest.tiles) {
    let arr = map.get(tile.docId);
    if (!arr) {
      arr = [];
      map.set(tile.docId, arr);
    }
    arr.push(tile.id);
  }
  return map;
}

/**
 * Given a fixture tool_result event, replace synthetic chunkIds with
 * real tile IDs sampled from the same documents.
 */
function resolveChunkIds(
  payload: Record<string, unknown>,
  docTileMap: Map<string, string[]>
): Record<string, unknown> {
  const metadata = payload.metadata as Record<string, unknown> | undefined;
  if (!metadata?.chunkIds) return payload;

  const syntheticIds = metadata.chunkIds as string[];
  const docIds = (payload.docIds as string[]) || [];

  // Group synthetic IDs by their source doc (prefix before _chunk_)
  const countPerDoc = new Map<string, number>();
  for (const sid of syntheticIds) {
    const docId = sid.replace(/_chunk_\d+$/, '');
    countPerDoc.set(docId, (countPerDoc.get(docId) || 0) + 1);
  }

  // Sample real tile IDs from each doc
  const resolvedIds: string[] = [];
  for (const [docId, count] of countPerDoc) {
    const realTiles = docTileMap.get(docId);
    if (!realTiles || realTiles.length === 0) {
      // If this doc doesn't exist in manifest, try matching from docIds list
      // or fall back to keeping synthetic IDs
      continue;
    }
    // Take up to `count` tiles, cycling if needed
    for (let i = 0; i < count; i++) {
      resolvedIds.push(realTiles[i % realTiles.length]);
    }
  }

  // If we couldn't resolve any from the per-chunk mapping, try the docIds array
  if (resolvedIds.length === 0 && docIds.length > 0) {
    for (const docId of docIds) {
      const realTiles = docTileMap.get(docId);
      if (realTiles) {
        const take = Math.min(realTiles.length, Math.ceil(syntheticIds.length / docIds.length));
        for (let i = 0; i < take; i++) {
          resolvedIds.push(realTiles[i]);
        }
      }
    }
  }

  return {
    ...payload,
    metadata: {
      ...metadata,
      chunkIds: resolvedIds.length > 0 ? resolvedIds : syntheticIds,
    },
  };
}

interface UseFixtureStreamOptions {
  onEvent: (event: TraceEvent) => void;
  onStart: (query: string) => void;
  onComplete: () => void;
  onAnswerStart?: () => void;
  onAnswerFragment?: (fragment: string) => void;
  onAnswerComplete?: () => void;
  onResources?: (items: ResourceItem[]) => void;
  onError: (message: string) => void;
  manifest: GridManifest | null;
}

export function useFixtureStream({
  onEvent,
  onStart,
  onComplete,
  onAnswerStart,
  onAnswerFragment,
  onAnswerComplete,
  onResources,
  onError,
  manifest,
}: UseFixtureStreamOptions) {
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const runIdRef = useRef(0);

  const clearTimers = useCallback(() => {
    for (const timer of timersRef.current) {
      clearTimeout(timer);
    }
    timersRef.current = [];
  }, []);

  const playFixture = useCallback(
    (steps: FixtureStep[] = ZONING_AGRICULTURAL_FIXTURE, query = FIXTURE_QUERY) => {
      clearTimers();
      const runId = ++runIdRef.current;
      const docTileMap = buildDocTileMap(manifest);

      onStart(query);

      let seq = 0;
      let elapsedMs = 0;
      let lastTurn: number | null = null;
      const baseTimestamp = Date.now();

      const scheduleStep = (index: number) => {
        if (index >= steps.length) return;

        const step = steps[index];
        const gapMs = turnGapMs(lastTurn, step.event.turn);
        const delayMs = step.delayMs + gapMs;
        elapsedMs += delayMs;

        const timer = setTimeout(() => {
          if (runId !== runIdRef.current) return;

          seq += 1;

          // Resolve chunk IDs for tool_result events
          let payload = step.event.payload;
          if (step.event.kind === 'tool_result') {
            payload = resolveChunkIds(payload, docTileMap);
          }

          const event: TraceEvent = {
            ...step.event,
            payload,
            seq,
            timestamp: baseTimestamp + elapsedMs,
          };
          onEvent(event);

          if (typeof step.event.turn === 'number') {
            lastTurn = step.event.turn;
          }

          if (step.event.kind === 'loop_complete') {
            onComplete();
            onResources?.(fixtureResources as ResourceItem[]);
            scheduleAnswerPlayback(
              FIXTURE_ANSWER,
              timersRef.current,
              runId,
              runIdRef,
              onAnswerStart,
              onAnswerFragment,
              onAnswerComplete
            );
            return;
          }

          scheduleStep(index + 1);
        }, delayMs);

        timersRef.current.push(timer);
      };

      try {
        scheduleStep(0);
      } catch (err) {
        console.error('[Visualizer] Fixture playback failed:', err);
        onError('Failed to play fixture trace');
        onComplete();
      }
    },
    [clearTimers, manifest, onAnswerComplete, onAnswerFragment, onAnswerStart, onComplete, onError, onEvent, onResources, onStart]
  );

  const cancelFixture = useCallback(() => {
    runIdRef.current += 1;
    clearTimers();
  }, [clearTimers]);

  const playVectorOnly = useCallback(() => {
    playFixture(VECTOR_SEARCH_ONLY_FIXTURE, VECTOR_ONLY_QUERY);
  }, [playFixture]);

  return { playFixture, playVectorOnly, cancelFixture };
}
