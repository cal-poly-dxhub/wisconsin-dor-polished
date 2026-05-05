'use client';

import { ThemeToggle } from './theme-toggle';
import { UserMenu } from './auth/user-menu';
import { useBreakpoint } from '@/hooks/use-breakpoint';

export function ThemeToggleContainer() {
  const breakpoint = useBreakpoint();
  const positionClass =
    breakpoint === 'wide'
      ? 'fixed top-4 right-4 gap-4 p-0'
      : 'relative justify-end gap-2 px-4 pt-4';

  return (
    <div className={`z-50 flex items-center ${positionClass}`}>
      <UserMenu />
      <ThemeToggle />
    </div>
  );
}
