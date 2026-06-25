'use client';

import { useState, useCallback } from 'react';
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
}

interface PageResponse {
  items: ActivityItem[];
  count: number;
  nextCursor: string | null;
}

const DEFAULT_PAGE_SIZE = 50;

export function useActivityData() {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [totalLoaded, setTotalLoaded] = useState(0);
  const [lastFetched, setLastFetched] = useState<number | null>(null);
  const [currentFilters, setCurrentFilters] = useState<ActivityFilters>({});

  const fetchPage = useCallback(async (filters: ActivityFilters = {}, cursor?: string | null) => {
    setLoading(true);
    try {
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
    } catch (err) {
      console.error('Failed to fetch activity data:', err);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFirstPage = useCallback(async (filters: ActivityFilters = {}) => {
    setCurrentFilters(filters);
    const data = await fetchPage(filters, null);
    if (data) {
      setItems(data.items);
      setNextCursor(data.nextCursor);
      setHasMore(data.nextCursor !== null);
      setTotalLoaded(data.items.length);
      setLastFetched(Date.now());
    }
  }, [fetchPage]);

  const loadNextPage = useCallback(async () => {
    if (!nextCursor || loading) return;
    const data = await fetchPage(currentFilters, nextCursor);
    if (data) {
      setItems(prev => [...prev, ...data.items]);
      setNextCursor(data.nextCursor);
      setHasMore(data.nextCursor !== null);
      setTotalLoaded(prev => prev + data.items.length);
    }
  }, [nextCursor, loading, currentFilters, fetchPage]);

  const refresh = useCallback(async () => {
    await loadFirstPage(currentFilters);
  }, [loadFirstPage, currentFilters]);

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
