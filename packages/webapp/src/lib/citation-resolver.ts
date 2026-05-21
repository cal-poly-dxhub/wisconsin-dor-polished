import { getIdToken } from './auth';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL!;
if (!API_BASE_URL) throw new Error('NEXT_PUBLIC_API_BASE_URL is not set');

/**
 * Build a one-shot URL that the user navigates to in a new tab. The
 * resolver Lambda validates the Cognito JWT (carried in ?token= because
 * window.open cannot attach a custom Authorization header), HEAD-checks
 * the s3 key, and 302-redirects to a 15-minute presigned URL with an
 * optional #page=N fragment.
 *
 * Returns null when no JWT is available (signed out). Caller should
 * close the popup it opened synchronously.
 */
export async function buildResolverUrl(
  s3Key: string,
  page?: number
): Promise<string | null> {
  const token = await getIdToken();
  if (!token) return null;

  const params = new URLSearchParams({ s3Key, token });
  if (page && page > 0) {
    params.set('page', String(page));
  }
  const base = API_BASE_URL.replace(/\/+$/, '');
  return `${base}/citation?${params.toString()}`;
}
