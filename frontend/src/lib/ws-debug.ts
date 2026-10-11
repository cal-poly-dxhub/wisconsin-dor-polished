/**
 * Per-frame WebSocket timing and receipt logs. They are noisy (one line per
 * token fragment), so they only print in development builds.
 */
export function wsDebug(...args: unknown[]): void {
  if (process.env.NODE_ENV === 'development') console.log(...args);
}
