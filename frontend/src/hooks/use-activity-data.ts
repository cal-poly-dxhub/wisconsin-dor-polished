'use client';

import { useState, useCallback, useRef } from 'react';
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

export interface ActivityItem {
  queryId: string;
  sessionId: string;
  query: string;
  answer: string;
  timestamp: string;
  thumbUp: boolean | null;
  feedback: string | null;
  trace: TraceEvent[] | null;
  email: string | null;
}

export type FeedbackFilter = 'all' | 'up' | 'down' | 'rated' | 'unrated';

export interface ActivityFilters {
  after?: string;
  before?: string;
  feedback?: FeedbackFilter;
  limit?: number;
  cacheKey?: string;
}

interface PageResponse {
  items: ActivityItem[];
  count: number;
  nextCursor: string | null;
}

interface CacheEntry {
  items: ActivityItem[];
  nextCursor: string | null;
  fetchedAt: number;
  filterKey: string;
}

const DEFAULT_PAGE_SIZE = 50;
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const STORAGE_KEY = 'admin-activity-cache';

function loadCache(): CacheEntry | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw) as CacheEntry;
    if (Date.now() - entry.fetchedAt > CACHE_TTL_MS) return null;
    return entry;
  } catch {
    return null;
  }
}

function saveCache(entry: CacheEntry) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
  } catch {
    // quota exceeded — ignore
  }
}

let moduleCache: CacheEntry | null = loadCache();

function buildFilterKey(filters: ActivityFilters): string {
  if (filters.cacheKey) return filters.cacheKey;
  return JSON.stringify({ after: filters.after, before: filters.before });
}

export function useActivityData() {
  const [items, setItems] = useState<ActivityItem[]>(moduleCache?.items ?? []);
  const [loading, setLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(moduleCache?.nextCursor ?? null);
  const [hasMore, setHasMore] = useState(moduleCache?.nextCursor !== null);
  const [totalLoaded, setTotalLoaded] = useState(moduleCache?.items.length ?? 0);
  const [lastFetched, setLastFetched] = useState<number | null>(moduleCache?.fetchedAt ?? null);
  const currentFiltersRef = useRef<ActivityFilters>({});
  const currentAutoLoadAllRef = useRef(false);

  const fetchPage = useCallback(async (filters: ActivityFilters = {}, cursor?: string | null) => {
    const params = new URLSearchParams();
    params.set('limit', String(filters.limit ?? DEFAULT_PAGE_SIZE));
    if (cursor) params.set('cursor', cursor);
    if (filters.after) params.set('after', filters.after);
    if (filters.before) params.set('before', filters.before);
    if (filters.feedback && filters.feedback !== 'all') params.set('feedback', filters.feedback);

    const response = await http.get(`admin/activity?${params.toString()}`).json<{
      statusCode?: number;
      body?: string;
      items?: ActivityItem[];
      count?: number;
      nextCursor?: string | null;
    }>();

    let data: PageResponse;
    if (response.statusCode && response.body) {
      data = JSON.parse(response.body);
    } else {
      data = response as unknown as PageResponse;
    }

    return data;
  }, []);

  const applyResults = useCallback((allItems: ActivityItem[], cursor: string | null, filterKey: string) => {
    const entry: CacheEntry = { items: allItems, nextCursor: cursor, fetchedAt: Date.now(), filterKey };
    moduleCache = entry;
    saveCache(entry);
    setItems(allItems);
    setNextCursor(cursor);
    setHasMore(cursor !== null);
    setTotalLoaded(allItems.length);
    setLastFetched(Date.now());
  }, []);

  const loadFirstPage = useCallback(async (filters: ActivityFilters = {}, autoLoadAll = false) => {
    currentFiltersRef.current = filters;
    currentAutoLoadAllRef.current = autoLoadAll;

    const filterKey = buildFilterKey(filters);

    if (moduleCache && moduleCache.filterKey === filterKey && Date.now() - moduleCache.fetchedAt < CACHE_TTL_MS) {
      setItems(moduleCache.items);
      setNextCursor(moduleCache.nextCursor);
      setHasMore(moduleCache.nextCursor !== null);
      setTotalLoaded(moduleCache.items.length);
      setLastFetched(moduleCache.fetchedAt);
      return;
    }

    setLoading(true);
    try {
      const data = await fetchPage(filters, null);
      if (!data) return;

      let allItems = data.items;
      let cursor = data.nextCursor;

      if (autoLoadAll) {
        while (cursor) {
          const next = await fetchPage(filters, cursor);
          if (!next) break;
          allItems = [...allItems, ...next.items];
          cursor = next.nextCursor;
        }
      }

      applyResults(allItems, cursor, filterKey);
    } catch (err) {
      console.error('Failed to fetch activity data:', err);
    } finally {
      setLoading(false);
    }
  }, [fetchPage, applyResults]);

  const loadNextPage = useCallback(async () => {
    if (!nextCursor || loading) return;
    setLoading(true);
    try {
      const data = await fetchPage(currentFiltersRef.current, nextCursor);
      if (data) {
        const allItems = [...items, ...data.items];
        const filterKey = buildFilterKey(currentFiltersRef.current);
        applyResults(allItems, data.nextCursor, filterKey);
      }
    } catch (err) {
      console.error('Failed to load next page:', err);
    } finally {
      setLoading(false);
    }
  }, [nextCursor, loading, items, fetchPage, applyResults]);

  const refresh = useCallback(async () => {
    moduleCache = null;
    try { sessionStorage.removeItem(STORAGE_KEY); } catch {}
    await loadFirstPage(currentFiltersRef.current, currentAutoLoadAllRef.current);
  }, [loadFirstPage]);

  const getItemById = useCallback(
    (queryId: string) => items.find(item => item.queryId === queryId) ?? null,
    [items]
  );

  return {
    items,
    loading,
    lastFetched,
    hasMore,
    totalLoaded,
    nextCursor,
    loadFirstPage,
    loadNextPage,
    refresh,
    getItemById,
  };
}
