"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TraceEvent {
  kind:
    | "loop_start"
    | "reasoning"
    | "tool_call"
    | "tool_result"
    | "loop_complete"
    | "phase";
  turn?: number | null;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
  devPayload?: Record<string, unknown>;
}

export interface ActiveOp {
  toolName: string;
  summary: string;
  chunkIds: string[];
  /** How many of chunkIds have been revealed so far */
  revealedCount: number;
  status: "revealing" | "complete";
}

export interface OperationEntry {
  id: string;
  toolName: string;
  summary: string;
  chunkIds: string[];
  revealedCount: number;
  status: "pending" | "revealing" | "complete";
  /** Sequential index of this vector_search (1-based), null for non-vector ops */
  vectorIndex: number | null;
}

/** Describes what the grid should currently display for a given tool call */
export interface GridOp {
  opId: string;
  toolName: string;
  /** For vector_search/search_document: grouped results per call [{label: "Q1", chunkIds: [...]}] */
  vectorCalls: { label: string; chunkIds: string[] }[];
  /** For get_neighbors: seed doc + discovered neighbor doc IDs */
  neighborData?: { seedDocId: string; neighborDocIds: string[] };
}

export interface HighlightState {
  /** All chunk IDs revealed so far (cumulative across all tool_results in this query) */
  revealedIds: Set<string>;
  /** Chunk IDs being actively revealed RIGHT NOW (current batch, for bright highlighting) */
  activeIds: Set<string>;
  /** The active operation for the card */
  activeOp: ActiveOp | null;
  /** All operations in order (for the sidebar stack) */
  operations: OperationEntry[];
  /** What the grid should display right now (the "focused" operation) */
  gridOp: GridOp | null;
  /** Whether the full loop is done */
  done: boolean;
  /** Replay a specific operation by id (for clicking completed cards) */
  replayOp: (opId: string) => void;
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

interface BatchEntry {
  toolName: string;
  summary: string;
  chunkIds: string[];
}

const BATCH_GAP_MS = 700;
const STAGGER_TICK_MS = 80;
const GROUP_SIZE_MIN = 3;
const GROUP_SIZE_MAX = 5;
const MAX_STAGGER_MS = 1200;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useHighlightTimeline(
  traceEvents: TraceEvent[]
): HighlightState {
  // Exposed state
  const [revealedIds, setRevealedIds] = useState<Set<string>>(new Set());
  const [activeIds, setActiveIds] = useState<Set<string>>(new Set());
  const [activeOp, setActiveOp] = useState<ActiveOp | null>(null);
  const [operations, setOperations] = useState<OperationEntry[]>([]);
  const [gridOp, setGridOp] = useState<GridOp | null>(null);
  const [done, setDone] = useState(false);
  const opCounterRef = useRef(0);
  const vectorCountRef = useRef(0);
  // Track which vector_search calls belong to the current "group" (parallel calls in same turn)
  const currentGridOpRef = useRef<GridOp | null>(null);

  // Internal refs
  const queueRef = useRef<BatchEntry[]>([]);
  const isRevealingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const batchStartRef = useRef<number>(0);
  const processedSeqRef = useRef<number>(-1);
  const prevEventsLenRef = useRef<number>(0);

  // Cleanup timer helper
  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Process the next batch from the queue
  const processNextBatch = useCallback(() => {
    const queue = queueRef.current;
    if (queue.length === 0) {
      isRevealingRef.current = false;
      return;
    }

    isRevealingRef.current = true;
    const batch = queue.shift()!;
    batchStartRef.current = Date.now();

    const { chunkIds, toolName, summary } = batch;

    // Determine group size: cap total stagger at MAX_STAGGER_MS
    const maxGroups = Math.floor(MAX_STAGGER_MS / STAGGER_TICK_MS);
    const groupSize = Math.min(
      GROUP_SIZE_MAX,
      Math.max(GROUP_SIZE_MIN, Math.ceil(chunkIds.length / maxGroups))
    );

    // Split chunkIds into groups
    const groups: string[][] = [];
    for (let i = 0; i < chunkIds.length; i += groupSize) {
      groups.push(chunkIds.slice(i, i + groupSize));
    }

    let groupIndex = 0;

    // Set active op as revealing
    setActiveOp({
      toolName,
      summary,
      chunkIds,
      revealedCount: 0,
      status: "revealing",
    });

    // Set initial activeIds to empty for this batch
    setActiveIds(new Set());

    const revealNextGroup = () => {
      if (groupIndex >= groups.length) {
        // Batch complete
        clearTimer();

        // Mark op complete
        setActiveOp((prev) =>
          prev ? { ...prev, revealedCount: chunkIds.length, status: "complete" } : null
        );
        setOperations((prev) => {
          const idx = prev.findIndex(
            (op) => op.toolName === toolName && op.status === "revealing"
          );
          if (idx === -1) return prev;
          const updated = [...prev];
          updated[idx] = { ...updated[idx], revealedCount: chunkIds.length, status: "complete" };
          return updated;
        });

        // Clear active highlight after a brief moment
        setActiveIds(new Set());

        // Enforce minimum gap between batches
        const elapsed = Date.now() - batchStartRef.current;
        const remaining = Math.max(0, BATCH_GAP_MS - elapsed);

        setTimeout(() => {
          processNextBatch();
        }, remaining);

        return;
      }

      const group = groups[groupIndex];
      groupIndex++;

      // Add to revealed (cumulative)
      setRevealedIds((prev) => {
        const next = new Set(prev);
        for (const id of group) next.add(id);
        return next;
      });

      // Set active (only current group is "bright")
      setActiveIds(new Set(group));

      // Update revealed count on active op and operation stack
      const currentRevealedCount = Math.min(groupIndex * groupSize, chunkIds.length);
      setActiveOp((prev) =>
        prev
          ? { ...prev, revealedCount: currentRevealedCount }
          : null
      );
      setOperations((prev) => {
        const idx = prev.findIndex(
          (op) => op.toolName === toolName && op.status === "revealing"
        );
        if (idx === -1) return prev;
        const updated = [...prev];
        updated[idx] = { ...updated[idx], revealedCount: currentRevealedCount };
        return updated;
      });
    };

    // Reveal the first group immediately
    revealNextGroup();

    // Then stagger the rest
    if (groups.length > 1) {
      timerRef.current = setInterval(() => {
        revealNextGroup();
      }, STAGGER_TICK_MS);
    } else {
      // Single group — batch is already complete
      setActiveOp((prev) =>
        prev ? { ...prev, revealedCount: chunkIds.length, status: "complete" } : null
      );
      setOperations((prev) => {
        const idx = prev.findIndex(
          (op) => op.toolName === toolName && op.status === "revealing"
        );
        if (idx === -1) return prev;
        const updated = [...prev];
        updated[idx] = { ...updated[idx], revealedCount: chunkIds.length, status: "complete" };
        return updated;
      });
      setActiveIds(new Set());

      const elapsed = Date.now() - batchStartRef.current;
      const remaining = Math.max(0, BATCH_GAP_MS - elapsed);
      setTimeout(() => {
        processNextBatch();
      }, remaining);
    }
  }, [clearTimer]);

  // Watch traceEvents for new tool_result events with chunkIds
  useEffect(() => {
    // Reset detection: if events array shrunk or is empty, reset everything
    if (traceEvents.length === 0 && prevEventsLenRef.current > 0) {
      clearTimer();
      queueRef.current = [];
      isRevealingRef.current = false;
      processedSeqRef.current = -1;
      opCounterRef.current = 0;
      vectorCountRef.current = 0;
      currentGridOpRef.current = null;
      setRevealedIds(new Set());
      setActiveIds(new Set());
      setActiveOp(null);
      setOperations([]);
      setGridOp(null);
      setDone(false);
      prevEventsLenRef.current = 0;
      return;
    }

    prevEventsLenRef.current = traceEvents.length;

    // Process new events
    for (const event of traceEvents) {
      if (event.seq <= processedSeqRef.current) continue;
      processedSeqRef.current = event.seq;

      if (event.kind === "tool_call") {
        const toolName = (event.payload.toolName as string) || "unknown";
        const summary = (event.payload.summary as string) || "";
        const opId = `op-${opCounterRef.current++}`;

        const vectorIndex = toolName === "vector_search" ? ++vectorCountRef.current : null;

        // Only clear the grid when a new turn introduces a different tool type
        // (parallel calls within the same turn shouldn't override each other)
        const currentRef = currentGridOpRef.current;
        const isDifferentTool = currentRef && currentRef.toolName !== toolName;
        const isDifferentTurn = currentRef && currentRef.opId !== opId;

        if (isDifferentTool && isDifferentTurn) {
          // Only the "primary" tool of a new turn gets grid control
          // Priority: search_document/get_section > vector_search > others
          const hasGridViz = true; // All tools get grid control (specific or fallback zoom)
          if (hasGridViz) {
            currentGridOpRef.current = null;
            setGridOp(null);
          }
        }

        // Start/extend the grid op for tools with visualizations
        if (toolName === "vector_search") {
          if (!currentGridOpRef.current || currentGridOpRef.current.toolName !== "vector_search") {
            currentGridOpRef.current = { opId, toolName, vectorCalls: [] };
          }
        } else if (toolName === "search_document" || toolName === "get_section") {
          if (!currentGridOpRef.current || currentGridOpRef.current.toolName !== toolName) {
            currentGridOpRef.current = { opId, toolName, vectorCalls: [] };
          }
        } else if (toolName === "get_neighbors") {
          if (!currentGridOpRef.current || currentGridOpRef.current.toolName !== "get_neighbors") {
            currentGridOpRef.current = { opId, toolName, vectorCalls: [], neighborData: { seedDocId: summary, neighborDocIds: [] } };
          }
        } else {
          // All other tools (prepare_answer, faq_search, get_authority_chain, etc.)
          currentGridOpRef.current = { opId, toolName, vectorCalls: [] };
          setGridOp(currentGridOpRef.current);
        }

        // Add pending operation to the stack
        setOperations((prev) => [
          ...prev,
          { id: opId, toolName, summary, chunkIds: [], revealedCount: 0, status: "pending", vectorIndex },
        ]);

        setActiveOp({
          toolName,
          summary,
          chunkIds: [],
          revealedCount: 0,
          status: "revealing",
        });
      }

      if (event.kind === "tool_result") {
        const metadata = event.payload.metadata as
          | { chunkIds?: string[]; chunkCount?: number; latencyMs?: number }
          | undefined;
        let chunkIds = metadata?.chunkIds || [];
        const toolName = (event.payload.toolName as string) || "unknown";
        const summary = (event.payload.summary as string) || "";

        // Update gridOp for vector_search results
        if (toolName === "vector_search" && chunkIds.length > 0) {
          const current = currentGridOpRef.current;
          if (current && current.toolName === "vector_search") {
            const label = `Q${current.vectorCalls.length + 1}`;
            current.vectorCalls.push({ label, chunkIds });
            setGridOp({ ...current });
          }
        }

        // Update gridOp for search_document / get_section results
        if ((toolName === "search_document" || toolName === "get_section") && chunkIds.length > 0) {
          const current = currentGridOpRef.current;
          if (current && current.toolName === toolName) {
            const docLabel = (event.payload.metadata as Record<string, unknown>)?.docTitle as string
              || (event.payload.metadata as Record<string, unknown>)?.docId as string
              || summary;
            current.vectorCalls.push({ label: docLabel, chunkIds });
            setGridOp({ ...current });
          }
        }

        // Update gridOp for get_neighbors results
        if (toolName === "get_neighbors") {
          const current = currentGridOpRef.current;
          const neighborDocIds = (event.payload.docIds as string[]) || [];
          if (current && current.toolName === "get_neighbors" && current.neighborData) {
            current.neighborData.neighborDocIds = neighborDocIds;
            setGridOp({ ...current });
          }
          // Store docIds on the operation entry so replay can access them
          if (neighborDocIds.length > 0) {
            chunkIds = neighborDocIds;
          }
        }

        // Update the most recent pending operation with result data
        setOperations((prev) => {
          const idx = prev.findLastIndex(
            (op) => op.toolName === toolName && op.status === "pending"
          );
          if (idx === -1) {
            // No matching pending op — add a new completed one
            return [
              ...prev,
              {
                id: `op-${opCounterRef.current++}`,
                toolName,
                summary,
                chunkIds,
                revealedCount: chunkIds.length,
                status: "complete" as const,
                vectorIndex: null,
              },
            ];
          }
          const updated = [...prev];
          updated[idx] = {
            ...updated[idx],
            summary: summary || updated[idx].summary,
            chunkIds,
            revealedCount: 0,
            status: chunkIds.length > 0 ? "revealing" : "complete",
          };
          return updated;
        });

        if (chunkIds.length > 0) {
          queueRef.current.push({ toolName, summary, chunkIds });

          if (!isRevealingRef.current) {
            processNextBatch();
          }
        }
      }

      if (event.kind === "loop_complete") {
        setDone(true);
        // Mark all remaining operations as complete
        setOperations((prev) =>
          prev.map((op) => (op.status !== "complete" ? { ...op, status: "complete" as const } : op))
        );
      }
    }
  }, [traceEvents, clearTimer, processNextBatch]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearTimer();
    };
  }, [clearTimer]);

  // Replay: rebuild the gridOp for a given operation by scanning the operations list
  const replayOp = useCallback((opId: string) => {
    if (!done) return; // only allow replay after trace completes

    const ops = operations;
    const targetIdx = ops.findIndex((op) => op.id === opId);
    if (targetIdx === -1) return;

    const target = ops[targetIdx];

    if (target.toolName === "vector_search") {
      // Find all consecutive vector_search ops in the same group
      // (i.e. going backwards from target, find first non-vector-search, then collect forward)
      let groupStart = targetIdx;
      while (groupStart > 0 && ops[groupStart - 1].toolName === "vector_search") {
        groupStart--;
      }
      let groupEnd = targetIdx;
      while (groupEnd < ops.length - 1 && ops[groupEnd + 1].toolName === "vector_search") {
        groupEnd++;
      }

      const vectorCalls: { label: string; chunkIds: string[] }[] = [];
      for (let i = groupStart; i <= groupEnd; i++) {
        if (ops[i].chunkIds.length > 0) {
          vectorCalls.push({ label: `Q${vectorCalls.length + 1}`, chunkIds: ops[i].chunkIds });
        }
      }

      setGridOp({ opId: target.id, toolName: "vector_search", vectorCalls });
    } else if (target.toolName === "get_neighbors") {
      setGridOp({ opId: target.id, toolName: "get_neighbors", vectorCalls: [], neighborData: { seedDocId: target.summary, neighborDocIds: target.chunkIds } });
    } else if (target.toolName === "search_document" || target.toolName === "get_section") {
      // Find consecutive same-tool ops in the group
      let groupStart = targetIdx;
      while (groupStart > 0 && ops[groupStart - 1].toolName === target.toolName) {
        groupStart--;
      }
      let groupEnd = targetIdx;
      while (groupEnd < ops.length - 1 && ops[groupEnd + 1].toolName === target.toolName) {
        groupEnd++;
      }

      const vectorCalls: { label: string; chunkIds: string[] }[] = [];
      for (let i = groupStart; i <= groupEnd; i++) {
        if (ops[i].chunkIds.length > 0) {
          vectorCalls.push({ label: ops[i].summary || target.toolName, chunkIds: ops[i].chunkIds });
        }
      }

      setGridOp({ opId: target.id, toolName: target.toolName, vectorCalls });
    } else {
      // Other tools — clear grid
      setGridOp({ opId: target.id, toolName: target.toolName, vectorCalls: [] });
    }
  }, [done, operations]);

  return { revealedIds, activeIds, activeOp, operations, gridOp, done, replayOp };
}
