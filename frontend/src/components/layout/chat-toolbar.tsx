'use client';

import { ThemeToggle } from '@/components/theme-toggle';
import { UserMenu } from '@/components/auth/user-menu';

export function ChatToolbar() {
  return (
    <div className="flex items-center justify-between border-b border-border bg-card px-6 py-3">
      {/* Left: Logo/Title */}
      <div className="flex items-center gap-2">
        <h1 className="text-sm font-medium text-foreground">
          Wisconsin DOR
        </h1>
      </div>

      {/* Right: User Menu & Theme Toggle */}
      <div className="flex items-center gap-3">
        <UserMenu />
        <ThemeToggle />
      </div>
    </div>
  );
}
