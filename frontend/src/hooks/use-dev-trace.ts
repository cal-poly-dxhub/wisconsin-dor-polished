'use client';
import { useMemo } from 'react';

/**
 * Returns true when the agent trace UI should reveal dev-mode detail
 * (raw tool inputs, result summaries, latencies, token usage).
 *
 * Enabled via:
 *   - URL param `?debug=1` (shareable debug URLs), OR
 *   - localStorage `wisco:devTrace=1` (sticky per-developer setting).
 */
export function useDevTrace(): boolean {
  return useMemo(() => {
    if (process.env.NODE_ENV === 'development') return true;
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    if (params.get('debug') === '1') return true;
    try {
      return localStorage.getItem('wisco:devTrace') === '1';
    } catch {
      return false;
    }
  }, []);
}
