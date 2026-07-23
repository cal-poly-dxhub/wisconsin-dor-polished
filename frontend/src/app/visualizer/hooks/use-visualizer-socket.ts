'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { WebSocket as PartySocket } from 'partysocket';
import { createSession, sendMessage } from '@/api/chat-api';
import type { SourceDocument, FAQ } from '@messages/websocket-interface';
import type { ResourceItem } from '@/stores/types';
import { useFixtureStream } from './use-fixture-stream';
import type { GridManifest } from './use-corpus-manifest';

export interface TraceEvent {
  kind: 'loop_start' | 'reasoning' | 'tool_call' | 'tool_result' | 'loop_complete' | 'phase' | 'turn_usage';
  turn?: number | null;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
  devPayload?: Record<string, unknown>;
}

interface UseVisualizerSocketReturn {
  traceEvents: TraceEvent[];
  answerText: string;
  answerComplete: boolean;
  resourceItems: ResourceItem[];
  isConnected: boolean;
  isRunning: boolean;
  currentQuery: string | null;
  sendQuery: (query: string) => void;
  playFixtures: () => void;
  playVectorOnly: () => void;
  error: string | null;
}

const WS_URL = process.env.NEXT_PUBLIC_WEBSOCKET_URL!;

export function useVisualizerSocket(manifest: GridManifest | null): UseVisualizerSocketReturn {
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [answerText, setAnswerText] = useState('');
  const [answerComplete, setAnswerComplete] = useState(false);
  const [resourceItems, setResourceItems] = useState<ResourceItem[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [currentQuery, setCurrentQuery] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<PartySocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const resetQueryState = useCallback(() => {
    setAnswerText('');
    setAnswerComplete(false);
    setResourceItems([]);
  }, []);

  const resetAnswerStream = useCallback(() => {
    setAnswerText('');
    setAnswerComplete(false);
  }, []);

  const setResources = useCallback((items: ResourceItem[]) => {
    setResourceItems(items);
  }, []);

  const appendResources = useCallback((items: ResourceItem[]) => {
    if (items.length === 0) return;
    setResourceItems((prev) => [...prev, ...items]);
  }, []);

  const appendTraceEvent = useCallback((traceEvent: TraceEvent) => {
    setTraceEvents((prev) => {
      if (prev.some((e) => e.seq === traceEvent.seq)) return prev;
      return [...prev, traceEvent];
    });
  }, []);

  const { playFixture, playVectorOnly: playVectorOnlyRaw, cancelFixture } = useFixtureStream({
    onStart: (query) => {
      setError(null);
      setTraceEvents([]);
      resetQueryState();
      setCurrentQuery(query);
      setIsRunning(true);
    },
    onEvent: appendTraceEvent,
    onComplete: () => setIsRunning(false),
    onAnswerStart: resetAnswerStream,
    onAnswerFragment: (fragment) => {
      setAnswerText((prev) => prev + fragment);
    },
    onAnswerComplete: () => setAnswerComplete(true),
    onResources: setResources,
    onError: (message) => setError(message),
    manifest,
  });

  const connectWebSocket = useCallback((sessionId: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const url = `${WS_URL}?sessionId=${sessionId}`;
    const ws = new PartySocket(url, [], {
      connectionTimeout: 4000,
      maxRetries: 10,
    });

    ws.addEventListener('open', () => setIsConnected(true));
    ws.addEventListener('close', () => setIsConnected(false));
    ws.addEventListener('error', () => {
      setError('WebSocket connection error');
    });

    ws.addEventListener('message', (event: MessageEvent) => {
      try {
        const raw = JSON.parse(event.data as string);
        if (!raw || typeof raw !== 'object' || !('streamId' in raw)) return;
        if (raw.streamId === 'heartbeat') return;

        const body = raw.body;
        if (!body || typeof body !== 'object' || !('responseType' in body)) return;

        if (body.responseType === 'agent-event') {
          const traceEvent: TraceEvent = {
            kind: body.kind,
            turn: body.turn ?? null,
            seq: body.seq,
            timestamp: body.timestamp,
            payload: body.payload || {},
            devPayload: body.devPayload || {},
          };

          appendTraceEvent(traceEvent);

          if (body.kind === 'loop_complete') {
            setIsRunning(false);
          }
          return;
        }

        if (body.responseType === 'answer-event') {
          if (body.event === 'start') {
            resetAnswerStream();
          } else if (body.event === 'stop') {
            setAnswerComplete(true);
          }
          return;
        }

        if (body.responseType === 'fragment') {
          const fragment = body.content?.fragment;
          if (typeof fragment === 'string') {
            setAnswerText((prev) => prev + fragment);
          }
          return;
        }

        if (body.responseType === 'documents') {
          const docs = body.content?.documents;
          if (Array.isArray(docs) && docs.length > 0) {
            appendResources(
              (docs as SourceDocument[]).map((doc) => ({
                type: 'document' as const,
                data: doc,
              }))
            );
          }
          return;
        }

        if (body.responseType === 'faq') {
          const faqs = body.content?.faqs;
          if (Array.isArray(faqs) && faqs.length > 0) {
            appendResources(
              (faqs as FAQ[]).map((faq) => ({
                type: 'faq' as const,
                data: faq,
              }))
            );
          }
        }
      } catch (err) {
        console.error('[Visualizer WS] Parse error:', err);
      }
    });

    wsRef.current = ws;
  }, [appendTraceEvent, appendResources, resetAnswerStream]);

  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (sessionIdRef.current) return sessionIdRef.current;

    try {
      const { sessionId } = await createSession();
      sessionIdRef.current = sessionId;
      connectWebSocket(sessionId);
      return sessionId;
    } catch (err) {
      setError('Failed to create session');
      console.error('[Visualizer] Session creation failed:', err);
      return null;
    }
  }, [connectWebSocket]);

  const sendQuery = useCallback(
    async (query: string) => {
      cancelFixture();
      setError(null);
      setTraceEvents([]);
      resetQueryState();
      setCurrentQuery(query);
      setIsRunning(true);

      const sessionId = await ensureSession();
      if (!sessionId) {
        setIsRunning(false);
        return;
      }

      // Wait for WebSocket to be connected before sending
      if (!wsRef.current || wsRef.current.readyState !== globalThis.WebSocket.OPEN) {
        await new Promise<void>((resolve) => {
          const check = () => {
            if (wsRef.current?.readyState === globalThis.WebSocket.OPEN) {
              resolve();
            } else {
              setTimeout(check, 50);
            }
          };
          check();
        });
      }

      try {
        await sendMessage(sessionId, { message: query });
      } catch (err) {
        setError('Failed to send query');
        setIsRunning(false);
        console.error('[Visualizer] Send failed:', err);
      }
    },
    [cancelFixture, ensureSession, resetQueryState]
  );

  const playFixtures = useCallback(() => {
    if (isRunning) return;
    playFixture();
  }, [isRunning, playFixture]);

  const playVectorOnly = useCallback(() => {
    if (isRunning) return;
    playVectorOnlyRaw();
  }, [isRunning, playVectorOnlyRaw]);

  useEffect(() => {
    return () => {
      cancelFixture();
      wsRef.current?.close();
    };
  }, [cancelFixture]);

  return {
    traceEvents,
    answerText,
    answerComplete,
    resourceItems,
    isConnected,
    isRunning,
    currentQuery,
    sendQuery,
    playFixtures,
    playVectorOnly,
    error,
  };
}
