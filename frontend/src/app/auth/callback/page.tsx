'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { completeSsoSignIn } from '@/lib/sso';

/** Return leg of single sign-on: swap the code for tokens, then load the app. */
function Callback() {
  const params = useSearchParams();
  const code = params.get('code');
  const state = params.get('state');
  const responseError =
    params.get('error_description') ||
    params.get('error') ||
    (!code || !state ? 'The sign-in response was incomplete.' : null);
  const [exchangeError, setExchangeError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (responseError || !code || !state || started.current) return;
    started.current = true;
    completeSsoSignIn(code, state)
      // Full load so AuthProvider picks the new session up from storage.
      .then(() => window.location.replace('/'))
      .catch((e: unknown) => setExchangeError(e instanceof Error ? e.message : 'Sign-in failed.'));
  }, [responseError, code, state]);

  const error = responseError ?? exchangeError;
  if (!error) {
    return <p className="text-sm text-muted-foreground">Signing you in…</p>;
  }
  return (
    <div className="max-w-md text-center">
      <p className="text-base font-semibold text-foreground">Sign-in didn&apos;t complete</p>
      <p className="mt-2 text-sm text-muted-foreground">{error}</p>
      <Link href="/login" className="mt-4 inline-block text-sm text-primary underline-offset-4 hover:underline">
        Back to sign in
      </Link>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Suspense fallback={null}>
        <Callback />
      </Suspense>
    </div>
  );
}
