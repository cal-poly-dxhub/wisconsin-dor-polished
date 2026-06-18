'use client';

import { ThemeToggle } from './theme-toggle';
import { UserMenu } from './auth/user-menu';

export function ThemeToggleContainer() {
  return (
    <div className="z-50 flex items-center relative justify-end gap-2 px-4 pt-4 xl:fixed xl:top-4 xl:right-4 xl:gap-4 xl:p-0">
      <UserMenu />
      <ThemeToggle />
    </div>
  );
}
