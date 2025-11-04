'use client';

import { useAuth } from '@/contexts/auth-context';
import { Button } from '@/components/ui/button';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';

export function UserMenu() {
  const { signOut, session } = useAuth();
  const router = useRouter();

  const handleSignOut = async () => {
    try {
      await signOut();
      toast.success('Signed out successfully');
      router.push('/login');
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Failed to sign out';
      toast.error(errorMessage);
    }
  };

  if (!session) {
    return null;
  }

  const email = session.getIdToken().payload.email as string | undefined;

  return (
    <div className="flex items-center gap-4">
      {email && <span className="text-sm text-muted-foreground">{email}</span>}
      <Button variant="outline" size="sm" onClick={handleSignOut}>
        Sign out
      </Button>
    </div>
  );
}
