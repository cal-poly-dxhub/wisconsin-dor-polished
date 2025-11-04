'use client';

import { ThemeToggle } from './theme-toggle';
import { UserMenu } from './auth/user-menu';

export function ThemeToggleContainer() {
  return (
    <div className="fixed top-4 right-4 z-50 flex items-center gap-4">
      <UserMenu />
      <ThemeToggle />
    </div>
  );
}
