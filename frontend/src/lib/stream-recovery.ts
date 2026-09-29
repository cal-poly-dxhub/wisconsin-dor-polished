import type { Query, QueryStatus } from '@/stores/types';

/**
 * Recovering an answer whose stream was cut off by a dropped WebSocket
 * (Task 62). The retrieval Lambda keeps running after the socket goes away and
 * persists the full answer to chat history, so the client polls the session
 * history until that answer appears and swaps it in whole.
 */

/** Waiting on the server: retrieval running (`sent`) or answer streaming. */
const IN_FLIGHT: ReadonlySet<QueryStatus> = new Set(['sent', 'streaming']);

export function inFlightQueryIds(queries: Record<string, Query>): string[] {
  return Object.values(queries)
    .filter(q => IN_FLIGHT.has(q.status) && !q.queryId.startsWith('pending-'))
    .map(q => q.queryId);
}

export function isInFlight(query: Query | undefined): boolean {
  return !!query && IN_FLIGHT.has(query.status);
}

export function persistedAnswer(
  history: { messages: { queryId: string; answer: string }[] },
  queryId: string,
): string | null {
  const hit = history.messages.find(m => m.queryId === queryId);
  return hit && hit.answer ? hit.answer : null;
}

export interface RecoverOptions {
  queryId: string;
  fetchHistory: () => Promise<{ messages: { queryId: string; answer: string }[] }>;
  /** Stop polling once this turns false (e.g. a late "stop" finished it). */
  stillWaiting: () => boolean;
  intervalMs?: number;
  timeoutMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

/** Poll until the persisted answer appears; null on timeout or when no longer waiting. */
export async function recoverAnswer({
  queryId,
  fetchHistory,
  stillWaiting,
  intervalMs = 4000,
  // Retrieval + streaming runs well under this; the Lambda times out at 15 min
  // but a real answer lands within ~2.
  timeoutMs = 4 * 60 * 1000,
  sleep = ms => new Promise(r => setTimeout(r, ms)),
}: RecoverOptions): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!stillWaiting()) return null;
    try {
      const answer = persistedAnswer(await fetchHistory(), queryId);
      if (answer) return answer;
    } catch {
      // Network still down; keep trying until the deadline.
    }
    await sleep(intervalMs);
  }
  return null;
}
