'use client';

import { useState, useEffect, useCallback } from 'react';
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
}

interface CachedData {
  items: ActivityItem[];
  fetchedAt: number;
}

const CACHE_KEY = 'admin-activity-cache';
const CACHE_TTL_MS = 60 * 60 * 1000;

function getCachedData(): CachedData | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as CachedData;
  } catch {
    return null;
  }
}

function setCachedData(items: ActivityItem[]) {
  const data: CachedData = { items, fetchedAt: Date.now() };
  localStorage.setItem(CACHE_KEY, JSON.stringify(data));
}

function isCacheStale(cached: CachedData): boolean {
  return Date.now() - cached.fetchedAt > CACHE_TTL_MS;
}

export function useActivityData() {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastFetched, setLastFetched] = useState<number | null>(null);

  const fetchData = useCallback(async (force = false) => {
    if (!force) {
      const cached = getCachedData();
      if (cached && !isCacheStale(cached)) {
        setItems(cached.items);
        setLastFetched(cached.fetchedAt);
        setLoading(false);
        return;
      }
    }

    setLoading(true);
    try {
      const response = await http.get('admin/activity').json<{
        statusCode?: number;
        body?: string;
        items?: ActivityItem[];
      }>();

      let data: { items: ActivityItem[] };
      if (response.statusCode && response.body) {
        data = JSON.parse(response.body);
      } else {
        data = response as unknown as { items: ActivityItem[] };
      }

      setItems(data.items);
      setCachedData(data.items);
      setLastFetched(Date.now());
    } catch (err) {
      console.error('Failed to fetch activity data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const getItemById = useCallback(
    (queryId: string) => items.find(item => item.queryId === queryId) ?? null,
    [items]
  );

  return { items, loading, lastFetched, fetchData, getItemById };
}
