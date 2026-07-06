'use client';

/* eslint-disable react-hooks/purity */
import { useEffect, useRef, useState } from 'react';
import type { TraceEvent } from '../hooks/use-visualizer-socket';

interface WaterfallProps {
  events: TraceEvent[];
  currentQuery: string | null;
}

const TOOL_LABELS: Record<string, string> = {
  vector_search: 'Vector Search',
  get_neighbors: 'Get Neighbors',
  search_document: 'Search Document',
  list_sections: 'List Sections',
  get_section: 'Get Section',
  get_authority_chain: 'Authority Chain',
  fetch_case_opinion: 'Fetch Case Opinion',
  prepare_answer: 'Prepare Answer',
  faq_search: 'FAQ Search',
  refine_query: 'Refine Query',
};

function formatToolLabel(toolName: string): string {
  return TOOL_LABELS[toolName] || toolName;
}

function getNumber(obj: Record<string, unknown>, key: string): number | undefined {
  const value = obj[key];
  return typeof value === 'number' ? value : undefined;
}

function getString(obj: Record<string, unknown>, key: string): string {
  const value = obj[key];
  return typeof value === 'string' ? value : '';
}

function buildResultSummary(payload: Record<string, unknown>): string {
  const meta =
    payload.metadata && typeof payload.metadata === 'object'
      ? (payload.metadata as Record<string, unknown>)
      : {};
  const parts: string[] = [];

  const chunkCount = getNumber(meta, 'chunkCount');
  if (chunkCount != null) parts.push(`${chunkCount} chunks`);
  const neighborCount = getNumber(meta, 'neighborCount');
  if (neighborCount != null) parts.push(`${neighborCount} neighbors`);
  const faqCount = getNumber(meta, 'faqCount');
  if (faqCount != null) parts.push(`${faqCount} FAQs`);
  const citedDocCount = getNumber(meta, 'citedDocCount');
  if (citedDocCount != null) parts.push(`${citedDocCount} cited`);
  const latencyMs = getNumber(meta, 'latencyMs');
  if (latencyMs != null) parts.push(`${latencyMs}ms`);

  if (parts.length === 0) {
    const summary = getString(payload, 'summary');
    if (summary) return summary;
  }

  return parts.join(' · ');
}

function buildCompleteSummary(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  const citedDocCount = getNumber(payload, 'citedDocCount');
  if (citedDocCount != null) parts.push(`${citedDocCount} cited docs`);
  const elapsedMs = getNumber(payload, 'elapsedMs');
  if (elapsedMs != null) parts.push(`${elapsedMs}ms`);
  return parts.join(' · ');
}

interface WaterfallItem {
  id: string;
  tool: string;
  label: string;
  callSummary: string;
  resultSummary: string;
  status: 'pending' | 'done';
}

const MIN_ACTIVE_MS = 500;

export function Waterfall({ events, currentQuery }: WaterfallProps) {
  // Track when each item transitioned to "done" so we can hold the active state
  const doneAtMap = useRef<Map<string, number>>(new Map());
  // Track which items have "appeared" (to animate from opacity-0 → opacity-100)
  const appearedSet = useRef<Set<string>>(new Set());
  const [, forceRender] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const items: WaterfallItem[] = [];
  let loopComplete: { summary: string } | null = null;

  // Use seq as the unique key for pairing tool_call → tool_result
  // tool_result events reference the same turn + toolName
  const pendingMap = new Map<string, number>(); // key → index in items

  for (const event of events) {
    const payload = event.payload;

    if (event.kind === 'tool_call') {
      const tool = getString(payload, 'toolName') || 'unknown';
      // Unique key: turn + tool + seq (seq ensures uniqueness for parallel calls)
      const key = `${event.turn}-${tool}-${event.seq}`;
      pendingMap.set(key, items.length);
      items.push({
        id: key,
        tool,
        label: formatToolLabel(tool),
        callSummary: getString(payload, 'summary'),
        resultSummary: '',
        status: 'pending',
      });
    } else if (event.kind === 'tool_result') {
      const tool = getString(payload, 'toolName') || 'unknown';
      // Find the matching pending tool_call — match by turn + tool, take the first unresolved
      let matched = false;
      for (const [key, idx] of pendingMap.entries()) {
        if (key.startsWith(`${event.turn}-${tool}-`) && items[idx].status === 'pending') {
          items[idx].status = 'done';
          items[idx].resultSummary = buildResultSummary(payload);
          // Record the time this item became done (only once)
          if (!doneAtMap.current.has(key)) {
            doneAtMap.current.set(key, Date.now());
          }
          matched = true;
          break;
        }
      }
      if (!matched) {
        const key = `result-${event.seq}`;
        if (!doneAtMap.current.has(key)) {
          doneAtMap.current.set(key, Date.now());
        }
        items.push({
          id: key,
          tool,
          label: formatToolLabel(tool),
          callSummary: '',
          resultSummary: buildResultSummary(payload),
          status: 'done',
        });
      }
    } else if (event.kind === 'loop_complete') {
      loopComplete = { summary: buildCompleteSummary(payload) };
      // Mark any remaining pending items as done
      for (const item of items) {
        if (item.status === 'pending') {
          item.status = 'done';
          if (!doneAtMap.current.has(item.id)) {
            doneAtMap.current.set(item.id, Date.now());
          }
        }
      }
    }
  }

  // Determine which items are still visually active (within MIN_ACTIVE_MS of completing)
  const now = Date.now();
  let hasRecentlyDone = false;

  const isVisuallyActive = (item: WaterfallItem): boolean => {
    if (item.status === 'pending') return true;
    const doneAt = doneAtMap.current.get(item.id);
    if (doneAt && now - doneAt < MIN_ACTIVE_MS) {
      hasRecentlyDone = true;
      return true;
    }
    return false;
  };

  // Check each item to set hasRecentlyDone flag
  const visuallyActiveSet = new Set<string>();
  for (const item of items) {
    if (isVisuallyActive(item)) {
      visuallyActiveSet.add(item.id);
    }
  }

  // Schedule a re-render after MIN_ACTIVE_MS to transition recently-done items
  useEffect(() => {
    if (hasRecentlyDone) {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        forceRender((n) => n + 1);
      }, MIN_ACTIVE_MS);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  });

  // Clean up maps when events reset (new query)
  useEffect(() => {
    if (events.length === 0) {
      doneAtMap.current.clear();
      appearedSet.current.clear();
    }
  }, [events.length]);

  // Mark new items as "appeared" after a micro-delay so the browser paints opacity-0 first
  useEffect(() => {
    const newIds = items.filter((i) => !appearedSet.current.has(i.id)).map((i) => i.id);
    if (newIds.length === 0) return;
    const raf = requestAnimationFrame(() => {
      for (const id of newIds) {
        appearedSet.current.add(id);
      }
      forceRender((n) => n + 1);
    });
    return () => cancelAnimationFrame(raf);
  });

  const activeIdx = items.findLastIndex((i) => visuallyActiveSet.has(i.id));

  return (
    <div className="space-y-0.5">
      {items.length === 0 && !currentQuery && (
        <p className="text-xs text-muted-foreground/40">Waiting for query...</p>
      )}
      {items.map((item, i) => {
        const hasAppeared = appearedSet.current.has(item.id);
        const isActive = i === activeIdx || visuallyActiveSet.has(item.id);
        const opacity = !hasAppeared
          ? 'opacity-0'
          : isActive
            ? 'opacity-100'
            : item.status === 'done'
              ? 'opacity-50'
              : 'opacity-70';
        return (
          <div
            key={item.id}
            className={`py-1.5 transition-opacity duration-400 ease-out ${opacity}`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                  isActive
                    ? 'bg-foreground animate-pulse'
                    : item.status === 'done'
                      ? 'bg-foreground/40'
                      : 'bg-foreground/20'
                }`}
              />
              <span
                className={`font-medium ${
                  isActive ? 'text-xs text-foreground' : 'text-xs text-foreground/60'
                }`}
              >
                {item.label}
              </span>
            </div>
            {(item.resultSummary || item.callSummary) && (
              <p className="ml-3.5 text-[11px] text-muted-foreground/50 leading-tight mt-0.5">
                {item.resultSummary || item.callSummary}
              </p>
            )}
          </div>
        );
      })}
      {loopComplete && (
        <div className="pt-2 mt-2 border-t border-border/20">
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-foreground/60 flex-shrink-0" />
            <span className="text-xs font-medium text-foreground/70">Complete</span>
          </div>
          {loopComplete.summary && (
            <p className="ml-3.5 text-[11px] text-muted-foreground/50 leading-tight mt-0.5">
              {loopComplete.summary}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
