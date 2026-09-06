'use client';

import { useCallback, useRef, useState } from 'react';
import { http } from '@/lib/http';

export interface TraceEvent {
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

export interface ActivitySummary {
  queryId: string;
  sessionId: string;
  query: string;
  timestamp: string;
  thumbUp: boolean | null;
  // First-class rating scalar. `thumbUp` collapses mid→false, so `rating` is
  // the only field that distinguishes a "mixed" rating in the list. Null on
  // rows that predate the scalar and haven't been backfilled.
  rating: 'up' | 'mid' | 'down' | null;
  feedback: string | null;
  email: string | null;
}

// Structured feedback captured by the chat feedback modal, stored on the
// ChatHistoryTable row under `richFeedback` (camelCase, mirrors the backend
// RichFeedback model + the frontend FeedbackDraft). Only surfaced via the
// per-query detail endpoint, not the list GSI. All fields optional/nullable
// because older rows predate the richer schema.
export interface RichSubsectionFeedback {
  answer: 'yes' | 'no' | null;
  comment?: string;
}

export interface RichSourceNote {
  id: string;
  sourceId?: string;
  citedFully?: string; // '' | 'yes' | 'no'
  missedDetail?: string;
  comment?: string;
}

export interface RichAnnotation {
  id: string;
  startOffset?: number;
  endOffset?: number;
  quote?: string;
  comment?: string;
}

export interface RichFeedback {
  rating: 'up' | 'mid' | 'down' | null;
  positiveComment?: string;
  response?: Record<string, RichSubsectionFeedback>;
  sourcesOk?: 'yes' | 'no' | null;
  sourceNotes?: RichSourceNote[];
  linksWork?: 'yes' | 'no' | null;
  brokenLinkIds?: string[];
  brokenLinksReason?: string;
  annotations?: RichAnnotation[];
  speedTimely?: 'yes' | 'no' | null;
  speedComment?: string;
}

export interface ActivityItem extends ActivitySummary {
  answer: string;
  trace: TraceEvent[] | null;
  richFeedback?: RichFeedback | null;
  feedbackSubmittedAt?: string | null;
}

export type FeedbackFilter = 'all' | 'up' | 'mid' | 'down' | 'rated' | 'unrated';

export interface ActivityFilters {
  after?: string;
  before?: string;
  feedback?: FeedbackFilter;
  limit?: number;
  cacheKey?: string;
}

interface PageResponse {
  items: ActivitySummary[];
  count: number;
  nextCursor: string | null;
}

interface CacheEntry {
  items: ActivitySummary[];
  nextCursor: string | null;
  fetchedAt: number;
  filterKey: string;
}

type CacheStore = Record<string, CacheEntry>;

const DEFAULT_PAGE_SIZE = 50;
const CACHE_TTL_MS = 5 * 60 * 1000;
const STORAGE_KEY = 'admin-activity-summary-cache-v2';
const MAX_CACHE_ENTRIES = 8;

function loadCache(): CacheStore {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as CacheStore;
    const now = Date.now();
    return Object.fromEntries(
      // Drop stale entries AND any malformed entry whose `items` isn't an array
      // (e.g. one poisoned by a past error response) so it can't crash reads.
      Object.entries(parsed).filter(
        ([, entry]) =>
          entry && Array.isArray(entry.items) && now - entry.fetchedAt < CACHE_TTL_MS
      )
    );
  } catch {
    return {};
  }
}

function saveCache(cache: CacheStore) {
  try {
    const newestEntries = Object.entries(cache)
      .sort(([, left], [, right]) => right.fetchedAt - left.fetchedAt)
      .slice(0, MAX_CACHE_ENTRIES);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(newestEntries)));
  } catch {
    // Storage may be disabled or full. The in-memory cache remains usable.
  }
}

let moduleCache: CacheStore = loadCache();

function buildFilterKey(filters: ActivityFilters): string {
  if (filters.cacheKey) return filters.cacheKey;
  return JSON.stringify({
    after: filters.after,
    before: filters.before,
    feedback: filters.feedback ?? 'all',
  });
}

export function useActivityData() {
  const [items, setItems] = useState<ActivitySummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [totalLoaded, setTotalLoaded] = useState(0);
  const [lastFetched, setLastFetched] = useState<number | null>(null);
  const currentFiltersRef = useRef<ActivityFilters>({});
  const requestGenerationRef = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchPage = useCallback(async (
    filters: ActivityFilters = {},
    cursor?: string | null,
    signal?: AbortSignal
  ) => {
    const params = new URLSearchParams();
    params.set('limit', String(filters.limit ?? DEFAULT_PAGE_SIZE));
    if (cursor) params.set('cursor', cursor);
    if (filters.after) params.set('after', filters.after);
    if (filters.before) params.set('before', filters.before);
    if (filters.feedback && filters.feedback !== 'all') {
      params.set('feedback', filters.feedback);
    }

    const response = await http.get(`admin/activity?${params.toString()}`, { signal }).json<{
      statusCode?: number;
      body?: string;
      items?: ActivitySummary[];
      count?: number;
      nextCursor?: string | null;
    }>();

    const page = (response.statusCode && response.body
      ? JSON.parse(response.body)
      : response) as PageResponse;

    // A wrapped error envelope (statusCode+body) parses to an object without an
    // `items` array. Treat that as a failure so it surfaces in the catch block
    // instead of being cached as an empty/undefined page and crashing readers.
    if (!page || !Array.isArray(page.items)) {
      throw new Error('Activity response did not contain an items array');
    }
    return page;
  }, []);

  const applyResults = useCallback((
    allItems: ActivitySummary[],
    cursor: string | null,
    filterKey: string
  ) => {
    const fetchedAt = Date.now();
    const entry: CacheEntry = {
      items: allItems,
      nextCursor: cursor,
      fetchedAt,
      filterKey,
    };
    moduleCache = { ...moduleCache, [filterKey]: entry };
    saveCache(moduleCache);
    setItems(allItems);
    setNextCursor(cursor);
    setTotalLoaded(allItems.length);
    setLastFetched(fetchedAt);
  }, []);

  const loadFirstPage = useCallback(async (filters: ActivityFilters = {}) => {
    currentFiltersRef.current = filters;
    const filterKey = buildFilterKey(filters);
    const generation = ++requestGenerationRef.current;

    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();

    const cached = moduleCache[filterKey];
    if (cached && Array.isArray(cached.items) && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
      setItems(cached.items);
      setNextCursor(cached.nextCursor);
      setTotalLoaded(cached.items.length);
      setLastFetched(cached.fetchedAt);
      setLoading(false);
      return;
    }

    setItems([]);
    setNextCursor(null);
    setTotalLoaded(0);
    setLastFetched(null);
    setLoading(true);

    try {
      const data = await fetchPage(filters, null, abortControllerRef.current.signal);
      if (generation === requestGenerationRef.current && data) {
        applyResults(data.items, data.nextCursor, filterKey);
      }
    } catch (error) {
      if (generation === requestGenerationRef.current && !abortControllerRef.current.signal.aborted) {
        console.error('Failed to fetch activity data:', error);
      }
    } finally {
      if (generation === requestGenerationRef.current) setLoading(false);
    }
  }, [applyResults, fetchPage]);

  const loadNextPage = useCallback(async () => {
    if (!nextCursor || loading) return;

    const generation = ++requestGenerationRef.current;
    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();
    setLoading(true);

    try {
      const data = await fetchPage(
        currentFiltersRef.current,
        nextCursor,
        abortControllerRef.current.signal
      );
      if (generation === requestGenerationRef.current && data) {
        const allItems = [...items, ...data.items];
        applyResults(allItems, data.nextCursor, buildFilterKey(currentFiltersRef.current));
      }
    } catch (error) {
      if (generation === requestGenerationRef.current && !abortControllerRef.current.signal.aborted) {
        console.error('Failed to load next activity page:', error);
      }
    } finally {
      if (generation === requestGenerationRef.current) setLoading(false);
    }
  }, [applyResults, fetchPage, items, loading, nextCursor]);

  const refresh = useCallback(async () => {
    const filterKey = buildFilterKey(currentFiltersRef.current);
    const { [filterKey]: _removed, ...remainingCache } = moduleCache;
    void _removed;
    moduleCache = remainingCache;
    saveCache(moduleCache);
    await loadFirstPage(currentFiltersRef.current);
  }, [loadFirstPage]);

  return {
    items,
    loading,
    lastFetched,
    hasMore: nextCursor !== null,
    totalLoaded,
    nextCursor,
    loadFirstPage,
    loadNextPage,
    refresh,
  };
}
