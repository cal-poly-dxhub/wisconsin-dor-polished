'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/auth-context';

export function ProtectedRoute({
  children,
  requireAdmin = false,
}: {
  children: React.ReactNode;
  /** Also require membership in the `Admins` Cognito group (admin pages). */
  requireAdmin?: boolean;
}) {
  const { isAuthenticated, isLoading, isAdmin } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-lg text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  if (requireAdmin && !isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="max-w-md rounded-lg border border-border bg-card p-6 text-center">
          <p className="text-base font-semibold text-foreground">Admin access required</p>
          <p className="mt-2 text-sm text-muted-foreground">
            This area is limited to members of the administrators group. If you need access,
            ask the project team to add your account.
          </p>
          <Link href="/" className="mt-4 inline-block text-sm text-primary underline-offset-4 hover:underline">
            Back to chat
          </Link>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
